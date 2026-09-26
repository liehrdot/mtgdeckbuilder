"""Shared HTTP layer: one async client, polite per-host rate limiting and a small disk cache.

All data sources used here (Scryfall, EDHREC, Commander Spellbook, Archidekt) are free and
need no API key, but they ask clients to identify themselves and to not hammer the servers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

USER_AGENT = "mtgdeck/0.1 (Claude Code Commander deckbuilder; +https://github.com/liehrdot/mtgdeckbuilder)"

# Minimum seconds between two requests to the same host (or host + path prefix).
_MIN_INTERVAL = {
    # Scryfall: search/named/random/collection 2 req/s, everything else 10 req/s
    "api.scryfall.com/cards/search": 0.5,
    "api.scryfall.com/cards/named": 0.5,
    "api.scryfall.com/cards/collection": 0.5,
    "api.scryfall.com": 0.1,
    "json.edhrec.com": 0.5,
    "backend.commanderspellbook.com": 0.75,  # "80 requests per minute should be a safe rate"
    "archidekt.com": 0.5,
    "cards.scryfall.io": 0.05,
    "cdn.mpcautofill.com": 0.05,  # image CDN, no documented rate limit – stay polite anyway
}
_DEFAULT_INTERVAL = 0.25

CACHE_DIR = Path(os.environ.get("MTG_CACHE_DIR", Path.home() / ".cache" / "mtgdeck"))
DEFAULT_TTL = int(os.environ.get("MTG_CACHE_TTL", 24 * 3600))  # Scryfall recommends caching >= 24h


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, detail: str = ""):
        super().__init__(f"HTTP {status} for {url}: {detail}".strip(": "))
        self.status = status
        self.url = url
        self.detail = detail


_client: httpx.AsyncClient | None = None
_locks: dict[str, asyncio.Lock] = {}
_last_call: dict[str, float] = {}


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
    return _client


def _bucket(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    for key in _MIN_INTERVAL:
        if "/" in key and (host + parsed.path).startswith(key):
            return key
    return host


async def _throttle(bucket: str) -> None:
    lock = _locks.setdefault(bucket, asyncio.Lock())
    async with lock:
        wait = _MIN_INTERVAL.get(bucket, _DEFAULT_INTERVAL) - (time.monotonic() - _last_call.get(bucket, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call[bucket] = time.monotonic()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def _cache_get(key: str, ttl: int) -> Any | None:
    if ttl <= 0:
        return None
    path = _cache_path(key)
    try:
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _cache_put(key: str, value: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(json.dumps(value), "utf-8")
    except OSError:
        pass  # caching is best effort


async def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    ttl: int = DEFAULT_TTL,
    retries: int = 2,
) -> Any:
    """Perform a request and return decoded JSON, using the disk cache for identical requests."""
    key = json.dumps([method, url, params, json_body], sort_keys=True, default=str)
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached

    bucket = _bucket(url)
    for attempt in range(retries + 1):
        await _throttle(bucket)
        resp = await client().request(method, url, params=params, json=json_body)
        if resp.status_code == 429 and attempt < retries:
            await asyncio.sleep(float(resp.headers.get("Retry-After", 2 + 2 * attempt)))
            continue
        if resp.status_code >= 500 and attempt < retries:
            await asyncio.sleep(1 + attempt)
            continue
        break

    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("details") or body.get("detail") or json.dumps(body)[:300]
        except ValueError:
            detail = resp.text[:300]
        raise HttpError(resp.status_code, url, str(detail))

    data = resp.json()
    _cache_put(key, data)
    return data


async def get_json(url: str, params: dict[str, Any] | None = None, ttl: int = DEFAULT_TTL) -> Any:
    return await request_json("GET", url, params=params, ttl=ttl)


async def post_json(url: str, body: Any, ttl: int = DEFAULT_TTL) -> Any:
    return await request_json("POST", url, json_body=body, ttl=ttl)


async def download(url: str, dest: Path) -> Path:
    """Download a binary file (e.g. a card image) unless it already exists."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    await _throttle(_bucket(url))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    async with client().stream("GET", url, headers={"Accept": "*/*"}) as resp:
        if resp.status_code >= 400:
            raise HttpError(resp.status_code, url, "download failed")
        with tmp.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(dest)
    return dest
