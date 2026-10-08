"""OPERATOR DESK — trades only what the operator told the village to, in the chat.

This firm has no strategy of its own. Its orders are the ones the operator gave
in the chat on the website (`/village/talk`) and confirmed with a tap; see
`src/trading/desk.py`. Each tick it hands over the confirmed orders whose
market is open, and the village reviews them exactly like any firm's: risk
limits, conscience, daily loss halt, paper venue, ledger.

It reads the order table itself because that is where the orders are; it does
not touch any other table, and it places nothing — the village does, or not.
"""
from __future__ import annotations


def propose(context):
    from src.config import Config
    from src.db.connection import Database
    from src.trading import desk

    db = Database.from_url(Config().database_url)
    try:
        return desk.take(db, context)
    finally:
        db.close()
