"""No hanging jobs and understandable errors when a web service is down."""

import asyncio
import time

import claude_agent_sdk
import httpx
import pytest
from claude_agent_sdk import ResultMessage
from fastapi.testclient import TestClient

from mtgdeck import http
from mtgdeck.gui import app as gui
from mtgdeck.http import HttpError


def _offline(monkeypatch, exc=httpx.ConnectError):
    def down(request: httpx.Request) -> httpx.Response:
        raise exc("down", request=request)

    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(down)))


async def test_unreachable_service_becomes_http_error_with_german_text(monkeypatch):
    _offline(monkeypatch)
    with pytest.raises(HttpError) as info:
        await http.request_json("GET", "https://api.scryfall.com/cards/named", ttl=0, retries=0)
    assert info.value.status == 0
    assert info.value.friendly.startswith("Scryfall ist gerade nicht erreichbar (keine Verbindung)")

    _offline(monkeypatch, httpx.ReadTimeout)
    with pytest.raises(HttpError) as info:
        await http.request_json("GET", "https://json.edhrec.com/pages/x.json", ttl=0, retries=0)
    assert "EDHREC" in info.value.friendly and "Zeitüberschreitung" in info.value.friendly

    with pytest.raises(HttpError) as info:
        await http.download("https://cards.scryfall.io/large/x.jpg", http.CACHE_DIR / "x.jpg")
    assert info.value.status == 0 and not (http.CACHE_DIR / "x.jpg.part").exists()


def test_friendly_texts_by_status():
    assert "bremst" in HttpError(429, "https://api.scryfall.com/x").friendly
    assert "Probleme (Fehler 503)" in HttpError(503, "https://backend.commanderspellbook.com/x").friendly
    assert HttpError(500, "https://image.pollinations.ai/p").friendly.startswith("Der Bildgenerator")
    assert "abgelehnt (Fehler 403)" in HttpError(403, "https://example.org/x").friendly


def test_route_reports_unreachable_service_as_502(monkeypatch):
    async def down(*_a, **_k):
        raise HttpError(0, "https://api.scryfall.com/cards/search", "keine Verbindung")

    monkeypatch.setattr(gui.scryfall, "search", down)
    r = TestClient(gui.app).get("/api/tokens/search", params={"q": "Treasure"})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Scryfall ist gerade nicht erreichbar")

    # routes without their own handling go through the global one
    resp = asyncio.run(gui._http_error(None, HttpError(0, "https://json.edhrec.com/x", "keine Verbindung")))
    assert resp.status_code == 502 and b"EDHREC ist gerade nicht erreichbar" in resp.body


def _wait(job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


def test_job_always_ends_with_done_even_when_its_last_step_crashes(monkeypatch):
    async def fake_query(prompt, options):
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", result="ok")  # fmt: skip

    async def broken_finish(job, ok, text, structured):
        raise HttpError(503, "https://json.edhrec.com/pages/x.json")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    with TestClient(gui.app) as client:
        async def start():
            return gui._start("hallo", None, finish=broken_finish)["job"]

        job_id = client.portal.call(start)
        job = _wait(job_id)
        events = client.get(f"/api/jobs/{job_id}/events").text
    assert job.events[-1] == {"type": "done", "ok": False}
    assert any(e["type"] == "error" and "EDHREC hat gerade Probleme" in e["text"] for e in job.events)
    assert '"type": "done"' in events  # the stream ends instead of waiting forever


def test_runner_crash_and_old_jobs_are_pruned():
    with TestClient(gui.app) as client:
        async def boom(job):
            await asyncio.sleep(0)
            raise KeyError("kaputt")

        async def start():
            return gui._start_runner(boom)["job"]

        old = gui.Job(id="old-job", done=True, ended=time.time() - gui.JOB_KEEP_SECONDS - 5)
        gui.JOBS[old.id] = old
        job = _wait(client.portal.call(start))
        assert job.events == [{"type": "error", "text": "KeyError: 'kaputt'"}, {"type": "done", "ok": False}]
        assert "old-job" not in gui.JOBS
        assert client.get("/api/jobs/old-job/events").status_code == 404
