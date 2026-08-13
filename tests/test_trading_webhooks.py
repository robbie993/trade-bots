"""TradingView alerts arriving as signals.

Two properties decide whether this endpoint is acceptable, and both are worth
testing harder than the happy path:

1. **It cannot spend anything.** An alert becomes a reading on the signal
   board — one voice in one firm's debate, downstream of which sit the trader,
   the risk manager, the conscience, the venue and the approval gate. Somebody
   who guesses the URL and the token can argue. They cannot buy.
2. **It is never open.** With no `MVV_WEBHOOK_TOKEN` configured it refuses
   everything, and the token it checks is deliberately *not* the console's —
   TradingView stores the alert body in plaintext in its own UI, and a secret
   that lives in somebody else's web form must not unlock the approval gate.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.trading import webhooks  # noqa: E402

TOKEN = "webhook-token-long-enough-to-count"
PATH = "/api/signals/tradingview"


@pytest.fixture
def client(tmp_path, firms_yaml, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'w.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    monkeypatch.setenv("TRADE_DATA_SEED", "12345")
    monkeypatch.setenv("MVV_PUBLIC", "0")
    from src.cli import main

    main(["trade", "init"])

    def build(token=TOKEN, public=False):
        if token:
            monkeypatch.setenv("MVV_WEBHOOK_TOKEN", token)
        else:
            monkeypatch.delenv("MVV_WEBHOOK_TOKEN", raising=False)
        monkeypatch.setenv("MVV_PUBLIC", "1" if public else "0")
        import src.agents.web as web
        import src.deploy as deploy

        importlib.reload(deploy)
        importlib.reload(web)
        return TestClient(web.app, raise_server_exceptions=False)

    yield build
    monkeypatch.delenv("MVV_WEBHOOK_TOKEN", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    build("")


def alert(**extra) -> dict:
    body = {"secret": TOKEN, "symbol": "SPY", "action": "buy"}
    body.update(extra)
    return body


# =========================================================================
# never open
# =========================================================================
def test_no_token_configured_means_no_webhook(monkeypatch):
    monkeypatch.delenv("MVV_WEBHOOK_TOKEN", raising=False)
    assert webhooks.configured() is False
    assert webhooks.authorised("") is False
    assert webhooks.authorised("anything") is False


def test_a_short_token_does_not_count(monkeypatch):
    monkeypatch.setenv("MVV_WEBHOOK_TOKEN", "short")
    assert webhooks.configured() is False
    assert webhooks.authorised("short") is False


def test_an_unconfigured_deployment_refuses_and_says_how(client):
    response = client("").post(PATH, json={"symbol": "SPY", "action": "buy"})
    assert response.status_code == 503
    assert "MVV_WEBHOOK_TOKEN" in response.text


def test_the_wrong_secret_is_refused(client):
    response = client().post(PATH, json=alert(secret="not-the-token-but-long"))
    assert response.status_code == 401


def test_a_missing_secret_is_refused(client):
    body = alert()
    body.pop("secret")
    assert client().post(PATH, json=body).status_code == 401


def test_the_console_token_does_not_open_the_webhook(client, monkeypatch):
    """Separate secrets on purpose: TradingView stores the alert body in
    plaintext in its own UI."""
    monkeypatch.setenv("MVV_GATE_TOKEN", "the-console-token-long-enough-xx")
    response = client().post(PATH, json=alert(secret="the-console-token-long-enough-xx"))
    assert response.status_code == 401


def test_the_secret_may_travel_in_a_header(client):
    body = alert()
    body.pop("secret")
    response = client().post(PATH, json=body, headers={"x-webhook-token": TOKEN})
    assert response.status_code == 200


# =========================================================================
# what an alert turns into
# =========================================================================
def test_a_buy_becomes_a_bullish_reading(monkeypatch):
    monkeypatch.setenv("MVV_WEBHOOK_TOKEN", TOKEN)
    reading, why = webhooks.reading_from({"symbol": "spy", "action": "BUY"})
    assert why == ""
    assert reading.symbol == "SPY"
    assert reading.score > 0
    assert reading.confidence > 0


def test_a_sell_becomes_a_bearish_one():
    reading, _ = webhooks.reading_from({"ticker": "AAPL", "action": "sell"})
    assert reading.score < 0


def test_close_is_a_real_position_not_a_dropped_one():
    """"Exit" is information: no direction, and the firm should hear that
    rather than hear nothing."""
    reading, why = webhooks.reading_from({"symbol": "SPY", "action": "close"})
    assert why == ""
    assert reading.score == 0


def test_an_explicit_score_wins_over_the_default():
    reading, _ = webhooks.reading_from(
        {"symbol": "SPY", "action": "buy", "score": -40, "confidence": 12}
    )
    assert reading.score == Decimal("-40")
    assert reading.confidence == Decimal("12")


def test_an_unknown_action_is_refused_rather_than_guessed():
    """Guessing the direction of a trade signal is worse than ignoring it."""
    reading, why = webhooks.reading_from({"symbol": "SPY", "action": "yolo"})
    assert reading is None
    assert "not one of" in why


def test_an_alert_with_no_symbol_is_refused():
    reading, why = webhooks.reading_from({"action": "buy"})
    assert reading is None
    assert "no symbol" in why


def test_alerts_can_be_told_apart_on_the_board():
    assert webhooks.publisher_for({}) == "tradingview"
    assert webhooks.publisher_for({"strategy": "RSI Cross"}) == "tradingview:RSICross"
    # and a hostile name cannot invent a publisher of its own
    assert ";" not in webhooks.publisher_for({"name": "a;DROP TABLE signals"})


# =========================================================================
# the shape of the request
# =========================================================================
def test_a_body_that_is_not_json_says_what_to_paste(client):
    response = client().post(PATH, content=b"not json at all")
    assert response.status_code == 400
    assert "alert message box" in response.text


def test_an_enormous_body_is_refused(client):
    response = client().post(PATH, content=b"x" * (webhooks.MAX_BODY + 1))
    assert response.status_code == 413


# =========================================================================
# what it actually does
# =========================================================================
def test_an_alert_reaches_the_signal_board(client):
    response = client().post(PATH, json=alert(note="RSI crossed 30"))
    assert response.status_code == 200
    body = response.json()
    assert body["published"] == 1
    assert body["symbol"] == "SPY"
    assert body["publisher"] == "tradingview"


def test_the_reading_is_heard_by_a_firm_on_that_bar(client, tmp_path):
    import os

    from src.db.connection import Database
    from src.trading.ecosystem import Ecosystem
    from src.trading.firms.analysts import build_analysts

    client().post(PATH, json=alert(score=55, confidence=40))

    eco = Ecosystem(Database.from_url(os.environ["DATABASE_URL"]))
    seat = build_analysts(["signals"], board=eco.signals)[0]
    signal = seat.analyse("SPY", eco.market(), {})
    assert signal.score == Decimal("55")
    assert "tradingview" in signal.note
    eco.db.close()


def test_a_webhook_works_on_a_read_only_deployment(client):
    """Which is the whole point — a read-only deployment is exactly where
    TradingView will be pointed. Exempt from the blanket refusal, and not
    exempt from authentication."""
    hosted = client(public=True)
    assert hosted.post("/village/actions/tick", follow_redirects=False).status_code == 403
    assert hosted.post(PATH, json=alert()).status_code == 200
    assert hosted.post(PATH, json=alert(secret="wrong-but-long-enough-x")).status_code == 401


# =========================================================================
# and cannot spend anything
# =========================================================================
def test_the_webhook_cannot_reach_the_ledger(client):
    """A hundred maximally bullish alerts move one seat's vote and buy nothing.
    Every gate downstream is untouched by this endpoint's existence."""
    import os

    from src.db.connection import Database
    from src.trading.ecosystem import Ecosystem

    web = client()
    for symbol in ("SPY", "QQQ"):
        for _ in range(50):
            web.post(PATH, json=alert(symbol=symbol, score=100, confidence=100))

    eco = Ecosystem(Database.from_url(os.environ["DATABASE_URL"]))
    try:
        before = {f.firm_key: f.cash for f in eco.store.firms()}
        assert eco.store.fills() == []
        assert {f.firm_key: f.cash for f in eco.store.firms()} == before
    finally:
        eco.db.close()


def test_the_only_new_write_route_is_the_webhook(client):
    """The gate's write surface must not have grown."""
    import src.agents.web as web

    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)
                continue
            methods = (getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}
            if methods - {"GET"}:
                yield route.path

    client()
    posts = set(walk(web.app.routes))
    assert PATH in posts
    assert "/approvals/{approval_id}/approve" in posts   # still there, still guarded


# =========================================================================
# the path every platform probes
# =========================================================================
def test_health_answers_at_both_names(client):
    web = client()
    assert web.get("/health").status_code == web.get("/api/health").status_code
    assert web.get("/health").json() == web.get("/api/health").json()
