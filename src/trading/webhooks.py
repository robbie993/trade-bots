"""TradingView alerts, arriving as signals.

An alert fires on TradingView, TradingView posts JSON at a URL, and a firm that
listens to the ``signals`` analyst hears it on the next tick. That is the whole
feature, and the two words that decide its shape are *signal* and *authenticated*.

**A signal, not a strategy.** The obvious reading of "connect TradingView to the
village" is to submit each alert to the court as a strategy. That is the wrong
object: an alert is a symbol and a direction — "RSI crossed 30 on BTCUSD" — and
a strategy is a thing with a genome, a backtest and a track record. Submitting
alerts as strategies would produce a docket full of one-line cases that the
jury cannot evaluate and a firm roster full of things that are not firms.

An alert is exactly a **reading**, which the village already models: a score
from -100 to +100, a confidence, and a note, published by a named source and
heard by whoever is listening. So a TradingView alert becomes a publisher on
the signal board, alongside your own scanners. See ``src/trading/signals.py``.

The consequence worth stating plainly: **there is no path from this endpoint to
an order.** A reading joins one debate at one seat of one firm; the proposal
that results still meets the trader, the risk manager, the conscience, the
venue and the approval gate, none of which know a webhook exists. Somebody who
guesses the URL can add one voice to an argument. They cannot spend anything.

**Authenticated, with its own secret.** This is a POST that anybody on the
internet can reach, which is the exact shape of the thing the rest of this
repository refuses to have. Two rules make it acceptable:

* it carries its own token, ``MVV_WEBHOOK_TOKEN``, and **not** the console's.
  TradingView stores the alert body in plaintext in its own UI, and a secret
  that lives in somebody else's web form must not be the secret that unlocks
  your approval gate;
* with no token configured, the endpoint refuses everything. There is no
  unauthenticated mode to forget to turn off.

The staleness rule from the signal board applies unchanged: a reading is heard
on the bar it was published for and goes silent after. An alert that fired on
Tuesday does not vote on Thursday.
"""

# No `from __future__ import annotations` here, for the same reason as
# src/access.py: FastAPI resolves a handler's annotations against the *module*
# namespace, and the route below is defined inside `install()` with `Request`
# imported locally. Under postponed evaluation the annotation is the string
# "Request", resolves to nothing, and FastAPI decides `request` must be a query
# parameter — every alert then fails with a 422 about a missing field. The
# local import is what lets this module load without fastapi installed.

import hmac
import os
from typing import Optional

# The alert body may not be larger than this. TradingView sends a short JSON
# object; anything enormous is either a mistake or somebody probing.
MAX_BODY = 8192

PUBLISHER_PREFIX = "tradingview"

# What an alert's `action` may say, and which direction it means. Deliberately
# small: a word not on this list is refused rather than guessed at, because
# guessing the direction of a trade signal is the one thing worse than
# ignoring it.
ACTIONS = {
    "buy": 1, "long": 1, "bull": 1, "bullish": 1, "up": 1,
    "sell": -1, "short": -1, "bear": -1, "bearish": -1, "down": -1,
    "close": 0, "exit": 0, "flat": 0, "neutral": 0,
}

DEFAULT_SCORE = 70          # what "buy" means when no score is given
DEFAULT_CONFIDENCE = 60     # an alert is a real opinion, not a certainty


def token() -> str:
    return os.environ.get("MVV_WEBHOOK_TOKEN", "").strip()


def configured() -> bool:
    return len(token()) >= 16


def authorised(supplied: Optional[str]) -> bool:
    """Constant-time check. False whenever no token is configured."""
    if not configured():
        return False
    return hmac.compare_digest(str(supplied or "").strip(), token())


