"""What the village found outside itself — kept, not traded.

See migration 027. One row per (source, item_key); seeing the same thing again
moves `last_seen` and refreshes what was said about it, and never duplicates it.
"""

from __future__ import annotations

import json

from ..db.connection import utcnow_iso


def upsert(db, source: str, item_key: str, title: str = "", url: str = "",
           symbols=(), score=None, detail=None) -> None:
    now = utcnow_iso()
    values = {
        "title": str(title or "")[:500],
        "url": str(url or "")[:1000],
        "symbols": ",".join(str(s) for s in symbols or ()),
        "score": score,
        "detail": json.dumps(detail or {}),
        "last_seen": now,
    }
    row = db.query_one(
        "SELECT id FROM intel WHERE source = ? AND item_key = ?",
        (source, str(item_key)[:255]),
    )
    if row:
        db.update("intel", row["id"], values)
    else:
        db.insert("intel", {"source": source, "item_key": str(item_key)[:255],
                            "first_seen": now, **values})


def recent(db, source: str = "", limit: int = 20) -> list:
    try:
        if source:
            rows = db.query(
                "SELECT * FROM intel WHERE source = ? ORDER BY last_seen DESC, id DESC LIMIT ?",
                (source, limit))
        else:
            rows = db.query("SELECT * FROM intel ORDER BY last_seen DESC, id DESC LIMIT ?",
                            (limit,))
    except Exception:  # noqa: BLE001 - an unmigrated ledger has found nothing
        return []
    out = []
    for r in rows:
        try:
            r["detail"] = json.loads(r.get("detail") or "{}")
        except (TypeError, ValueError):
            r["detail"] = {}
        out.append(r)
    return out


def sources(db) -> list:
    try:
        return [r["source"] for r in db.query(
            "SELECT source, MAX(last_seen) AS seen FROM intel GROUP BY source ORDER BY seen DESC")]
    except Exception:  # noqa: BLE001
        return []


__all__ = ["recent", "sources", "upsert"]
