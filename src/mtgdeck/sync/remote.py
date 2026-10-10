"""Talking to the sync server over HTTPS – the device side of ``server.py``.

The one place outside ``http.py`` with its own HTTP client: the sync runs in a worker thread under file locks and
must be neither cached nor throttled, so it uses a short-lived blocking ``httpx.Client`` per run.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..http import USER_AGENT
from .store import SyncConflict, decode_b64, encode


class RemoteError(RuntimeError):
    """The server could not be used; ``str()`` is a German sentence for the user."""

    def __init__(self, text: str, status: int = 0):
        super().__init__(text)
        self.status = status


class AuthError(RemoteError):
    """The server does not know this device (any more) – it was removed, or the server was replaced."""


def normalize_url(text: str) -> str:
    """``sync.example.de`` / ``https://sync.example.de/koppeln#…`` → ``https://sync.example.de``."""
    text = (text or "").strip().split("#")[0].strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    text = re.sub(r"/(koppeln|api)(/.*)?$", "", text.rstrip("/"))
    return text.rstrip("/")


def parse_link(text: str) -> tuple[str, str]:
    """A pairing link ``https://…/koppeln#ABCD-EFGH`` → (server address, code); a bare code → ("", code)."""
    text = (text or "").strip()
    if "#" in text:
        url, _, code = text.partition("#")
        return normalize_url(url), code.strip()
    if re.fullmatch(r"[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}", text):
        return "", text
    return normalize_url(text), ""


def _doc_in(doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, "data": decode_b64(doc.get("data"))}


class HttpTransport:
    def __init__(self, url: str, token: str = "", *, timeout: float = 60.0, client: httpx.Client | None = None):
        self.url = normalize_url(url)
        self.token = token
        self._own = client is None
        self._client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    def close(self) -> None:
        if self._own:
            self._client.close()

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _call(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
              conflict_ok: bool = False) -> httpx.Response:  # fmt: skip
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self._client.request(method, self.url + path, json=json, params=params, headers=headers)
        except httpx.TimeoutException:
            raise RemoteError("Der Sync-Server antwortet nicht (Zeitüberschreitung) – später noch einmal versuchen.") from None
        except httpx.HTTPError:
            raise RemoteError("Der Sync-Server ist nicht erreichbar – Internetverbindung und Adresse prüfen.") from None
        if r.status_code == 409 and conflict_ok:
            return r
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            text = detail if isinstance(detail, str) else f"Der Sync-Server meldet Fehler {r.status_code}."
            raise (AuthError if r.status_code == 401 else RemoteError)(text, r.status_code)
        return r

    def health(self) -> dict[str, Any]:
        try:
            data = self._call("GET", "/api/health").json()
        except ValueError:
            data = {}
        if not isinstance(data, dict) or data.get("service") != "mtgdeck-sync":
            raise RemoteError("Unter dieser Adresse läuft kein Sync-Server des MTG-Deckbuilders.")
        return data

    def changes(self, since: int) -> dict[str, Any]:
        page = self._call("GET", "/api/sync/changes", params={"since": since}).json()
        return {**page, "docs": [_doc_in(d) for d in page["docs"]]}

    def put(self, doc: dict[str, Any]) -> dict[str, Any]:
        body = {"path": doc["path"], "kind": doc["kind"], "data": encode(doc.get("data")),
                "base_seq": doc.get("base_seq"), "deleted": bool(doc.get("deleted"))}  # fmt: skip
        r = self._call("PUT", "/api/sync/doc", json=body, conflict_ok=True)
        if r.status_code == 409:
            raise SyncConflict(_doc_in(r.json()["current"]))
        return r.json()

    def devices(self) -> list[dict[str, Any]]:
        return self._call("GET", "/api/devices").json()["devices"]

    def revoke(self, device_id: str) -> dict[str, Any]:
        return self._call("DELETE", f"/api/devices/{device_id}").json()

    def presence(self, info: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/api/presence", json={"info": info}).json()

    def pair_start(self) -> dict[str, Any]:
        return self._call("POST", "/api/pair/start").json()

    def claim(self, code: str, name: str, kind: str = "pc") -> dict[str, Any]:
        return self._call("POST", "/api/pair/claim", json={"code": code, "name": name, "kind": kind}).json()

    # questions from the phone („Frag Claude“): this PC takes one, reports progress and sends the answer
    def claim_job(self, wait: float = 0) -> dict[str, Any] | None:
        return self._call("POST", "/api/jobs/claim", params={"wait": wait}).json().get("job")

    def job_progress(self, job_id: str, text: str = "") -> str:
        return self._call("POST", f"/api/jobs/{job_id}/progress", json={"text": text[:200]}).json().get("status", "")

    def job_finish(self, job_id: str, result: dict[str, Any]) -> str:
        return self._call("POST", f"/api/jobs/{job_id}/finish", json=result).json().get("status", "")