def reading_from(payload: dict) -> tuple:
    """An alert into (Reading, ""), or (None, why not).

    Accepts what TradingView alert bodies actually look like once somebody has
    written a JSON template into the message box:

        {"secret": "...", "symbol": "BTCUSD", "action": "buy"}
        {"secret": "...", "ticker": "AAPL", "action": "sell", "score": -40}
        {"secret": "...", "symbol": "SPY", "action": "buy",
         "confidence": 30, "note": "RSI crossed 30"}
    """
    from ..money import D
    from .signals import NOTE_LIMIT, Reading

    symbol = str(
        payload.get("symbol") or payload.get("ticker") or payload.get("asset") or ""
    ).upper().strip()
    if not symbol:
        return None, "no symbol"

    raw_action = str(payload.get("action") or payload.get("side") or "").lower().strip()
    if raw_action not in ACTIONS:
        return None, (
            f"action {raw_action!r} is not one of {', '.join(sorted(ACTIONS))}"
        )
    direction = ACTIONS[raw_action]

    # An explicit score wins; otherwise the action decides the sign and the
    # default decides the strength. `close` is a real position: no direction,
    # so it publishes a flat score rather than being dropped.
    if payload.get("score") is not None:
        try:
            score = D(payload["score"])
        except Exception:  # noqa: BLE001
            return None, f"score {payload['score']!r} is not a number"
    else:
        score = D(DEFAULT_SCORE * direction)

    confidence = payload.get("confidence")
    if confidence is None:
        confidence = DEFAULT_CONFIDENCE if direction else D(0)
    try:
        confidence = D(confidence)
    except Exception:  # noqa: BLE001
        return None, f"confidence {confidence!r} is not a number"

    note = str(payload.get("note") or payload.get("comment") or raw_action)[:NOTE_LIMIT]
    # Reading clamps nothing itself; the board and the Signal model do. Passing
    # the raw values on keeps one place responsible for the range.
    return Reading(symbol=symbol, score=score, confidence=confidence, note=note), ""


def publisher_for(payload: dict) -> str:
    """`tradingview` or `tradingview:<alert name>`, so several alerts can be
    told apart on the board without one drowning out another."""
    name = str(payload.get("strategy") or payload.get("name") or "").strip()
    clean = "".join(c for c in name if c.isalnum() or c in "-_")[:24]
    return f"{PUBLISHER_PREFIX}:{clean}" if clean else PUBLISHER_PREFIX


def install(app) -> None:
    """Mount ``POST /api/signals/tradingview``."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.post("/api/signals/tradingview")
    async def tradingview(request: Request):
        if not configured():
            return JSONResponse(
                {"error": "this deployment accepts no webhooks",
                 "fix": "set MVV_WEBHOOK_TOKEN (16+ characters) and redeploy"},
                status_code=503,
            )

        raw = await request.body()
        if len(raw) > MAX_BODY:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        try:
            import json

            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return JSONResponse(
                {"error": "body must be JSON",
                 "hint": 'put a JSON template in the alert message box, e.g. '
                         '{"secret":"...","symbol":"{{ticker}}","action":"buy"}'},
                status_code=400,
            )
        if not isinstance(payload, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

        # The secret may travel in the body (TradingView's alert box is the only
        # field most people have) or in a header, for anything that can send one.
        supplied = payload.get("secret") or request.headers.get("x-webhook-token")
        if not authorised(supplied):
            return JSONResponse({"error": "unauthorised"}, status_code=401)

        reading, why = reading_from(payload)
        if reading is None:
            return JSONResponse({"error": why}, status_code=400)

        from .web import ecosystem

        eco = ecosystem()
        try:
            as_of = eco.market().as_of()
            if as_of is None:
                return JSONResponse(
                    {"error": "the village has no market data, so a reading has no "
                              "bar to attach to"},
                    status_code=503,
                )
            publisher = publisher_for(payload)
            written = eco.signals.publish(publisher, [reading], as_of)
        finally:
            eco.db.close()

        return JSONResponse({
            "published": written,
            "publisher": publisher,
            "symbol": reading.symbol,
            "score": str(reading.score),
            "confidence": str(reading.confidence),
            "as_of": str(as_of),
            "heard_by": "firms listing the `signals` analyst, on this bar only",
        })


__all__ = [
    "ACTIONS", "DEFAULT_CONFIDENCE", "DEFAULT_SCORE", "MAX_BODY",
    "PUBLISHER_PREFIX", "authorised", "configured", "install", "publisher_for",
    "reading_from", "token",
]
