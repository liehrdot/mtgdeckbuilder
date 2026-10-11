"""The backend as a program: data folders from one root, the launcher's arguments, the access token of the desktop
shell, the health route and the frozen-aware MCP command."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mtgdeck import launch, paths
from mtgdeck.gui import app as gui


def test_paths_follow_mtg_home_and_the_frozen_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("MTG_HOME", raising=False)
    monkeypatch.delenv("MTG_CACHE_HOME", raising=False)
    monkeypatch.delenv("MTG_AGENT_ROOT", raising=False)
    assert paths.home() == paths.REPO_ROOT and paths.agent_root() == paths.REPO_ROOT  # a checkout: as before
    monkeypatch.setenv("MTG_HOME", str(tmp_path / "daten"))
    monkeypatch.setenv("MTG_CACHE_HOME", str(tmp_path / "cache"))
    assert paths.home() == tmp_path / "daten" and paths.cache_home() == tmp_path / "cache"
    monkeypatch.delenv("MTG_HOME")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert paths.frozen() and paths.agent_root() == tmp_path / "bundle" / "agent"
    home = paths.home()
    assert home != paths.REPO_ROOT and home.name in ("MTG Deckbuilder", "mtgdeck")  # the platform's app-data folder
    assert gui._mcp_command() == [sys.executable, "mcp"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert gui._mcp_command() == [sys.executable, "-m", "mtgdeck.mcp_server"]


def test_launcher_arguments_become_environment(monkeypatch, tmp_path):
    for k in ("MTG_HOME", "MTG_CACHE_HOME", "MTG_GUI_HOST", "MTG_GUI_PORT", "MTG_GUI_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    launch.apply(launch._parser().parse_args(["--data", str(tmp_path), "--port", "0", "--token", "auto", "--host", "127.0.0.1"]))
    assert os.environ["MTG_HOME"] == str(tmp_path) and os.environ["MTG_GUI_PORT"] == "0" and os.environ["MTG_GUI_HOST"] == "127.0.0.1"
    assert len(os.environ["MTG_GUI_TOKEN"]) >= 24
    launch.apply(launch._parser().parse_args(["--token", "geheim"]))
    assert os.environ["MTG_GUI_TOKEN"] == "geheim"
    assert launch._parser().parse_args(["mcp"]).command == "mcp" and launch._parser().parse_args([]).command == "serve"


def test_token_guards_everything_but_health_and_static(monkeypatch):
    monkeypatch.setattr(gui, "GUI_TOKEN", "s3cret")
    client = TestClient(gui.app)
    assert client.get("/api/health").json()["ok"] is True  # the shell's readiness check needs no token
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/api/decks").status_code == 401 and client.get("/").status_code == 401
    assert client.get("/?token=falsch", follow_redirects=False).status_code == 401
    r = client.get("/?token=s3cret", follow_redirects=False)  # the first link sets the cookie
    assert r.status_code == 303 and r.headers["location"] == "/" and "mtg_token=s3cret" in r.headers["set-cookie"]
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()  # Lax: see the test below
    assert client.get("/api/decks").status_code == 200  # the TestClient keeps the cookie
    assert client.get("/").status_code == 200
    bare = TestClient(gui.app)
    assert bare.get("/api/decks", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert bare.get("/api/decks", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_without_a_token_the_app_is_open_as_before(monkeypatch):
    monkeypatch.setattr(gui, "GUI_TOKEN", "")
    client = TestClient(gui.app)
    assert client.get("/api/decks").status_code == 200
    h = client.get("/api/health").json()
    assert h["service"] == "mtgdeck-gui" and h["frozen"] is False and h["home"]


@pytest.mark.skipif(os.environ.get("MTG_TEST_SUBPROCESS") != "1", reason="starts a real server; set MTG_TEST_SUBPROCESS=1")
def test_serve_on_a_free_port_prints_the_url(tmp_path):
    env = {**os.environ, "MTG_SYNC_AUTO": "0", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "mtgdeck.launch", "--data", str(tmp_path), "--port", "0", "--token", "auto"],
                            stdout=subprocess.PIPE, text=True, env=env)  # fmt: skip
    try:
        url = None
        for _ in range(200):
            line = proc.stdout.readline()
            if line.startswith("MTGDECK_URL="):
                url = line.split("=", 1)[1].strip()
                break
        assert url and "?token=" in url
        port = int(url.split(":")[2].split("/")[0])
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        health = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5).read()
        assert b'"ok":true' in health and str(tmp_path).encode() in health
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/decks", timeout=5)
        assert exc.value.code == 401
    finally:
        proc.terminate()
        proc.wait(10)


def test_token_cookie_survives_the_shells_cross_site_navigation(monkeypatch):
    """The desktop shell's start page lives on tauri.localhost and navigates to /?token=…; the cookie set by the
    redirect must be sent on the navigation that follows, which a SameSite=Strict cookie is not (Chromium shows
    the 401 JSON instead of the app). Lax is sent on top-level navigations and still withheld from cross-site POSTs."""
    from fastapi.testclient import TestClient

    from mtgdeck.gui import app as gui

    monkeypatch.setattr(gui, "GUI_TOKEN", "tok123")
    client = TestClient(gui.app, follow_redirects=False)
    r = client.get("/?token=tok123")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "mtg_token=tok123" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/?token=wrong").status_code == 401
