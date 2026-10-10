"""The desktop shell's bridge (poll: tooltip, notices, commands; what the web app sees of it) and signing in to
Claude with the CLI inside the GUI's terminal."""

import sys
import time

import pytest
from fastapi.testclient import TestClient

from mtgdeck import settings
from mtgdeck.gui import app as gui
from mtgdeck.gui import desktop
from mtgdeck.gui.app import _cli_auth_status  # the real one – conftest fakes the module attribute per test

FAKE_CLI = """\
import json, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "oauth_token"}))
elif args[:2] == ["auth", "login"]:
    print("Opening browser ... Logged in as Tester")
elif args[:2] == ["auth", "logout"]:
    print("Logged out")
else:
    sys.exit(2)
"""


def _fake_cli(tmp_path):
    script = tmp_path / "claude.py"
    script.write_text(FAKE_CLI)
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    exe.chmod(0o755)
    return str(exe)


def _wait(job_id, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if gui.JOBS[job_id].done:
            return gui.JOBS[job_id]
        time.sleep(0.05)
    raise AssertionError(gui.JOBS[job_id].events)


posix = pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a shell script")


@posix
def test_the_real_auth_check_reads_the_cli_json(tmp_path):
    assert _cli_auth_status(_fake_cli(tmp_path)) == {"logged_in": True, "method": "oauth_token"}
    assert _cli_auth_status(str(tmp_path / "missing")) == {"logged_in": None, "method": None}


def test_ai_route_reports_the_sign_in_state_and_a_missing_login_blocks(monkeypatch):
    client = TestClient(gui.app)
    st = client.get("/api/ai").json()
    assert st["available"] and st["login"] == {"logged_in": True, "method": "test"} and "bundled" in st["cli"]
    monkeypatch.setattr(gui, "_cli_auth_status", lambda cli: {"logged_in": False, "method": None})
    st = client.get("/api/ai", params={"refresh": "true"}).json()
    assert not st["available"] and "noch nicht angemeldet" in st["reason"] and st["login"]["logged_in"] is False
    assert client.post("/api/find-commander", json={"prompt": "Zombies"}).status_code == 503
    monkeypatch.setattr(gui, "_cli_auth_status", lambda cli: {"logged_in": None, "method": None})  # cannot tell: no block
    assert client.get("/api/ai", params={"refresh": "true"}).json()["available"]


def test_login_needs_the_cli(monkeypatch):
    monkeypatch.setattr(gui, "_find_claude_cli", lambda: None)
    client = TestClient(gui.app)
    assert client.post("/api/ai/login", json={}).status_code == 400
    assert "login" not in client.get("/api/ai").json()


@posix
def test_login_runs_the_cli_in_the_terminal(monkeypatch, tmp_path):
    if not gui.terminal.available():
        pytest.skip("no pty")
    monkeypatch.setattr(gui, "_find_claude_cli", lambda: _fake_cli(tmp_path))
    monkeypatch.setattr(gui, "_cli_auth_status", _cli_auth_status)  # the real check, against the fake CLI
    gui._ai_failure.update(text="old", at=time.time())
    with TestClient(gui.app) as client:
        j = _wait(client.post("/api/ai/login", json={}).json()["job"])
        assert any(e["type"] == "console" and e["running"] for e in j.events)
        assert any(e["type"] == "term" and "Logged in" in e["data"] for e in j.events)
        assert j.events[-1] == {"type": "done", "ok": True}
        assert not gui._ai_failure  # a fresh sign-in clears the remembered failure
        st = client.get("/api/ai").json()
        assert st["login"] == {"logged_in": True, "method": "oauth_token"}
        assert "login" in client.post("/api/ai/logout").json()


def test_without_a_shell_nothing_is_shown_or_queued():
    client = TestClient(gui.app)
    assert client.get("/api/desktop").json() == {"shell": None, "notify": True}
    assert desktop.notify("Deck fertig") is None  # nobody listens
    assert client.post("/api/desktop/command", json={"type": "open_data"}).status_code == 409


def test_shell_poll_reports_state_and_fetches_notices_and_commands_once():
    client = TestClient(gui.app)
    r = client.post("/api/desktop/poll", json={"since": 0, "visible": False, "autostart": True, "version": "0.1.0"}).json()
    assert r["notices"] == [] and r["commands"] == [] and r["tooltip"] == "MTG Deckbuilder"
    shell = client.get("/api/desktop").json()["shell"]
    assert shell["version"] == "0.1.0" and shell["autostart"] is True and shell["visible"] is False
    n = desktop.notify("Deck fertig", "„Meren“ ist gespeichert.")
    client.post("/api/desktop/command", json={"type": "autostart", "value": False})
    client.post("/api/desktop/command", json={"type": "autostart", "value": True})  # replaces the older one
    client.post("/api/desktop/command", json={"type": "open_log"})
    r = client.post("/api/desktop/poll", json={"since": 0}).json()
    assert [x["title"] for x in r["notices"]] == ["Deck fertig"] and r["since"] == n["id"]
    assert r["commands"] == [{"type": "autostart", "value": True}, {"type": "open_log"}]
    r = client.post("/api/desktop/poll", json={"since": r["since"]}).json()
    assert r["notices"] == [] and r["commands"] == []
    assert client.post("/api/desktop/command", json={"type": "reboot"}).status_code == 422


def test_notices_follow_the_setting_and_the_tooltip_shows_the_state(monkeypatch):
    client = TestClient(gui.app)
    client.post("/api/desktop/poll", json={})
    settings.update({"desktop_notify": False})
    assert desktop.notify("x") is None
    settings.update({"desktop_notify": True})
    assert desktop.notify("x")["title"] == "x"
    monkeypatch.setattr(gui, "_sync_view", lambda: {"connected": True, "last_ok": time.time() - 150, "running": False,
                                                    "online": True, "phone": {"current": {"id": "q1"}}})  # fmt: skip
    gui.JOBS["t1"] = gui.Job(id="t1")
    try:
        tip = client.post("/api/desktop/poll", json={}).json()["tooltip"].split("\n")
    finally:
        gui.JOBS.pop("t1", None)
    assert tip == ["MTG Deckbuilder", "Ein Auftrag läuft", "Sync: vor 2 Min.", "Beantwortet gerade eine Frage vom Handy"]
