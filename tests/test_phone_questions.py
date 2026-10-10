"""„Frag Claude“ from the phone (phase 4): a question becomes an open message in the conversation and a job on the
sync server; a PC claims it, reports progress and sends the answer, which the server writes into the conversation."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from mtgdeck.sync import appdata, appops, server
from mtgdeck.sync.files import decode
from mtgdeck.sync.remote import HttpTransport
from mtgdeck.sync.server import create_app

AT = "2026-10-10T20:00:00+00:00"
CHAT = "a1b2c3d4e5f6"


def _op(type_, payload, op_id):
    return {"id": op_id, "type": type_, "payload": payload, "at": AT}


def _ask(msg_id, question="Wie spiele ich gegen Atraxa?", chat_id=CHAT, deep=False, op_id=None):
    return _op("chat.ask", {"chat_id": chat_id, "message_id": msg_id, "question": question, "deep": deep}, op_id or f"op{msg_id}")


# ---- the operations (pure) ----------------------------------------------------------------------

def test_chat_ask_opens_a_conversation_and_is_idempotent():
    out = appops.apply_copy(_ask("0000000001"), {})
    doc = out[f"decks/.chats/{CHAT}.json"]
    assert doc["id"] == CHAT and doc["title"] == "Wie spiele ich gegen Atraxa?" and doc["created"] == AT
    m = doc["messages"][0]
    assert m == {"id": "0000000001", "asked": AT, "question": "Wie spiele ich gegen Atraxa?", "answer": None, "status": "waiting",
                 "deep": False, "source": "phone"}  # fmt: skip
    docs = {f"decks/.chats/{CHAT}.json": doc}
    assert appops.apply_copy(_ask("0000000001"), docs) == {}  # sent twice
    follow = appops.apply_copy(_ask("0000000002", "Und gegen Krenko?", deep=True), docs)[f"decks/.chats/{CHAT}.json"]
    assert [m["id"] for m in follow["messages"]] == ["0000000001", "0000000002"] and follow["messages"][1]["deep"]
    assert follow["title"] == doc["title"]  # the first question names the conversation
    with pytest.raises(appops.OpError):
        appops.apply_copy(_ask("0000000003", "?"), docs)
    with pytest.raises(appops.OpError):
        appops.paths(_ask("0000000003", chat_id="../x"))


def test_chat_cancel_takes_back_open_questions_only():
    path = f"decks/.chats/{CHAT}.json"
    doc = appops.apply_copy(_ask("0000000001"), {})[path]
    answered = appops.answer_chat(doc, "0000000001", at=AT, answer="So.")
    asked2 = appops.apply_copy(_ask("0000000002"), {path: answered})[path]
    cancel = lambda mid, docs: appops.apply_copy(_op("chat.cancel", {"chat_id": CHAT, "message_id": mid}, "opc" + mid), docs)  # noqa: E731
    assert cancel("0000000001", {path: asked2}) == {}  # answered: stays
    left = cancel("0000000002", {path: asked2})[path]
    assert [m["id"] for m in left["messages"]] == ["0000000001"]
    assert cancel("0000000001", {path: doc}) == {path: None}  # the only question: the conversation goes away
    assert cancel("0000000001", {}) == {}


def test_answer_chat_writes_the_answer_or_the_failure():
    doc = appops.apply_copy(_ask("0000000001"), {})[f"decks/.chats/{CHAT}.json"]
    ok = appops.answer_chat(doc, "0000000001", at=AT, answer=" Spiel **schnell**. ", cards={"Sol Ring": {"image": "x"}}, decks={"meren": {"name": "Meren"}})
    m = ok["messages"][0]
    assert m["answer"] == "Spiel **schnell**." and m["answered"] == AT and "status" not in m and m["decks"] == {"meren": {"name": "Meren"}}
    assert appops.answer_chat(ok, "0000000001", at=AT, answer="noch mal") is None  # answered already
    assert appops.answer_chat(doc, "nope000000", at=AT, answer="x") is None  # taken back
    failed = appops.answer_chat(doc, "0000000001", at=AT, error="Claude Code ist nicht angemeldet")["messages"][0]
    assert failed["status"] == "failed" and failed["error"] == "Claude Code ist nicht angemeldet" and failed["answer"] is None


def test_the_read_model_carries_conversations():
    path = f"decks/.chats/{CHAT}.json"
    doc = appops.apply_copy(_ask("0000000001"), {})[path]
    doc = appops.answer_chat(doc, "0000000001", at=AT, answer="Mit {{meren}}.", decks={"meren": {"name": "Meren", "commanders": []}})
    doc = appops.apply_copy(_ask("0000000002", "Und dann?"), {path: doc})[path]
    pc_chat = {"id": "ffffffffffff", "title": "Vom PC", "updated": "2026-10-01T00:00:00+00:00",
               "messages": [{"id": "x1", "question": "Welches Deck?", "answer": "Meren.", "cards": {"Sol Ring": {"image": "i"}}}]}  # fmt: skip
    snap = appdata.snapshot({path: doc, "decks/.chats/ffffffffffff.json": pc_chat, "decks/.chats/000000000000.json": {"messages": []}})
    assert [c["id"] for c in snap["chats"]] == [CHAT, "ffffffffffff"]  # newest first, empty ones left out
    first, second = snap["chats"][0]["messages"]
    assert first["answer"] == "Mit {{meren}}." and first["decks"] == {"meren": "Meren"} and first["source"] == "phone"
    assert second["answer"] is None and second["status"] == "waiting"
    assert snap["chats"][1]["messages"][0] == {"id": "x1", "asked": None, "question": "Welches Deck?", "answer": "Meren.", "deep": False, "source": "pc"}


# ---- the routes: phone asks, PC answers ---------------------------------------------------------

def _setup(tmp_path):
    app = create_app(tmp_path / "server")
    client = TestClient(app)
    t = HttpTransport("http://testserver", client=client)
    phone = t.claim(app.state.devices.new_code()["code"], "Handy", "phone")
    pc = t.claim(app.state.devices.new_code()["code"], "Tudl", "pc")
    return app, client, {"Authorization": f"Bearer {phone['token']}"}, {"Authorization": f"Bearer {pc['token']}"}


def _chat_doc(app):
    d = app.state.store.get(f"decks/.chats/{CHAT}.json")
    return decode("json", d["data"]) if d and not d["deleted"] else None


def test_a_question_from_the_phone_is_answered_by_the_pc(tmp_path):
    app, client, phone, pc = _setup(tmp_path)
    assert client.post("/api/jobs/claim", headers=phone).status_code == 403  # phones ask, PCs answer
    assert client.post("/api/jobs/claim", headers=pc).json() == {"job": None}

    res = client.post("/api/app/ops", json={"ops": [_ask("0000000001", deep=True)]}, headers=phone).json()
    assert res["results"] == [{"id": "op0000000001", "ok": True}]
    data = client.get("/api/app/data", headers=phone).json()
    assert data["chats"][0]["messages"][0]["status"] == "waiting"
    assert client.get("/api/app/status", headers=phone).json()["jobs"][0] | {"created": 0} == {
        "id": "0000000001", "chat_id": CHAT, "status": "waiting", "progress": None, "error": None, "created": 0, "finished": None}

    job = client.post("/api/jobs/claim", headers=pc).json()["job"]
    assert job == {"id": "0000000001", "chat_id": CHAT, "title": "Wie spiele ich gegen Atraxa?", "question": "Wie spiele ich gegen Atraxa?",
                   "deep": True, "asked": AT, "history": []}  # fmt: skip
    assert client.post("/api/jobs/claim", headers=pc).json() == {"job": None}  # taken
    assert client.post("/api/jobs/0000000001/progress", json={"text": "liest Kartentexte"}, headers=pc).json() == {"status": "running"}
    st = client.get("/api/app/status", headers=phone).json()["jobs"][0]
    assert st["status"] == "running" and st["progress"] == "liest Kartentexte"

    done = client.post("/api/jobs/0000000001/finish", json={"answer": "Halte Removal für [[Atraxa, Praetors' Voice]].",
                                                            "cards": {"Atraxa, Praetors' Voice": {"image": "i"}}}, headers=pc).json()  # fmt: skip
    assert done == {"status": "done"}
    m = _chat_doc(app)["messages"][0]
    assert m["answer"].startswith("Halte Removal") and m["cards"]["Atraxa, Praetors' Voice"] == {"image": "i"}
    assert client.get("/api/app/status", headers=phone).json()["jobs"][0]["status"] == "done"
    assert client.get("/api/app/data", headers=phone).json()["chats"][0]["messages"][0]["answer"].startswith("Halte")
    assert client.post("/api/jobs/0000000001/finish", json={"answer": "zweimal"}, headers=pc).json() == {"status": "done"}  # nothing rewritten
    assert _chat_doc(app)["messages"][0]["answer"].startswith("Halte")

    # a follow-up carries the earlier exchange along
    client.post("/api/app/ops", json={"ops": [_ask("0000000002", "Und gegen Krenko?")]}, headers=phone)
    job = client.post("/api/jobs/claim", headers=pc).json()["job"]
    assert job["question"] == "Und gegen Krenko?" and job["history"] == [{"question": "Wie spiele ich gegen Atraxa?", "answer": m["answer"]}]
    failed = client.post("/api/jobs/0000000002/finish", json={"error": "Claude Code ist nicht angemeldet"}, headers=pc).json()
    assert failed == {"status": "failed"}
    st = client.get("/api/app/status", headers=phone).json()["jobs"][1]
    assert st["status"] == "failed" and st["error"] == "Claude Code ist nicht angemeldet"
    assert _chat_doc(app)["messages"][1]["status"] == "failed"


def test_taking_back_a_question_and_a_pc_that_goes_silent(tmp_path, monkeypatch):
    app, client, phone, pc = _setup(tmp_path)
    client.post("/api/app/ops", json={"ops": [_ask("0000000001")]}, headers=phone)
    cancel = _op("chat.cancel", {"chat_id": CHAT, "message_id": "0000000001"}, "opcancel01")
    assert client.post("/api/app/ops", json={"ops": [cancel]}, headers=phone).json()["results"][0]["ok"]
    assert _chat_doc(app) is None and client.post("/api/jobs/claim", headers=pc).json() == {"job": None}

    # taken back while the PC works on it: the PC hears so and its answer is not written
    client.post("/api/app/ops", json={"ops": [_ask("0000000002")]}, headers=phone)
    assert client.post("/api/jobs/claim", headers=pc).json()["job"]["id"] == "0000000002"
    client.post("/api/app/ops", json={"ops": [_op("chat.cancel", {"chat_id": CHAT, "message_id": "0000000002"}, "opcancel02")]}, headers=phone)
    assert client.post("/api/jobs/0000000002/progress", json={"text": "x"}, headers=pc).json() == {"status": "cancelled"}
    assert client.post("/api/jobs/0000000002/finish", json={"answer": "zu spät"}, headers=pc).json() == {"status": "cancelled"}
    assert _chat_doc(app) is None

    # a PC that claims and then goes silent: after JOB_LEASE the question waits again and another PC takes it
    client.post("/api/app/ops", json={"ops": [_ask("0000000003")]}, headers=phone)
    assert client.post("/api/jobs/claim", headers=pc).json()["job"]["id"] == "0000000003"
    pc2 = {"Authorization": f"Bearer {HttpTransport('http://testserver', client=client).claim(app.state.devices.new_code()['code'], 'Laptop', 'pc')['token']}"}
    assert client.post("/api/jobs/claim", headers=pc2).json() == {"job": None}
    real = time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.JOB_LEASE + 5)
    assert client.get("/api/app/status", headers=phone).json()["jobs"][-1]["status"] == "waiting"
    assert client.post("/api/jobs/claim", headers=pc2).json()["job"]["id"] == "0000000003"
    assert client.post("/api/jobs/0000000003/progress", json={}, headers=pc).json() == {"status": "taken"}
    assert client.post("/api/jobs/0000000003/finish", json={"answer": "alt"}, headers=pc).json() == {"status": "taken"}
    assert client.post("/api/jobs/0000000003/finish", json={"answer": "neu"}, headers=pc2).json() == {"status": "done"}
    assert _chat_doc(app)["messages"][0]["answer"] == "neu"


def test_open_questions_are_limited_per_phone(tmp_path):
    app, client, phone, _pc = _setup(tmp_path)
    ops = [_ask(f"{i:010d}", op_id=f"opask{i:04d}") for i in range(server.MAX_OPEN_QUESTIONS + 1)]
    res = client.post("/api/app/ops", json={"ops": ops}, headers=phone).json()["results"]
    assert all(r["ok"] for r in res[:-1])
    assert res[-1]["ok"] is False and res[-1]["retry"] and "warten schon" in res[-1]["error"]  # stays in the phone's queue


def test_a_waiting_pc_gets_a_new_question_right_away(tmp_path):
    app, _client, phone, pc = _setup(tmp_path)
    got: dict = {}
    with TestClient(app) as client:  # one event loop for both requests, like uvicorn
        _wait_and_ask(client, phone, pc, got)
    assert got["job"]["id"] == "0000000001" and got["took"] < 5


def _wait_and_ask(client, phone, pc, got):

    def claim():
        started = time.monotonic()
        got["job"] = client.post("/api/jobs/claim?wait=10", headers=pc).json()["job"]
        got["took"] = time.monotonic() - started

    t = threading.Thread(target=claim)
    t.start()
    time.sleep(0.5)
    client.post("/api/app/ops", json={"ops": [_ask("0000000001")]}, headers=phone)
    t.join(10)


# ---- the PC: claims the question, answers it with the app chat, the answer comes back via sync -----------

def _pc_with_server(tmp_path, monkeypatch):
    """The PC (this test's data folders) paired with a real sync server app; phone paired too."""
    from mtgdeck import storage
    from mtgdeck.sync import Roots, service

    app = create_app(tmp_path / "server")
    client = TestClient(app)
    monkeypatch.setattr(service, "HttpTransport", lambda url, token="", **kw: HttpTransport(url, token, client=client))
    service.connect(f"http://testserver/koppeln#{app.state.devices.new_code()['code']}", name="Tudl", roots=Roots.current())
    storage.save({"name": "Meren", "commanders": ["Meren of Clan Nel Toth"], "bracket": 3, "cards": [{"name": "Sol Ring", "qty": 1, "category": "Ramp"}]})
    assert service.run()["ok"]
    phone = HttpTransport("http://testserver", client=client).claim(app.state.devices.new_code()["code"], "Handy", "phone")
    return app, client, {"Authorization": f"Bearer {phone['token']}"}


async def test_the_pc_answers_a_phone_question_and_gets_the_conversation(tmp_path, monkeypatch):
    import asyncio

    import claude_agent_sdk
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

    from mtgdeck import chat
    from mtgdeck.gui import app as gui
    from mtgdeck.sync import service

    app, client, phone = _pc_with_server(tmp_path, monkeypatch)
    client.post("/api/app/ops", json={"ops": [_ask("0000000001", "Wie spiele ich Meren gegen Atraxa?")]}, headers=phone)
    seen: dict = {"labels": []}
    answer = "Mit {{meren}}: halte [[Sol Ring]] für den schnellen Start."

    async def fake_query(prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__mtg__load_deck", input={"slug": "meren"})], model="m")
        await asyncio.sleep(0.3)
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s", result=answer)

    real_progress = service.job_progress
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(gui, "PHONE_LOOK", 0.05)
    monkeypatch.setattr(gui.sync_service, "job_progress", lambda jid, text="": (seen["labels"].append(text), real_progress(jid, text))[1])

    job = service.claim_job(0)
    assert job["id"] == "0000000001"
    assert await gui._answer_phone(job) == "done"
    assert "sieht sich „Meren“ an" in seen["labels"]
    assert seen["prompt"].endswith("Frage: Wie spiele ich Meren gegen Atraxa?") and "mcp__mtg__save_deck" in seen["options"].disallowed_tools
    assert "aus der Handy-App" in seen["prompt"]  # short answers for the table
    m = chat.get(CHAT)["messages"][0]  # the sync after the answer brought the conversation to this PC
    assert m["answer"] == answer and m["source"] == "phone" and m["decks"]["meren"]["name"] == "Meren" and m["cards"]["Sol Ring"]["image"]
    assert client.get("/api/app/status", headers=phone).json()["jobs"][0]["status"] == "done"

    # a follow-up asked on the PC continues the same conversation (the open/failed phone messages carry no answer)
    assert gui.chat_prompt("Und gegen Krenko?", chat.get(CHAT)["messages"] + [{"question": "offen", "answer": None}]).count("Frage: ") == 2


async def test_a_question_taken_back_stops_the_pc(tmp_path, monkeypatch):
    import asyncio

    import claude_agent_sdk
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

    from mtgdeck.gui import app as gui
    from mtgdeck.sync import service

    app, client, phone = _pc_with_server(tmp_path, monkeypatch)
    client.post("/api/app/ops", json={"ops": [_ask("0000000001")]}, headers=phone)

    async def slow_query(prompt, options):
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="mcp__mtg__app_overview", input={})], model="m")
        client.post("/api/app/ops", json={"ops": [_op("chat.cancel", {"chat_id": CHAT, "message_id": "0000000001"}, "opcancel01")]}, headers=phone)
        await asyncio.sleep(5)
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2, session_id="s", result="zu spät")

    monkeypatch.setattr(claude_agent_sdk, "query", slow_query)
    monkeypatch.setattr(gui, "PHONE_LOOK", 0.05)
    started = time.monotonic()
    assert await gui._answer_phone(service.claim_job(0)) == "cancelled"
    assert time.monotonic() - started < 4  # stopped, not waited for
    assert _chat_doc(app) is None


def test_whether_this_pc_answers(monkeypatch):
    from mtgdeck import settings as settings_mod
    from mtgdeck.gui import app as gui

    cfg = {"server": "http://x", "token": "t"}
    monkeypatch.setattr(gui.sync_service, "config", lambda: cfg)
    monkeypatch.setattr(gui, "ai_status", lambda: {"available": True})
    assert gui._phone_enabled()
    settings_mod.update({"phone_questions": False})
    assert not gui._phone_enabled()
    settings_mod.update({"phone_questions": True})
    cfg["revoked"] = True
    assert not gui._phone_enabled()
