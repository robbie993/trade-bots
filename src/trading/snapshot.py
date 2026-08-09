"""The village as one file you can send to somebody.

``trade dashboard`` renders Mission Control and the village map into a single
self-contained HTML file: no server, no build step, no network, no CDN. Open
it by double-clicking it, attach it to an email, commit it to a repository,
put it in a bucket.

It is the same code that renders the live pages, run once and frozen, which is
the only reason it is worth having. A second dashboard written by hand would
be a second set of numbers to keep true; this one cannot drift from
``/village`` because it *is* ``/village``.

What freezing costs, and why the file says so out loud:

**The buttons are gone.** Not disabled — removed. A snapshot with a "Run a
tick" button that silently does nothing is worse than one with no button,
because it invites you to believe the page is live. Every ``<form>``,
``<button>`` and link out of the page is stripped, and the banner says where
the working controls are.

**The villagers are a recording.** The live page asks the database what has
happened since the last poll. A file has nothing to ask, so the events travel
inside it and replay on load. They are still real — every one is a row the
tick wrote — but they are a recording of a moment, and the banner dates it.

**It is a moment, not a feed.** The header carries the timestamp, the database
it came from and the data source, so a snapshot found six months later can be
placed. A number without its as-of is a rumour.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from ..agents.web import STYLE
from ..db.connection import utcnow_iso
from .web import (
    FLOW_CORE,
    FLOW_REPLAY,
    FLOW_STYLE,
    MISSION_FOOTER,
    _panel,
    _render,
    _village,
    e,
)

# Forms and buttons never nest in the HTML this codebase generates, so a
# non-greedy match is exact rather than approximate. The test asserts the
# result contains no interactive element at all, which is the property that
# actually matters.
_FORM = re.compile(r"<form\b.*?</form>", re.S)
_BUTTON = re.compile(r"<button\b.*?</button>", re.S)
_ANCHOR_OPEN = re.compile(r"<a\s+href=[^>]*>")


def freeze(body: str) -> str:
    """Strip everything that would need a server to mean anything."""
    body = _FORM.sub("", body)
    body = _BUTTON.sub("", body)
    # Links are unwrapped rather than removed: the firm names they wrapped are
    # still worth reading, they just no longer go anywhere.
    body = _ANCHOR_OPEN.sub("", body).replace("</a>", "")
    return body


def build(eco, events: int = 120, note: str = "") -> str:
    """One self-contained page: the panels, the map, and what was recorded."""
    firms = eco.store.firms()
    taken = utcnow_iso()
    recorded = eco.flow.recent(events)
    payload = [ev.to_dict() for ev in recorded]

    banner = _panel(
        "",
        "<h1>The Village — snapshot</h1>"
        f"<p class=muted>Taken {e(taken)} · {len(firms)} firm(s) · "
        f"data source: {e(eco.feed.name)} · database: {e(_db_label(eco))}</p>"
        + (f"<p><strong>{e(note)}</strong></p>" if note else "")
        + "<p class=muted>This is a still photograph. The buttons, the approval "
        "gate and the live village live on the running server "
        "(<code>python -m src.main serve</code>); nothing in this file can "
        "grant an approval or move a penny, because there is nothing here to "
        "move it with.</p>",
    )

    village = _panel("", _village(firms))
    history = "".join(
        f"<li class='kind-{e(ev.kind)}'>"
        f"{e(ev.firm + ' · ' if ev.firm else '')}{e(ev.label)}"
        f"{e(' — ' + ev.detail if ev.detail else '')}</li>"
        for ev in reversed(recorded)
    )

    body = "".join([
        f"<style>{FLOW_STYLE}</style>",
        banner,
        village,
        f"<p class=muted>The figures in the yard are the {len(firms)} firm(s) "
        "that existed when this was taken. Everyone walking a road is an event "
        "the tick recorded — a recording, replayed, not a live village.</p>",
        "<div class=card><button id=flow-replay>Replay</button> "
        f"<span class=muted id=flow-status>{len(payload)} recorded event(s)"
        "</span></div>",
        _panel("What had just happened", f"<ul id=flow-log>{history}</ul>"),
        freeze(_render(eco, "").replace(MISSION_FOOTER, "")),
    ])

    script = (
        f"const EVENTS = {json.dumps(payload)};\n" + FLOW_CORE + FLOW_REPLAY
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>The Village — snapshot {e(taken)}</title>"
        f"<style>{STYLE}</style></head><body>"
        f"{body}"
        "<footer class=muted>Rendered by <code>trade dashboard</code> from the "
        "same code that serves <code>/village</code>. Self-contained: no "
        "network, no scripts from anywhere else."
        f"</footer><script>{script}</script></body></html>"
    )


def write(eco, path: Path, events: int = 120, note: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(eco, events=events, note=note), encoding="utf-8")
    return path


def _db_label(eco) -> str:
    """The database, with any password in the URL left out of the file."""
    url: Optional[str] = getattr(eco.db, "url", "") or ""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://…@{rest.rpartition('@')[2]}"
    return url


__all__ = ["build", "freeze", "write"]
