"""Sending the village a link, a video or a file (src/trading/inbox.py).

The rows are fakes made here; nothing is fetched or transcribed. What is checked
is the bookkeeping: what waits for the PC, what is read at once, that the bytes
go once read, and that the calls reach the idea lab and nowhere else.
"""

from __future__ import annotations

import importlib
import json
from datetime import timedelta

import pytest

from src.db.connection import Database, to_iso, utcnow
from src.trading import inbox

SYMBOLS = ["NVDA", "AAPL", "BTC-USD", "SPY"]


@pytest.fixture
def db(tmp_path):
    d = Database.from_url(f"sqlite:///{tmp_path / 'inbox.db'}")
    d.init_schema()
    yield d
    d.close()


def test_the_platform_is_told_from_the_link():
    assert inbox.platform_of("https://www.instagram.com/reel/ABC123/") == "instagram"
    assert inbox.platform_of("https://vm.tiktok.com/xyz/") == "tiktok"
    assert inbox.platform_of("https://x.com/someone/status/1") == "x"
    assert inbox.platform_of("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert inbox.platform_of("https://example.com/article") == "web"


def test_the_kind_of_file_is_told_from_its_name_or_type():
    assert inbox.kind_of("clip.MOV") == "video"
    assert inbox.kind_of("IMG_1.heic") == "image"
    assert inbox.kind_of("memo.m4a") == "audio"
    assert inbox.kind_of("notes.txt") == "text"
    assert inbox.kind_of("report.pdf") == "pdf"
    assert inbox.kind_of("blob", "video/mp4") == "video"


def test_a_link_pasted_from_a_share_sheet_is_found_in_its_words(db):
    row_id = inbox.submit_link(db, "Check this out https://www.instagram.com/reel/C0de1/?igsh=x",
                               "guy says buy nvidia")
    row = db.query_one("SELECT * FROM inbox WHERE id = ?", (row_id,))
    assert row["url"] == "https://www.instagram.com/reel/C0de1/?igsh=x"
    assert row["platform"] == "instagram" and row["status"] == "queued"
    with pytest.raises(inbox.Refused):
        inbox.submit_link(db, "not a link at all")


def test_a_text_file_is_read_at_once_with_its_calls(db):
    row_id = inbox.submit_file(db, "tips.txt", "text/plain",
                               b"I am buying NVDA shares here, and $PLTR too: buy PLTR now.",
                               symbols=SYMBOLS)
    row = db.query_one("SELECT * FROM inbox WHERE id = ?", (row_id,))
    assert row["status"] == "read" and row["body"] is None
    calls = {c["symbol"]: c["direction"] for c in json.loads(row["calls"])}
    assert calls.get("NVDA") == 1
    assert calls.get("PLTR") == 1          # a cashtag outside the universe still counts


def test_a_mention_is_not_a_call(db):
    row_id = inbox.submit_file(db, "news.txt", "text/plain",
                               b"Nvidia was in the news today.", symbols=SYMBOLS)
    row = db.query_one("SELECT calls FROM inbox WHERE id = ?", (row_id,))
    assert json.loads(row["calls"]) == []


def test_a_video_waits_for_the_pc_and_its_bytes_go_once_heard(db):
    row_id = inbox.submit_file(db, "clip.mp4", "video/mp4", b"\x00fakevideo", note="watch this")
    got = inbox.claim(db)
    assert got["id"] == row_id and got["status"] == "reading"
    assert got["body"] == b"\x00fakevideo"
    assert inbox.claim(db) is None                    # claimed once, not twice
    calls = inbox.finish(db, row_id, text="I'd sell bitcoin here", title="clip.mp4",
                         heard_by="whisper", symbols=SYMBOLS)
    assert calls and calls[0]["symbol"] == "BTC-USD" and calls[0]["direction"] == -1
    row = db.query_one("SELECT * FROM inbox WHERE id = ?", (row_id,))
    assert row["status"] == "read" and row["body"] is None and row["read_at"]


def test_a_too_big_or_empty_file_is_refused(db, monkeypatch):
    monkeypatch.setattr(inbox, "MAX_BYTES", 10)
    with pytest.raises(inbox.Refused):
        inbox.submit_file(db, "big.mp4", "video/mp4", b"x" * 11)
    with pytest.raises(inbox.Refused):
        inbox.submit_file(db, "empty.mp4", "video/mp4", b"")


def test_a_failure_is_retried_then_given_up_and_the_file_dropped(db):
    row_id = inbox.submit_file(db, "clip.mp4", "video/mp4", b"v")
    for _ in range(inbox.MAX_ATTEMPTS - 1):
        assert inbox.claim(db)["id"] == row_id
        inbox.fail(db, row_id, "whisper crashed")
        assert db.query_one("SELECT status FROM inbox WHERE id = ?", (row_id,))["status"] == "queued"
    inbox.claim(db)
    inbox.fail(db, row_id, "whisper crashed")
    row = db.query_one("SELECT * FROM inbox WHERE id = ?", (row_id,))
    assert row["status"] == "failed" and row["body"] is None


def test_a_failed_link_can_be_tried_again(db):
    row_id = inbox.submit_link(db, "https://x.com/a/status/1")
    inbox.claim(db)
    inbox.fail(db, row_id, "asked for a person", retry=False)
    inbox.retry(db, row_id)
    assert inbox.claim(db)["id"] == row_id


def test_a_claim_the_pc_never_finished_is_taken_back(db):
    row_id = inbox.submit_link(db, "https://www.tiktok.com/@a/video/1")
    inbox.claim(db)
    old = to_iso(utcnow() - inbox.STALE_CLAIM - timedelta(minutes=1))
    db.execute("UPDATE inbox SET claimed_at = ? WHERE id = ?", (old, row_id))
    assert inbox.claim(db)["id"] == row_id


def test_calls_go_to_the_idea_lab_until_it_has_entered_them(db):
    row_id = inbox.submit_file(db, "t.txt", "text/plain", b"buy AAPL now", symbols=SYMBOLS)
    (call,) = inbox.lab_calls(db)
    assert call.publisher == inbox.PUBLISHER and call.symbol == "AAPL" and call.side == "buy"
    assert call.note.startswith(f"#{row_id}")
    # The lab opens it (as IdeaLab._open would), and it is not offered again.
    db.insert("sandbox_ideas", {
        "publisher": inbox.PUBLISHER, "symbol": "AAPL", "side": "buy", "horizon": "1h",
        "opened_bar": str(utcnow()), "due_at": to_iso(utcnow() + timedelta(hours=1)),
        "entry_price": "100"})
    assert inbox.mark_entered(db) == 1
    assert inbox.lab_calls(db) == []
    (row,) = inbox.recent(db)
    assert row["calls"][0]["in_lab"]


def test_old_sends_stop_being_offered(db):
    row_id = inbox.submit_file(db, "t.txt", "text/plain", b"buy AAPL now", symbols=SYMBOLS)
    db.update("inbox", row_id, {"read_at": to_iso(utcnow() - inbox.LAB_WINDOW - timedelta(hours=1))})
    assert inbox.lab_calls(db) == []


def test_sends_never_reach_the_signal_board(db):
    inbox.submit_file(db, "t.txt", "text/plain", b"buy AAPL now", symbols=SYMBOLS)
    inbox.lab_calls(db)
    assert db.query("SELECT * FROM signals") == []


# =========================================================================
# the page
# =========================================================================
TOKEN = "a-long-enough-token-for-real-use"


@pytest.fixture
def hosted(tmp_path, firms_yaml, monkeypatch):
    from fastapi.testclient import TestClient

    # import_module, not `from src import ...`: tests/test_asgi.py drops src.*
    # from sys.modules, and a stale package attribute cannot be reloaded.
    access = importlib.import_module("src.access")
    deploy = importlib.import_module("src.deploy")

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["trade", "init"])
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    access.reset_attempts()
    web = importlib.import_module("src.agents.web")
    importlib.reload(deploy)
    web = importlib.reload(web)
    yield TestClient(web.app, raise_server_exceptions=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MVV_GATE_TOKEN", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    access.reset_attempts()
    importlib.reload(deploy)
    importlib.reload(web)


def test_the_page_shows_nothing_sent_until_signed_in(hosted):
    page = hosted.get("/village/send")
    assert page.status_code == 200
    assert "Sign in" in page.text and "What you sent" not in page.text
    refused = hosted.post("/village/actions/send", data={"link": "https://x.com/a/status/1"},
                          follow_redirects=False)
    assert refused.status_code == 403


def test_signed_in_a_link_and_a_file_can_be_sent(hosted):
    access = importlib.import_module("src.access")

    hosted.post(access.UNLOCK_PATH, data={access.FIELD: TOKEN, "next": "/village/send"},
                follow_redirects=False)
    sent = hosted.post(
        "/village/actions/send",
        data={"link": "https://www.instagram.com/reel/C0de1/", "note": "look"},
        files={"files": ("clip.mp4", b"\x00video", "video/mp4")},
        follow_redirects=False)
    assert sent.status_code == 303 and "/village/send?said=" in sent.headers["location"]
    page = hosted.get("/village/send")
    assert "What you sent" in page.text
    assert "instagram.com/reel/C0de1" in page.text and "clip.mp4" in page.text
    assert "waiting for the PC" in page.text


# =========================================================================
# talking to the village (src/trading/chat.py)
# =========================================================================
def test_a_question_waits_is_claimed_once_and_answered(db):
    from src.trading import chat

    q = chat.post(db, "how are the firms doing?")
    assert [m["id"] for m in chat.waiting(db)] == [q]
    got = chat.claim(db)
    assert got["id"] == q and got["status"] == "thinking"
    assert chat.claim(db) is None
    chat.answer(db, q, "Quiet day.", by="opus")
    assert chat.waiting(db) == []
    assert [m["role"] for m in chat.history(db)] == ["you", "village"]
    with pytest.raises(ValueError):
        chat.post(db, "   ")


def test_a_failed_answer_is_tried_again_then_given_up(db):
    from src.trading import chat

    q = chat.post(db, "hello")
    for _ in range(chat.MAX_ATTEMPTS):
        assert chat.claim(db)["id"] == q
        chat.fail(db, q, "claude timed out")
    assert db.query_one("SELECT status FROM village_chat WHERE id = ?", (q,))["status"] == "failed"
    assert chat.waiting(db) == []


def test_the_prompt_carries_the_village_and_the_conversation(db):
    from src.trading import chat

    db.insert("firms", {"firm_key": "big5", "name": "Big-5 Trend", "status": "active",
                        "allocation": "20000", "cash": "1000"})
    inbox.submit_file(db, "t.txt", "text/plain", b"buy AAPL now", symbols=SYMBOLS)
    first = chat.post(db, "hi")
    chat.claim(db)
    chat.answer(db, first, "Hello Robbie.")
    q = chat.post(db, "what did I send?")
    text = chat.prompt(db, chat.claim(db))
    assert "big5" in text and "Big-5 Trend" in text
    assert "buy AAPL" in text
    assert "OPERATOR: hi" in text and "VILLAGE: Hello Robbie." in text
    assert text.rstrip().endswith("OPERATOR: what did I send?\nVILLAGE:")
    assert "You are the Village" in text and "approve anything" in text
    assert q


def test_the_chat_never_reaches_the_money(db):
    from src.trading import chat

    q = chat.post(db, "buy 100 NVDA for big5 right now")
    chat.claim(db)
    chat.answer(db, q, "I can't trade.")
    for table in ("signals", "trade_proposals", "fills", "human_approvals"):
        assert db.query(f"SELECT * FROM {table}") == []


def test_signed_in_the_village_can_be_talked_to(hosted):
    access = importlib.import_module("src.access")

    assert hosted.get("/village/talk/messages").status_code == 403
    hosted.post(access.UNLOCK_PATH, data={access.FIELD: TOKEN, "next": "/village/talk"},
                follow_redirects=False)
    r = hosted.post("/village/actions/chat", data={"text": "hello village"},
                    headers={"X-Requested-With": "fetch"})
    assert r.json()["ok"]
    got = hosted.get("/village/talk/messages?after=0").json()
    assert got["waiting"] and got["messages"][0]["text"] == "hello village"
    page = hosted.get("/village/talk")
    assert "Talk to the village" in page.text and "hello village" in page.text
    assert "thinking" in page.text


def test_the_talk_page_lets_you_pick_who_you_talk_to(hosted):
    access = importlib.import_module("src.access")

    hosted.post(access.UNLOCK_PATH, data={access.FIELD: TOKEN, "next": "/village/talk"},
                follow_redirects=False)
    page = hosted.get("/village/talk").text
    assert "<option value='council'>The Council</option>" in page
    hosted.post("/village/actions/chat", data={"text": "how are we doing", "to": "council"},
                headers={"X-Requested-With": "fetch"})
    hosted.post("/village/actions/chat", data={"text": "hi", "to": "nobody_real"},
                headers={"X-Requested-With": "fetch"})
    got = hosted.get("/village/talk/messages?after=0").json()["messages"]
    assert [m["to"] for m in got] == ["the Council", ""]


def test_the_ideas_page_says_what_became_of_each_idea(hosted):
    access = importlib.import_module("src.access")

    assert "Sign in" in hosted.get("/village/ideas").text
    hosted.post(access.UNLOCK_PATH, data={access.FIELD: TOKEN, "next": "/village/ideas"},
                follow_redirects=False)
    assert "No ideas given yet" in hosted.get("/village/ideas").text
    from src.trading import ideas
    from src.trading.web import _inbox_db

    db = _inbox_db()
    try:
        ideas.give(db, "add options flow as a data source", "village")
    finally:
        db.close()
    page = hosted.get("/village/ideas").text
    assert "add options flow" in page and "NOT STARTED" in page
    assert "not reviewed yet" in page
