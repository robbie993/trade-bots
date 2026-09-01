"""Mission Control — the Village on one page.

The approval gate (``src/agents/web.py``) shows one thing: decisions waiting
for a human. Everything else the Village does — firms, kill switches, the
brokerage, the court, the arena, the market, the sandbox — lived only in the
CLI. This module is those six panels, mounted into the same app.

Server-rendered HTML, no build step, no framework, no external service. That
is not minimalism for its own sake: every number here is a `SELECT` away in
the same database the CLI reads, and a dashboard that needs a gateway, a
WebSocket bus and an npm install to display a table it could have rendered is
three more things that can be down when you want to know whether a firm is
bleeding.

**What the buttons may do.** The same as the CLI and no more. Ticking, running
a season, trying a strategy file, listing and buying with tokens — all
autonomous already. Anything that moves capital writes an approval request and
stops, exactly as it does from a terminal: this page has no privileged path
into the ledger, and a decision is granted at the gate at ``/`` or by
``mvv approve``.

The single exception is putting one firm on real money, which is decided on
its own confirmation page with every criterion on screen — see
``go_live_confirm``, which explains why that one is allowed to decide. It still
writes an approval row, and it still cannot promote a firm that has not earned
it. Coming *back* off real money was never an approval and still is not.

There is no authentication. Bind it to localhost.
"""

from __future__ import annotations

import html
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import Config
from ..db.connection import Database
from ..money import D, ZERO, fmt_money, fmt_pct, money
from ..notifications import build_notifier
from . import promotion
from .config import TradingConfig
from .data.feeds import build_feed
from .ecosystem import Ecosystem

router = APIRouter()

# Which broker a promotion names. The same default the CLI's `--venue` carries;
# there is one live venue wired up, and a page is the wrong place to be asked
# to choose between brokers.
LIVE_VENUE = "alpaca"

# Who the audit trail names when the switch on Mission Control is used. Not
# "web": an approval row for real money should say what kind of act produced
# it, and "somebody pressed the switch, with the criteria table in front of
# them" is a different act from "somebody ran `mvv approve 12`".
DECIDED_BY = "the switch on Mission Control"


#: One market feed for the whole process. See `ecosystem()`.
_FEED = None
_FEED_LOCK = threading.Lock()


def _shared_feed(config: TradingConfig):
    """The price feed, built once and kept.

    **The database is per-request; the feed must not be.** A fresh `Ecosystem`
    builds a fresh feed with an empty cache, and `/village` prices the entire
    36-symbol universe before it renders a byte. Paginated, at the feed's 0.35s
    politeness floor, that is twenty-five seconds of HTTP on a good run — and
    on a bad one it meets Alpaca's rate limit, sleeps two seconds per retry,
    and the page never returns at all. Mission Control was doing this on every
    single load, while competing with the tick loop for the same 200 requests
    a minute, so opening the page made the village's own prices worse.

    A feed is a safe thing to share: an HTTP client and a dict of bars, with
    its own one-bar TTL deciding when a symbol is refetched. Sharing it means
    the first page load after a bar turns over pays for the fetch and every
    other load is served from memory. The database connection stays per-request
    for the reason it always was — sqlite connections are not thread-safe.
    """
    global _FEED
    if _FEED is None:
        with _FEED_LOCK:
            if _FEED is None:       # re-check: another thread may have won
                _FEED = build_feed(config.data)
    return _FEED


def ecosystem() -> Ecosystem:
    """A fresh config/DB per request — no connection shared across threads."""
    app_config = Config()
    db = Database.from_url(app_config.database_url)
    config = TradingConfig()
    eco = Ecosystem(db, config, app_config, build_notifier(app_config))
    eco._feed = _shared_feed(config)
    return eco


def e(value) -> str:
    return html.escape(str(value))


def _table(rows: list, columns: Optional[list] = None) -> str:
    if not rows:
        return "<p class=muted>(none)</p>"
    columns = columns or list(rows[0].keys())
    head = "".join(f"<th>{e(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{r.get(c, '')}</td>" for c in columns) + "</tr>"
        for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _panel(title: str, body: str, actions: str = "") -> str:
    return f"<div class=card><h2>{e(title)}</h2>{body}{actions}</div>"


def _back(message: str = "") -> RedirectResponse:
    # quote(), not html.escape(): messages routinely contain "#" (listing and
    # approval ids), and in a URL that starts a fragment — the tail of the
    # message would silently vanish before it reached the page.
    target = f"/village?said={quote(message)}" if message else "/village"
    return RedirectResponse(target, status_code=303)


# =========================================================================
# the page
# =========================================================================
@router.get("/village", response_class=HTMLResponse)
def mission_control(request: Request) -> HTMLResponse:
    from ..agents.web import page

    eco = ecosystem()
    try:
        body = _render(eco, request.query_params.get("said", ""))
    finally:
        eco.db.close()
    return page("The Village — Mission Control", body)


def _render(eco: Ecosystem, said: str) -> str:
    firms = eco.store.firms()
    if not firms:
        return (
            "<h1>The Village — Mission Control</h1>"
            "<div class=card><p>No firms yet.</p>"
            "<p class=muted>Run <code>python -m src.main trade init</code> to create them "
            "from <code>config/firm_config.yaml</code>.</p></div>"
            "<p><a href='/'>&larr; approval gate</a></p>"
        )

    market = eco.market()
    cards = eco.brokerage.evaluator.evaluate_all(firms, market)
    by_id = {c.firm_id: c for c in cards}
    reconciliation = eco.brokerage.reconcile(market)
    pending = eco.gate.pending()

    equity = money(sum((D(c.equity) for c in cards), ZERO))
    capital = money(sum((D(f.allocation) for f in firms if not f.is_killed), ZERO))

    header = (
        "<h1>The Village — Mission Control</h1>"
        f"<p class=muted>{len(firms)} firm(s) · "
        f"{sum(1 for f in firms if f.is_active)} active, "
        f"{sum(1 for f in firms if f.status == 'paused')} paused, "
        f"{sum(1 for f in firms if f.is_killed)} killed · "
        f"as of {e(market.as_of())} · data: {e(eco.feed.name)}</p>"
    )
    if said:
        header += f"<div class=card><strong>{e(said)}</strong></div>"

    status_class = "good" if reconciliation.ok else "bad"
    header += _panel(
        "Ecosystem",
        _table([
            {
                "equity": fmt_money(equity),
                "capital deployed": fmt_money(capital),
                "reconciled": f"<span class={status_class}>"
                              f"{'yes' if reconciliation.ok else 'NO'}</span>",
                "pending approvals": (
                    f"<a href='/'>{len(pending)}</a>" if pending else "0"
                ),
            }
        ]),
        "<form method=post action='/village/actions/tick'>"
        "<button class=go>Run a tick</button></form>"
        "<form method=post action='/village/actions/apply-approvals'>"
        "<button>Carry out approved decisions</button></form>"
        "<a href='/village/flow'><button>Walk the village &rarr;</button></a>"
        "<a href='/village/solar'><button>Solar system &rarr;</button></a>",
    )
    if not reconciliation.ok:
        header += (
            "<div class='card alarm'><strong>The books do not reconcile.</strong>"
            f"<pre>{e(reconciliation.summary())}</pre>"
            "<p class=muted>The brokerage will refuse to score, kill or allocate "
            "until this is resolved. Nothing downstream of it runs.</p></div>"
        )

    return "".join([
        header,
        _firms_panel(eco, firms, by_id),
        _real_money_panel(eco, firms, by_id, reconciliation.ok),
        _brokerage_panel(eco, firms),
        _switches_panel(eco),
        _signals_panel(eco, market),
        _shadow_panel(eco),
        _council_panel(eco),
        _court_panel(eco),
        _arena_panel(eco),
        _market_panel(eco),
        _sandbox_panel(eco),
        MISSION_FOOTER,
    ])


# The line that closes Mission Control. Named because the snapshot exporter
# removes it by identity rather than by guessing at a regex: in a file with no
# server behind it, a link back to the approval gate goes nowhere.
MISSION_FOOTER = (
    "<p><a href='/'>&larr; approval gate</a> · "
    "<span class=muted>nothing on this page grants an approval, except putting "
    "one firm on real money — which is a decision about your own account, made "
    "with the evidence on screen, and is recorded as an approval either way"
    "</span></p>"
)


# =========================================================================
# panels
# =========================================================================
def _firms_panel(eco, firms, by_id) -> str:
    rows = []
    for firm in firms:
        card = by_id.get(firm.id)
        purse = eco.store.cash_view(firm, cash_floor_pct=eco.config.firm.cash_floor_pct)
        state = firm.status
        if card and not card.sufficient_data and not firm.is_killed:
            state = f"{state} <span class=muted>(measuring)</span>"
        if firm.status == "paused":
            # The one decision you could not make from the page you were
            # looking at: the CLI and the council could un-pause a firm, and
            # Mission Control could only watch it sit there.
            state += (
                f"<form method=post action='/village/actions/resume'>"
                f"<input type=hidden name=firm value='{e(firm.firm_key)}'>"
                f"<button class=go>Bring back</button></form>"
            )
        elif firm.is_killed:
            state += (
                " <span class=muted>(killed — a kill does not reverse; "
                "resubmit the strategy to the court to start it again)</span>"
            )
        rows.append({
            "firm": f"<a href='/village/firms/{e(firm.firm_key)}'>{e(firm.firm_key)}</a>",
            "state": state,
            "equity": fmt_money(card.equity) if card else "-",
            "return": _signed(card.return_pct) if card else "-",
            "drawdown": fmt_pct(card.drawdown_pct) if card else "-",
            "score": (f"{card.score}" if card and card.sufficient_data
                      else f"<span class=muted>({card.score})</span>" if card else "-"),
            "trades": card.closed_trades if card else 0,
            "cash": fmt_money(purse.withdrawable),
        })
    return _panel("Firms", _table(rows))


def _real_money_panel(eco, firms, by_id, reconciled: bool) -> str:
    """The one panel where a mistake costs money rather than opportunity.

    It is a *ledger* of who is on real money and a *request* form for who wants
    to be — not a switch. The asymmetry the whole feature is built on shows up
    here as two different-looking controls: going live is a grey button that
    files an approval and stops, coming back is a red one that acts
    immediately. A page that made those look alike would be lying about what
    they do.

    Every firm is listed with its first unmet criterion, because the useful
    question is not "which of these can go live" (usually none) but "what does
    this one still have to show me".
    """
    feed_name = getattr(eco.feed, "name", "")
    live = [f for f in firms if f.venue != "paper"]

    rows = []
    for firm in firms:
        verdict = promotion.assess(
            eco.store, firm, by_id.get(firm.id), feed_name,
            promotion.LiveReadiness(), reconciled,
        )
        on_live = firm.venue != "paper"
        if on_live:
            where = f"<span class=bad>{e(firm.venue)} — REAL MONEY</span>"
            button = (
                f"<form method=post action='/village/actions/to-paper'>"
                f"<input type=hidden name=firm value='{e(firm.firm_key)}'>"
                f"<button class=kill>Back to paper</button></form>"
            )
            standing = f"running {fmt_money(firm.cash)}"
        else:
            where = "<span class=muted>paper</span>"
            if verdict.ready:
                # A link, not a form: the press that matters is on the next
                # page, under the evidence. This one only opens it.
                button = (f"<a href='/village/live/{e(firm.firm_key)}'>"
                          f"<button class=go>Go live</button></a>")
                standing = (f"<span class=good>every criterion met</span> — would "
                            f"start at {fmt_money(verdict.start_capital)}")
            else:
                button = "<span class=muted>—</span>"
                first = verdict.failures[0]
                standing = (f"{len(verdict.failures)} of {len(verdict.checks)} unmet · "
                            f"next: {e(first.name)} is {e(first.value)}, "
                            f"needs {e(first.needs)}")
        rows.append({
            "firm": f"<a href='/village/firms/{e(firm.firm_key)}'>{e(firm.firm_key)}</a>",
            "trading": where,
            "standing": standing,
            "": button,
        })

    panic = ""
    if live:
        panic = (
            "<form method=post action='/village/actions/all-to-paper'>"
            "<button class=kill>Pull EVERYTHING back to paper</button></form>"
        )

    note = (
        "<p class=muted>Going live takes evidence and a human. <em>Go live</em> "
        "opens the criteria table with one button under it — a firm that has not "
        "earned it has no button at all, here or anywhere. Coming back takes "
        "neither: press <em>Back to paper</em> and it is done, and the tick "
        "returns a firm on its own the moment its book cannot be valued, it "
        "stops being active, or it draws down past the kill limit. Nothing here "
        "ever sells a real position; that is what turns an outage into a "
        "realised loss.</p>"
        f"<p class=muted>Evidence is being gathered against <strong>{e(feed_name)}</strong>. "
        + ("A record on invented data is not evidence about the world, so nothing "
           "can qualify while this reads <em>synthetic</em>."
           if any(bad in feed_name.lower() for bad in promotion.INVENTED_FEEDS)
           else "A record gathered on one feed does not qualify a firm on another.")
        + "</p>"
    )
    title = (f"Real money — {len(live)} firm(s) LIVE" if live
             else "Real money — nothing is live")
    return _panel(title, _table(rows) + note, panic)


def _brokerage_panel(eco, firms) -> str:
    rows = [{
        "firm": e(f.firm_key),
        "initial": fmt_money(f.initial_allocation),
        "allocation": fmt_money(f.allocation),
        "returned": fmt_money(max(D(0), D(f.initial_allocation) - D(f.allocation))),
        "vs initial": _signed(
            (D(f.allocation) - D(f.initial_allocation)) / D(f.initial_allocation) * D(100)
        ) if f.initial_allocation else "-",
    } for f in firms]
    events = eco.store.events(limit=6)
    recent = "".join(
        f"<li class=muted>{e(ev.get('event_type'))}: {e((ev.get('detail') or '')[:110])}</li>"
        for ev in events
    )
    return _panel(
        "Brokerage",
        _table(rows)
        + (f"<ul>{recent}</ul>" if recent else "")
        + "<p class=muted>Capital is cut automatically and raised only by an approval.</p>",
    )


def _switches_panel(eco) -> str:
    """The controls on the wall: what is running, and what you can stop.

    These are switches rather than settings because they take effect on the
    next tick of a process that is not this one — they are written to the
    database, and the loop reads them each pass. Flipping one here stops or
    starts something already running, without a restart and without a terminal.
    """
    default = eco.config.living.enabled
    paused = eco.settings.get("paused", default=False)

    def switch(name: str, label: str, about: str, on: bool,
               words: tuple, action: tuple) -> str:
        """One row: what it is, whether it is on, and the button to flip it.

        `action` is the button's label in each state rather than a generic
        "Turn off", because "Pause" and "Close the bazaar" are what you are
        actually about to do, and a control should say so.
        """
        state = (f"<span class=good>{words[0]}</span>" if on
                 else f"<span class=muted>{words[1]}</span>")
        return (
            f"<tr><td><strong>{e(label)}</strong><br>"
            f"<span class=muted>{e(about)}</span></td>"
            f"<td>{state}</td><td>"
            f"<form method=post action='/village/actions/switch'>"
            f"<input type=hidden name=name value='{e(name)}'>"
            f"<button class={'kill' if on else 'go'}>"
            f"{e(action[0] if on else action[1])}</button></form></td></tr>"
        )

    body = (
        "<table>"
        # Inverted on purpose: the stored switch is `paused`, but what a reader
        # wants to see is whether the village is running.
        + switch("paused", "The village",
                 "trading, scoring, the council — the whole loop",
                 not paused, ("running", "paused"), ("Pause", "Start again"))
        + switch("arena", "Arena", "seasons of head-to-head, and titles",
                 eco.settings.get("arena", default),
                 ("open", "closed"), ("Close", "Open"))
        + switch("bazaar", "Bazaar", "idle firms sell the books they are not using",
                 eco.settings.get("bazaar", default),
                 ("open", "closed"), ("Close", "Open"))
        + switch("tavern", "Tavern", "alliances form, and rivals scheme",
                 eco.settings.get("tavern", default),
                 ("open", "closed"), ("Close", "Open"))
        # Deliberately last and deliberately not defaulted from `living`: this
        # is the only switch on the wall that changes what the firms *do*.
        + switch("evolution", "Evolution",
                 "firms mutate their own genomes and keep what survives "
                 "bars it was not fitted on",
                 eco.settings.get("evolution", default=False),
                 ("learning", "frozen"), ("Freeze", "Start learning"))
        + "</table>"
        "<p class=muted>These reach the background loop, not this page: they are "
        "stored in the database and read on every tick, so a switch you flip is "
        "already in effect. Pausing stops the village trading; it does not close "
        "the gate, and nothing already approved is undone.</p>"
    )
    return _panel("Switches", body)


def _signals_panel(eco, market) -> str:
    """What the scanners published, and whether anyone is still hearing it.

    The one question this panel exists to answer is "is my screener actually
    doing anything", which has two halves people conflate: did it publish, and
    is it publishing for *this* bar. A reading from an earlier bar is shown and
    marked stale, because no firm is hearing it — see src/trading/signals.py.
    """
    from .signals import stamp

    specs = eco.scanners.specs
    if not specs and not eco.scanners.error:
        return ""                            # a village with no scanners: no panel

    now = stamp(market.as_of())

    # **One table per publisher, and the whole day in each.** This was a single
    # twelve-row list with every source mixed together and no clock on it, so a
    # headline, a moving average and a warning from a dead firm arrived
    # indistinguishable and most of the day's readings were simply off the
    # bottom. Each source now gets its own scrolling table, newest first, with
    # the time it was published — which is the only way to look at a day's
    # picks and see what a source actually said and when.
    blocks = []
    for pub in eco.signals.publishers():
        rows = []
        for row in eco.signals.recent(limit=400, publisher=pub):
            symbol = str(row["symbol"] or "")
            if not symbol:
                continue                 # the silence marker; see mark_silent
            fresh = str(row["as_of"]) == now
            score = D(row["score"] or 0)
            rows.append({
                "at": e(str(row["as_of"] or "")[11:16]),
                "symbol": e(symbol),
                "score": f"<span class={'good' if score > 0 else 'warn'}>"
                         f"{score:+.2f}</span>",
                "conf": f"{D(row['confidence'] or 0):.2f}",
                # "this bar", not "now": a reading is fresh for the bar it was
                # stamped with, and the whole staleness rule is stated in bars.
                "heard": ("<span class=good>this bar</span>" if fresh
                          else "<span class=muted>stale</span>"),
                "why": e(str(row["note"] or "")[:70]),
            })
        if not rows:
            continue
        blocks.append(
            f"<h3 style='margin:1rem 0 .25rem;font-size:.95rem'>{e(pub)} "
            f"<span class=muted>— {len(rows)} reading(s)</span></h3>"
            "<div style='max-height:16rem;overflow-y:auto;border:1px solid var(--line);"
            "border-radius:6px'>" + _table(rows) + "</div>"
        )

    # Who is listening, counting seats wherever they live. Heirs carry theirs in
    # the genome rather than the YAML, so reading only the spec reported six
    # firms as deaf while they were in fact hearing every word.
    heard_by = {"signals", "news", "scribe"}
    listening = []
    for firm in eco.store.firms():
        spec = eco.specs().get(firm.firm_key)
        seats = {str(a).strip().lower()
                 for a in (getattr(spec, "analysts", None)
                           or (firm.genome or {}).get("analysts", ()) or ())}
        if seats & heard_by and not firm.is_killed:
            listening.append(firm.firm_key)
    listening = sorted(listening)
    configured = ", ".join(
        f"<strong>{e(s.name)}</strong>" + ("" if s.enabled else " (off)") for s in specs
    ) or "none"

    note = (
        f"<p class=muted>Scanners: {configured}. "
        + (f"Heard by {e(', '.join(listening))}." if listening else
           "<strong>Nobody is listening</strong> — add <code>signals</code> to a "
           "firm's analysts in the firm config.")
        + " A scanner publishes a score and nothing else: it joins one debate at "
        "one seat, and the proposal that results still meets the risk manager, "
        "the conscience and the gate. It cannot move money.</p>"
    )
    if eco.scanners.error:
        note = f"<p class=bad>{e(eco.scanners.error)}</p>" + note
    body = "".join(blocks) or "<p class=muted>(nothing published yet)</p>"
    return _panel("What the scanners see", body + note)



def _shadow_panel(eco) -> str:
    """The options desk: what the account really did, and what the desk imagines.

    Kept visibly separate. The real rows are executed Alpaca fills; the shadow
    rows are a research desk that has never risked anything. Presenting them in
    one number would be part evidence and part invention, and the whole reason
    this desk exists is that the village had no executed option trades to learn
    from at all.
    """
    try:
        rows = eco.db.query(
            "SELECT source, arm, contract, underlying, side, entry_price, "
            "exit_price, realized, spread_pct, closed_at, opened_bar "
            "FROM shadow_trades ORDER BY id DESC LIMIT 400") or []
    except Exception:  # noqa: BLE001 - no table yet is not an error
        return ""
    if not rows:
        return ""

    def _score(source):
        done = [r for r in rows if r["source"] == source and r["closed_at"]]
        if not done:
            return 0, ZERO, 0.0
        vals = [D(str(r["realized"] or 0)) for r in done]
        wins = sum(1 for v in vals if v > 0)
        return len(vals), money(sum(vals, ZERO)), round(100.0 * wins / len(vals), 1)

    cards = []
    for source, label, note in (
        ("real", "Real — Alpaca", "executed, in the account"),
        ("shadow", "Shadow — village", "never risked a cent"),
    ):
        n, net, win = _score(source)
        colour = "good" if net > 0 else ("bad" if net < 0 else "muted")
        cards.append(
            f"<div class=card><strong>{label}</strong><br>"
            f"<span class={colour} style='font-size:1.4rem'>{fmt_money(net)}</span>"
            f"<br><span class=muted>{n} closed · {win}% won · {e(note)}</span></div>")

    # Which genome is winning. `n` is beside the money on purpose: this village
    # has already been fooled by a 100% win rate over 13 short-premium trades,
    # which is the expected shape rather than an edge.
    arms: dict = {}
    for r in rows:
        if r["source"] != "shadow" or not r["closed_at"]:
            continue
        arms.setdefault(str(r["arm"]), []).append(D(str(r["realized"] or 0)))
    arm_rows = []
    for arm, vals in sorted(arms.items(), key=lambda kv: -sum(kv[1], ZERO)):
        wins = sum(1 for v in vals if v > 0)
        arm_rows.append({
            "arm": e(arm), "trades": len(vals),
            "net": fmt_money(money(sum(vals, ZERO))),
            "mean": fmt_money(money(sum(vals, ZERO) / D(len(vals)))),
            "won": f"{round(100.0*wins/len(vals),1)}%",
            "verdict": ("<span class=muted>too few to rank</span>"
                        if len(vals) < 20 else "<span class=good>ranked</span>"),
        })

    open_rows = [{
        "source": e(str(r["source"])), "arm": e(str(r["arm"])),
        "contract": e(str(r["contract"])), "side": e(str(r["side"])),
        "at": e(str(r["entry_price"])),
        "spread": f"{D(str(r['spread_pct'] or 0)):.1f}%",
        "since": e(str(r["opened_bar"] or "")[:16]),
    } for r in rows if not r["closed_at"]][:12]

    body = "".join(cards)
    if arm_rows:
        body += ("<h3 style='margin:1rem 0 .25rem;font-size:.95rem'>Genomes, "
                 "graded against each other</h3>" + _table(arm_rows))
    if open_rows:
        body += ("<h3 style='margin:1rem 0 .25rem;font-size:.95rem'>Open</h3>"
                 + _table(open_rows))
    body += (
        "<p class=muted>The desk writes puts on paper and cannot reach the "
        "ledger — it has its own table and never touches cash, fills or "
        "positions. It fills at the <strong>bid</strong> and buys back at the "
        "<strong>ask</strong>, because a seller who books the mid invents the "
        "edge he is testing. Several genomes are graded on the same chain every "
        "bar, and none is promoted under 20 trades: short premium wins most of "
        "the time by construction, so a young win rate is a shape and not a "
        "result.</p>")
    return _panel("Options desk — real against imagined", body)

def _council_panel(eco) -> str:
    """What the village decided for itself — and what it handed back to you."""
    autonomy = eco.config.autonomy
    rows = [{
        "id": f"#{r['id']}",
        "verdict": _verdict(r.get("verdict")),
        "action": e(r.get("action") or ""),
        "firm": e(r.get("firm_key") or "-"),
        "for": r.get("for_weight"),
        "against": r.get("against_weight"),
        "reason": e((r.get("reason") or "")[:70]),
    } for r in eco.council.recent(8)]

    if not autonomy.council_decides:
        note = (
            "<p class=muted>The council is not sitting. Every kill, raise and "
            "resume waits for you at the <a href='/'>approval gate</a>. "
            "Start it with <code>TRADE_AUTONOMY=council</code>.</p>"
        )
    else:
        note = (
            "<p class=muted>The council rules from the ledger, not from the "
            "request: it grants, refuses, or <strong>defers</strong>. A deferred "
            "decision is still pending for you, which is the point — autonomy "
            "narrows what you are asked about rather than removing you. "
            "It has no panel for live trading, and never will.</p>"
        )
    return _panel("The council", _table(rows) + note)


def _verdict(value) -> str:
    css = {"grant": "good", "refuse": "warn", "defer": "muted"}.get(value or "", "muted")
    return f"<span class={css}>{e(value or '')}</span>"


def _court_panel(eco) -> str:
    rows = [{
        "id": f"<a href='/village/court/{r['id']}'>#{r['id']}</a>",
        "file": e((r.get("file_name") or "")[:30]),
        "ruling": _ruling(r.get("ruling")),
        "confidence": r.get("confidence"),
        "fitness": r.get("fitness") if r.get("fitness") is not None else "-",
        "candidate": r.get("genome_id") or "-",
    } for r in eco.court.docket(8)]
    upload = (
        "<form method=post action='/village/actions/court-submit' "
        "enctype='multipart/form-data'>"
        "<input type=file name=file accept='.py,.yaml,.yml,.json' required>"
        "<button class=go>Put it on trial</button></form>"
        "<form method=post action='/village/actions/recruit' "
        "enctype='multipart/form-data'>"
        "<input type=file name=file accept='.py,.yaml,.yml,.json' required>"
        "<button>Recruit it as a firm</button></form>"
    )
    return _panel(
        "Strategy court",
        _table(rows)
        + "<p class=muted>The file is read, never executed. <strong>Put it on "
        "trial</strong> admits an <em>unselected</em> candidate genome — nothing "
        "trades because the court liked it. <strong>Recruit it</strong> goes "
        "further: a cleared file becomes a firm of its own, created paused and "
        "holding nothing, with one approval for its capital.</p>",
        upload,
    )


def _arena_panel(eco) -> str:
    rows = [{
        "#": r["rank"],
        "firm": e(r["firm"]),
        "tokens": r["tokens"],
        "title": e(r["title"]),
        "reputation": r["reputation"],
    } for r in eco.arena.standings()]
    bouts = "".join(
        f"<li class=muted>{e(b.get('winner') or 'no contest')}: "
        f"{e(b.get('challenger'))} vs {e(b.get('opponent'))} on {e(b.get('metric'))}"
        f"{'' if b.get('winner') else ' — ' + e(b.get('reason') or '')}</li>"
        for b in eco.arena.bouts(5)
    )
    return _panel(
        "Competition",
        _table(rows)
        + (f"<ul>{bouts}</ul>" if bouts else "")
        + "<p class=muted>Tokens are points. Nothing converts them into capital.</p>",
        "<form method=post action='/village/actions/season'>"
        "<button class=go>Run a season</button></form>",
    )


def _market_panel(eco) -> str:
    listings = eco.black_market.listings("active")
    rows = [{
        "id": listing.id,
        "seller": e(listing.seller),
        "asset": e(listing.asset_type),
        "title": e(listing.title[:26]),
        "price": f"{listing.price} {listing.currency}",
        "buy": (
            "<form method=post action='/village/actions/market-buy'>"
            f"<input type=hidden name=listing value='{listing.id}'>"
            "<input name=buyer placeholder='buyer' size=10 required>"
            "<button>Buy</button></form>"
        ),
    } for listing in listings]

    pending = eco.black_market.pending_transfers()
    transfers = "".join(
        f"<li><strong>{e(row['seller'])} &rarr; {e(row['buyer'])}</strong> "
        f"{fmt_money(D(row['price']))} — awaiting approval "
        f"<a href='/'>#{row['approval_id']}</a> "
        f"<form method=post action='/village/actions/market-settle'>"
        f"<input type=hidden name=transaction value='{row['id']}'>"
        f"<button>Settle if approved</button></form></li>"
        for row in pending
    )

    sell = (
        "<form method=post action='/village/actions/market-sell'>"
        "<input name=seller placeholder='seller' size=10 required>"
        "<select name=asset>"
        "<option>genome</option><option>data</option>"
        "<option>compute</option><option>capital</option></select>"
        "<input name=price placeholder='price' size=8 required>"
        "<button>List it</button></form>"
    )
    return _panel(
        "Black market",
        _table(rows)
        + (f"<ul>{transfers}</ul>" if transfers else "")
        + "<p class=muted>Genomes, data and compute settle in tokens. Capital does "
        "not settle here: a sale is a cut plus a raise, and a raise needs you.</p>",
        sell,
    )


def _sandbox_panel(eco) -> str:
    rows = [{
        "firm": e(r["firm"]),
        "shadow equity": fmt_money(r["shadow_equity"]),
        "tokens": r["tokens"],
        "reputation": r["reputation"],
        "alliances": r["alliances"],
    } for r in eco.sandbox.scoreboard()]
    alliances = "".join(
        f"<li class=muted>{e(str(a))}</li>" for a in eco.sandbox.alliances.all()
    )
    events = "".join(
        f"<li class=muted>[{e(ev['event_type'])}] {e(ev['actor'])} &rarr; "
        f"{e(ev['target'] or '-')}: {e((ev['detail'] or '')[:100])}</li>"
        for ev in eco.sandbox.events(6)
    )
    actions = (
        "<form method=post action='/village/actions/sandbox'>"
        "<input type=hidden name=action value='form'>"
        "<input name=name placeholder='alliance name' size=14 required>"
        "<input name=actor placeholder='founder' size=10 required>"
        "<input name=target placeholder='members, comma separated' size=22 required>"
        "<button>Form</button></form>"
        "<form method=post action='/village/actions/sandbox'>"
        "<input type=hidden name=action value='betray'>"
        "<input name=actor placeholder='traitor' size=10 required>"
        "<input name=name placeholder='alliance' size=14 required>"
        "<button class=kill>Betray</button></form>"
        "<form method=post action='/village/actions/sandbox'>"
        "<input type=hidden name=action value='spy'>"
        "<input name=actor placeholder='spy' size=10 required>"
        "<input name=target placeholder='target' size=10 required>"
        "<button>Spy</button></form>"
        "<form method=post action='/village/actions/sandbox'>"
        "<input type=hidden name=action value='sabotage'>"
        "<input name=actor placeholder='saboteur' size=10 required>"
        "<input name=target placeholder='target' size=10 required>"
        "<button class=kill>Sabotage</button></form>"
    )
    return _panel(
        "Sandbox",
        _table(rows)
        + (f"<ul>{alliances}</ul>" if alliances else "")
        + (f"<ul>{events}</ul>" if events else "")
        + "<p class=muted>Shadow equity is the sandbox's own scoreboard. Sabotage "
        "moves it and never the real ledger — a drawdown has to stay a fact about "
        "that firm's own strategy.</p>",
        actions,
    )


def _signed(value) -> str:
    number = D(value)
    css = "good" if number > 0 else "bad" if number < 0 else "muted"
    return f"<span class={css}>{fmt_pct(number)}</span>"


def _ruling(verdict) -> str:
    css = {"accept": "good", "reject": "bad", "modify": "warn"}.get(verdict or "", "muted")
    return f"<span class={css}>{e((verdict or '-').upper())}</span>"


# =========================================================================
# detail pages
# =========================================================================
@router.get("/village/firms/{firm_key}", response_class=HTMLResponse)
def firm_detail(firm_key: str) -> HTMLResponse:
    from ..agents.web import page
    from .firms.kill_switch import kill_check_table

    eco = ecosystem()
    try:
        firm = eco.store.get_firm(firm_key)
        if firm is None:
            return page("Unknown firm", f"<h1>No firm {e(firm_key)}</h1>"
                                        "<p><a href='/village'>&larr; back</a></p>")
        market = eco.market()
        card = eco.brokerage.evaluator.evaluate(firm, market)
        purse = eco.store.cash_view(firm, market, eco.config.firm.cash_floor_pct)

        checks = [
            {
                "condition": row["condition"],
                "value": row["value"],
                "kills when": row["kill_when"],
                "triggered": ("<span class=bad>YES</span>" if row["triggered"] else "no"),
            }
            for row in kill_check_table(card.to_metrics(), eco.config.kill)
        ]
        positions = [
            {
                "symbol": e(p.symbol),
                "quantity": p.quantity,
                "avg price": p.avg_price,
                "mark": market.mark(p.symbol),
                "value": fmt_money(p.market_value(market.mark(p.symbol))),
                "unrealised": _money_signed(p.unrealized_pnl(market.mark(p.symbol))),
            }
            for p in eco.store.positions(firm.id)
        ]
        proposals = [
            {
                "id": p.id,
                "symbol": e(p.symbol),
                "side": e(p.side),
                "qty": p.quantity,
                "confidence": p.confidence,
                "risk": e(p.risk_verdict),
                "ethics": e(p.ethics_verdict),
                "status": e(p.status),
            }
            for p in eco.store.proposals(firm.id, limit=12)
        ]

        body = "".join([
            f"<h1>{e(firm.firm_key)} — {e(firm.name)}</h1>",
            f"<p class=muted>{e(firm.asset_class or '-')} · {e(firm.strategy or '-')} · "
            f"venue {e(firm.venue)} · <strong>{e(firm.status)}</strong></p>",
            _panel("Capital", _table([{
                "allocation": fmt_money(firm.allocation),
                "cash": fmt_money(purse.cash),
                "available": fmt_money(purse.available),
                "reserve": fmt_money(purse.reserve),
                "in positions": fmt_money(purse.in_positions),
                "equity": fmt_money(card.equity),
                "return": _signed(card.return_pct),
            }])),
            _panel("Kill conditions", _table(checks)
                   + ("" if card.sufficient_data else
                      "<p class=muted>Below the sample gate: win rate and Sharpe are not "
                      "a verdict yet.</p>")),
            _panel("Positions", _table(positions)),
            _panel("Recent proposals", _table(proposals)
                   + "<p class=muted>Blocked proposals are stored too — "
                   "&ldquo;why didn't it?&rdquo; is answerable from a row.</p>"),
            f"<p class=muted>genome: <code>{e(firm.genome)}</code></p>",
            "<p><a href='/village'>&larr; Mission Control</a> · "
        "<a href='/village/solar'>the same firms as a solar system</a></p>",
        ])
    finally:
        eco.db.close()
    return page(f"{firm_key} — the Village", body)


@router.get("/village/court/{case_id}", response_class=HTMLResponse)
def court_case(case_id: int) -> HTMLResponse:
    from ..agents.web import page
    import json

    eco = ecosystem()
    try:
        row = eco.court.case(case_id)
        if row is None:
            return page("Unknown case", f"<h1>No case {case_id}</h1>"
                                        "<p><a href='/village'>&larr; back</a></p>")
        try:
            findings = json.loads(row.get("findings") or "[]")
        except ValueError:
            findings = []
        table = _table([
            {
                "juror": e(f["juror"]),
                "verdict": _verdict(f["verdict"]),
                "weight": f["weight"],
                "veto": "<span class=bad>VETO</span>" if f.get("veto") else "",
                "reason": e(f["reason"]),
            }
            for f in findings
        ])
        body = "".join([
            f"<h1>Case #{row['id']} — {e(row['file_name'])}</h1>",
            f"<p class=muted>sha256 {e((row['file_sha256'] or '')[:32])}… · "
            f"{row['file_bytes']} bytes · submitted by {e(row['submitted_by'] or '-')}</p>",
            _panel(
                f"Ruling: {(row['ruling'] or '').upper()}",
                f"<p>{e(row['ruling_reason'])}</p>"
                f"<p class=muted>confidence {row['confidence']}</p>",
            ),
            _panel("The jury", table),
            _panel("Prosecution", f"<pre>{e(row.get('prosecution') or '')}</pre>"),
            _panel("Defence", f"<pre>{e(row.get('defence') or '')}</pre>"),
            "<p><a href='/village'>&larr; Mission Control</a></p>",
        ])
    finally:
        eco.db.close()
    return page(f"Case #{case_id} — the Village", body)


def _verdict(value: str) -> str:
    css = {"for": "good", "against": "bad"}.get(value, "muted")
    return f"<span class={css}>{e(value)}</span>"


def _money_signed(value) -> str:
    number = D(value)
    css = "good" if number > 0 else "bad" if number < 0 else "muted"
    return f"<span class={css}>{fmt_money(number)}</span>"


# =========================================================================
# actions
#
# Every one of these does exactly what the equivalent CLI command does, and
# nothing more. The ones that would move capital write an approval request
# and stop; this page cannot grant one.
# =========================================================================
@router.post("/village/actions/tick")
def action_tick() -> RedirectResponse:
    """One tick, unless the background loop is already running this village.

    **Two processes must never tick the same village.** On 2026-09-01 a `serve`
    process eighteen hours old and a browser tab left on "Let it run" drove
    35,637 ticks through this endpoint at one every 2.5 seconds, while the
    background loop ticked every 60. Because the web process was running the
    code it had been started with, every fix deployed that day was undone on
    the next web tick — a Sharpe sample gate worked perfectly in the loop while
    this endpoint kept writing the artifact it existed to stop, and wound up
    the best firm in the village on it. It is also the only good explanation
    for two torn ledgers, because a torn write needs a second writer.

    The auto-tick already refused to overlap *itself*, on exactly this
    reasoning. It simply had no way to see the other process. Now it does.
    """
    from .heartbeat import running_elsewhere

    other = running_elsewhere()
    if other is not None:
        return _back(
            f"not ticked — the background loop (pid {other['pid']}) is running "
            f"this village and last ticked {other['age_s']}s ago. Two processes "
            "ticking one ledger is how the books stop adding up."
        )
    eco = ecosystem()
    try:
        report = eco.tick()
        said = (
            f"tick: {report.proposals} proposal(s), {report.filled} filled, "
            f"{report.blocked_by_risk} blocked by risk, "
            f"{report.blocked_by_ethics} blocked by ethics"
        )
        if report.errors:
            said += f" — {report.errors[0][:120]}"
    except Exception as exc:  # noqa: BLE001 - a failed tick is a message, not a 500
        said = f"tick failed: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/resume")
def action_resume(firm: str = Form(...)) -> RedirectResponse:
    """Un-pause a firm. A human decision, and only ever a human decision.

    `resume_firm` refuses a killed firm, and that refusal is left alone. A
    pause is the system saying "this tripped a limit, look at it"; a kill is
    the answer to having looked. Reversing the second from a web button would
    make the kill switch a suggestion.
    """
    eco = ecosystem()
    try:
        result = eco.brokerage.resume_firm(firm, "web")
        said = f"{result['firm']} is active again"
        eco.flow.emit("brokerage", f"{firm} brought back", firm=firm,
                      detail="resumed by a human from Mission Control")
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/switch")
def action_switch(name: str = Form(...)) -> RedirectResponse:
    """Flip one of the wall switches — a quarter, or the whole loop.

    Written to the database rather than the environment because the process
    that ticks is not the process serving this page. See src/trading/settings.py.
    """
    from .settings import UnknownSetting

    eco = ecosystem()
    try:
        default = eco.config.living.enabled if name != "paused" else False
        now_on = eco.settings.toggle(name, default=default, by="web")
        if name == "paused":
            said = "the village is paused" if now_on else "the village is running again"
        else:
            said = f"the {name} is {'open' if now_on else 'closed'}"
    except UnknownSetting:
        said = f"refused: there is no switch called {name!r}"
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/apply-approvals")
def action_apply_approvals() -> RedirectResponse:
    eco = ecosystem()
    try:
        applied = eco.apply_approvals()
        said = "; ".join(applied) if applied else "nothing approved is waiting"
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


def _readiness(eco, firm_key: str):
    """The firm, its scorecard and its verdict, as of right now.

    Always recomputed at the moment it is needed and never carried over from
    the page that drew a button, because that page was rendered at some point
    in the past and the firm has been trading since.
    """
    record = eco.store.get_firm(firm_key)
    if record is None:
        raise ValueError(f"unknown firm {firm_key}")
    market = eco.market()
    card = eco.brokerage.evaluator.evaluate(record, market)
    verdict = promotion.assess(
        eco.store, record, card, getattr(eco.feed, "name", ""),
        promotion.LiveReadiness(), eco.brokerage.reconcile(market).ok,
    )
    return record, verdict


@router.get("/village/live/{firm_key}", response_class=HTMLResponse)
def go_live_confirm(firm_key: str) -> HTMLResponse:
    """The evidence, and one button under it.

    This page is the reason the switch can be a switch. The gate at ``/`` shows
    a one-line request and a yes; it does not show *why*, and approving a
    promotion there means deciding about real money while looking at a
    sentence. Here the full criteria table is on screen at the moment of the
    decision — which is a stricter thing than the two-step it replaces, not a
    looser one.

    So it is a confirmation, not a warning. It states plainly what will happen,
    how much, and what will take it back off, and then it asks once.
    """
    from ..agents.web import page

    eco = ecosystem()
    try:
        try:
            record, verdict = _readiness(eco, firm_key)
        except ValueError as exc:
            return page("Going live", f"<div class='card alarm'>{e(exc)}</div>"
                                      "<p><a href='/village'>&larr; Mission Control</a></p>")

        rows = [{"criterion": e(r["criterion"]), "value": e(r["value"]),
                 "needs": e(r["needs"]),
                 "met": ("<span class=good>yes</span>" if r["met"] == "yes"
                         else "<span class=bad>NO</span>")}
                for r in promotion.table(verdict)]

        if not verdict.ready:
            body = (
                f"<h1>{e(firm_key)} cannot go live</h1>"
                f"<div class='card alarm'><strong>{len(verdict.failures)} of "
                f"{len(verdict.checks)} criteria are unmet.</strong> Nothing has "
                "been requested and nothing can be.</div>"
                + _panel("The evidence", _table(rows))
                + "<p><a href='/village'>&larr; Mission Control</a></p>"
            )
            return page("Going live", body)

        body = (
            f"<h1>Put {e(firm_key)} on real money?</h1>"
            + _panel("The evidence", _table(rows))
            + "<div class=card><h2>What happens when you press it</h2><ul>"
            f"<li>{e(firm_key)} starts trading <strong>"
            f"{fmt_money(verdict.start_capital)}</strong> of real money at "
            f"{e(LIVE_VENUE)}. That is the whole mandate — the rest of its "
            "paper capital is handed back to the brokerage, so the cap is on "
            "the buying power and not only on the number.</li>"
            f"<li>Its book has to be flat first. It is: a firm carries no "
            "pretend positions across.</li>"
            "<li>The village <strong>puts it back on paper by itself</strong>, "
            "with no approval and no delay, the moment its book cannot be "
            "valued, it stops being active, or it draws past the kill limit.</li>"
            "<li>You can take it back at any time from Mission Control. That "
            "half needs nobody.</li>"
            "<li>Nothing will ever be sold automatically. If a guard trips "
            "while it holds real stock, you are told what it holds and you "
            "close it deliberately.</li>"
            "</ul>"
            "<form method=post action='/village/actions/go-live'>"
            f"<input type=hidden name=firm value='{e(firm_key)}'>"
            f"<button class=kill>Yes — put {e(firm_key)} on real money with "
            f"{fmt_money(verdict.start_capital)}</button></form>"
            "<form method=get action='/village'><button>No, go back</button></form>"
            "</div>"
        )
        return page(f"{firm_key} — going live", body)
    finally:
        eco.db.close()


@router.post("/village/actions/go-live")
def action_go_live(firm: str = Form(...)) -> RedirectResponse:
    """Put one firm on real money, in one press, from the confirmation page.

    **This is the one control in the web tier that decides rather than asks**,
    and it is worth being explicit about why it is allowed to be, because
    everything else here files a request and stops.

    The gate exists to stop *the system* spending money on its own. It was
    never there to stop the person who owns the account from spending their
    own — and when the same human clicks "ask" and then clicks "approve" thirty
    seconds later, the second click is not consent, it is navigation. What
    actually protects this decision is the evidence gate (a firm that has not
    earned it cannot be requested at all, from here or anywhere), the cap, the
    flat-book rule, and a village that hands the money back by itself. None of
    that is weakened by removing a page.

    What is *not* skipped is the record. The approval row is still written and
    still approved by name, so the audit trail reads exactly as it would have,
    and `apply_approvals` still carries it out — the same code path the CLI and
    the gate use, including its refusal if the firm has moved since.
    """
    eco = ecosystem()
    try:
        record, verdict = _readiness(eco, firm)
        approval = eco.brokerage.request_promotion(
            record.firm_key, verdict, LIVE_VENUE, DECIDED_BY)
        eco.gate.approve(approval.id, approved_by=DECIDED_BY,
                         notes="one press, with the criteria table on screen")
        applied = eco.apply_approvals()
        said = "; ".join(applied) if applied else (
            f"approval #{approval.id} was written but not carried out — "
            "check the gate"
        )
        eco.flow.move("gate", "brokerage", f"{firm} put on real money",
                      kind="alarm", firm=firm, detail=verdict.summary())
    except Exception as exc:  # noqa: BLE001 - the refusal is the message
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/to-paper")
def action_to_paper(firm: str = Form(...)) -> RedirectResponse:
    """Take one firm off real money. Needs nobody, refuses nothing."""
    eco = ecosystem()
    try:
        result = eco.brokerage.demote_firm(firm, "pulled back from Mission Control")
        if not result["changed"]:
            said = f"{firm} was already on paper"
        else:
            said = f"{firm} is back on paper"
            if result["still_open"]:
                said += (f" — it STILL HOLDS {', '.join(result['still_open'])} at "
                         f"{result['was_venue']}; nothing was sold, close it yourself")
            eco.flow.emit("brokerage", f"{firm} back to paper", kind="alarm",
                          firm=firm, detail="pulled back from Mission Control")
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/all-to-paper")
def action_all_to_paper() -> RedirectResponse:
    """The one button you want at three in the morning."""
    eco = ecosystem()
    try:
        results = eco.brokerage.all_to_paper("panic switch, from Mission Control")
        if not results:
            said = "nothing was on real money"
        else:
            said = f"pulled {len(results)} firm(s) off real money"
            held = [f"{r['firm']}: {', '.join(r['still_open'])}"
                    for r in results if r["still_open"]]
            if held:
                said += (" — nothing was sold, and these are STILL HELD at the "
                         f"venue: {'; '.join(held)}")
            eco.flow.emit("brokerage", "everything back to paper", kind="alarm",
                          detail="panic switch, from Mission Control")
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/season")
def action_season() -> RedirectResponse:
    eco = ecosystem()
    try:
        result = eco.run_season()
        decided = [b for b in result["bouts"] if b.decided]
        said = (
            f"season: {len(decided)} bout(s) decided of {len(result['bouts'])}, "
            f"{len(result['milestones'])} milestone(s) awarded"
        )
    except Exception as exc:  # noqa: BLE001
        said = f"season failed: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/court-submit")
async def action_court_submit(file: UploadFile) -> RedirectResponse:
    """Drop a strategy file in. It is written to disk and read, never run."""
    eco = ecosystem()
    try:
        inbox = Path(eco.config.audit_vault).parent / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "submitted").name
        target = inbox / name
        target.write_bytes(await file.read())
        case = eco.try_strategy(target, submitted_by="web")
        said = f"{name}: {case.ruling.verdict.upper()} — {case.ruling.reason[:120]}"
    except Exception as exc:  # noqa: BLE001
        said = f"could not try that file: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/recruit")
async def action_recruit(file: UploadFile) -> RedirectResponse:
    """Drop a bot in and make it a firm — if the court clears it.

    Same trial as court-submit; the difference is what an ACCEPT buys. Here it
    creates a firm, paused and holding nothing, plus one approval for the
    capital. Nothing on this page funds anything.
    """
    eco = ecosystem()
    try:
        inbox = Path(eco.config.audit_vault).parent / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "submitted").name
        target = inbox / name
        target.write_bytes(await file.read())
        result = eco.recruit(target, submitted_by="web")
        said = (
            f"{result.firm_key} recruited from {name} — paused and unfunded. "
            f"Approve #{result.approval_id} to give it "
            f"{fmt_money(result.requested)} and set it trading."
            if result.accepted else
            f"{name} was not recruited: {result.reason}"
        )
    except Exception as exc:  # noqa: BLE001
        said = f"could not recruit that file: {exc}"
    finally:
        eco.db.close()
    return _back(said[:220])


@router.post("/village/actions/market-sell")
def action_market_sell(
    seller: str = Form(...), asset: str = Form(...), price: str = Form(...)
) -> RedirectResponse:
    eco = ecosystem()
    try:
        listing = eco.list_asset(seller, asset, price)
        said = f"listed #{listing.id}: {seller} sells {asset} for {listing.price} {listing.currency}"
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/market-buy")
def action_market_buy(buyer: str = Form(...), listing: int = Form(...)) -> RedirectResponse:
    eco = ecosystem()
    try:
        result = eco.buy_listing(buyer, listing)
        if result["settled"]:
            said = f"settled transaction #{result['transaction']}"
        else:
            said = (
                f"transaction #{result['transaction']} opened — capital does not move "
                f"until approval #{result['approval_id']} is granted"
            )
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/market-settle")
def action_market_settle(transaction: int = Form(...)) -> RedirectResponse:
    eco = ecosystem()
    try:
        result = eco.black_market.settle_capital_transfer(transaction)
        said = (
            f"transferred {result['amount']} from {result['from']} to {result['to']}; "
            + eco.brokerage.reconcile(eco.market()).summary()
        )
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/sandbox")
def action_sandbox(
    action: str = Form(...),
    actor: str = Form(...),
    name: str = Form(""),
    target: str = Form(""),
) -> RedirectResponse:
    eco = ecosystem()
    try:
        said = str(eco.intrigue(action, actor, name=name, target=target))
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said[:220])


# =========================================================================
# the village map
#
# The villagers are real. Every walker is an event `Ecosystem.tick()` wrote as
# it happened, read back out of `flow_events`, walking the road its event
# travelled. If nothing is running, nobody walks — which is the point: an
# animation that always loops tells you only that the animation works.
# =========================================================================
# The animation is one piece of code with two drivers behind it. The live page
# polls the database; the exported snapshot replays a recording embedded in the
# file. Both feed the same `show()`, so a villager means the same thing in a
# file you emailed as it does on the running server.
FLOW_CORE = """
const NS = 'http://www.w3.org/2000/svg';
const KIND = {ok:'var(--good)', blocked:'var(--warn)',
              refused:'var(--warn)', alarm:'var(--bad)'};
function lamp(nodeId, kind) {
  const el = document.getElementById('node-' + nodeId);
  if (!el) return;
  el.classList.add('lit');
  el.style.setProperty('--lit', KIND[kind] || 'var(--good)');
  setTimeout(() => el.classList.remove('lit'), 900);
}

// One villager walks one road, once, and is gone. Nobody wanders for effect:
// if you see somebody on a road, something happened.
function walk(edgeId, ev) {
  const road = document.getElementById('edge-' + edgeId);
  const traffic = document.getElementById('traffic');
  if (!road || !traffic) return;
  const colour = KIND[ev.kind] || 'var(--good)';

  const g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'villager walking');
  g.innerHTML =
    "<title>" + (ev.firm ? ev.firm + ' — ' : '') + ev.label + "</title>" +
    "<ellipse class='ground' cx='0' cy='2' rx='7' ry='2.5'/>" +
    "<line class='leg leg-l' x1='-2.5' y1='-8' x2='-3.5' y2='0'/>" +
    "<line class='leg leg-r' x1='2.5' y1='-8' x2='3.5' y2='0'/>" +
    "<rect class='torso' x='-4.5' y='-18' width='9' height='11' rx='3.5' fill='" + colour + "'/>" +
    "<circle class='head' cx='0' cy='-21.5' r='4.2' fill='" + colour + "'/>";
  traffic.appendChild(g);

  const length = road.getTotalLength();
  const duration = Math.min(2600, Math.max(900, length * 4));
  const started = performance.now();
  function step(now) {
    const t = Math.min(1, (now - started) / duration);
    const at = road.getPointAtLength(t * length);
    // Flip the walker so they always face the way they are going.
    const ahead = road.getPointAtLength(Math.min(length, t * length + 1));
    const facing = ahead.x < at.x ? -1 : 1;
    g.setAttribute('transform', 'translate(' + at.x + ',' + at.y + ') scale(' + facing + ',1)');
    if (t < 1) { requestAnimationFrame(step); }
    else {
      // Arrived. Stand still for a beat, then go inside.
      g.classList.remove('walking');
      setTimeout(() => g.remove(), 500);
    }
  }
  requestAnimationFrame(step);
}

function log(ev) {
  const list = document.getElementById('flow-log');
  if (!list) return;
  const li = document.createElement('li');
  li.className = 'kind-' + ev.kind;
  li.textContent = (ev.firm ? ev.firm + ' · ' : '') + ev.label
                 + (ev.detail ? ' — ' + ev.detail : '');
  list.prepend(li);
  while (list.children.length > 40) list.lastChild.remove();
}

function show(ev, delay) {
  setTimeout(() => {
    if (ev.edge) walk(ev.edge, ev);
    lamp(ev.node, ev.kind);
    log(ev);
  }, delay);
}

function say(text) {
  const status = document.getElementById('flow-status');
  if (status) status.textContent = text;
}
"""


# The live driver: ask the database what has happened since last time.
FLOW_LIVE = """
let after = Number(document.body.dataset.after || 0);
let paused = false;
let living = false;
let ticking = false;

async function poll() {
  if (paused) return;
  try {
    const res = await fetch('/village/flow/events?after=' + after);
    const data = await res.json();
    // Space a burst out so a tick reads as people crossing the village one
    // after another rather than a single flash.
    data.events.forEach((ev, i) => show(ev, i * 260));
    if (data.events.length) after = data.events[data.events.length - 1].id;
    // Most polls land between ticks, so "quiet" would be wrong while the
    // village is plainly running. Say which it is.
    say(data.events.length
      ? data.events.length + ' villager(s) on the roads'
      : (living ? 'running — next tick shortly'
                : 'quiet — run a tick, let it run, or start `trade run`'));
  } catch (err) { /* a dropped poll is not an error; the page keeps trying */ }
}
setInterval(poll, 900);
poll();

document.getElementById('flow-pause')?.addEventListener('click', (ev) => {
  paused = !paused;
  ev.target.textContent = paused ? 'Resume' : 'Pause';
});

// Let it run: tick on a timer instead of on a click.
//
// One tick at a time, never two at once. A setInterval that fires while the
// previous tick is still settling would have two processes writing the ledger,
// and the whole point of this system is that the books add up.
async function liveTick() {
  if (!living || ticking) return;
  ticking = true;
  try {
    await fetch('/village/actions/tick', {method: 'POST'});
  } catch (err) { /* a missed tick is not fatal; the next one comes round */ }
  ticking = false;
  if (living) setTimeout(liveTick, 2500);
}

document.getElementById('flow-live')?.addEventListener('click', (ev) => {
  living = !living;
  ev.target.textContent = living ? 'Stop' : 'Let it run';
  ev.target.classList.toggle('go', living);
  if (living) { paused = false; liveTick(); }
  else say('stopped — the village is quiet again');
});
"""


# The snapshot driver: there is no server to ask, so the events travel inside
# the file. It plays once on load and then waits — a recording that looped
# forever would look like a village that never stops working.
FLOW_REPLAY = """
function replay() {
  EVENTS.forEach((ev, i) => show(ev, i * 260));
  say(EVENTS.length
    ? 'replaying ' + EVENTS.length + ' recorded event(s)'
    : 'nothing was recorded — the village was quiet');
}

document.getElementById('flow-replay')?.addEventListener('click', replay);
replay();
"""


BUILDINGS = {
    # kind: (roof colour, wall colour, a glyph over the door)
    "well":    ("#7c9cbf", "#9fb6cc", "\u224b"),
    "halls":   ("#8a6f4e", "#c0a276", "\u2302"),
    "temple":  ("#a06a9a", "#c9a3c4", "\u2625"),
    "post":    ("#5f8f6f", "#93b79f", "\u21c4"),
    "bank":    ("#8a7f4e", "#c3b880", "$"),
    "library": ("#4e7a8a", "#87b0bd", "\u25a4"),
    "pound":   ("#8a5050", "#bd8c8c", "\u2298"),
    "gate":    ("#6a6a8a", "#a0a0bd", "\u26bf"),
    "hall":    ("#7a5f8a", "#b092bd", "\u2696"),
    "archive": ("#5f6a5f", "#96a396", "\u274f"),
    "bell":    ("#8a4a4a", "#bd8080", "\u2621"),
    "court":   ("#4e5f8a", "#8c9bbd", "\u00a7"),
    "arena":   ("#8a7a4e", "#bdae80", "\u2605"),
    "bazaar":  ("#8a6a4e", "#bd9c80", "\u25b2"),
    "tavern":  ("#6a5a4a", "#a3927f", "\u2617"),
}

BUILDING_W, BUILDING_H = 96, 60


def _building(node: dict) -> str:
    """One building: footprint, roof, nameplate, and a door the villagers use."""
    roof, wall, glyph = BUILDINGS.get(node["building"], ("#777", "#aaa", "\u2022"))
    x, y = node["x"], node["y"]
    w, h = BUILDING_W, BUILDING_H
    return (
        f"<g id='node-{e(node['id'])}' class='building'>"
        f"<title>{e(node['label'])} \u2014 {e(node['about'])}</title>"
        f"<ellipse class='ground' cx='{x + w / 2}' cy='{y + h + 6}' rx='{w / 2}' ry='7'/>"
        f"<rect class='walls' x='{x}' y='{y + 18}' width='{w}' height='{h - 18}' rx='3' "
        f"fill='{wall}'/>"
        f"<path class='roof' d='M{x - 6},{y + 18} L{x + w / 2},{y - 4} L{x + w + 6},{y + 18} Z' "
        f"fill='{roof}'/>"
        f"<rect class='door' x='{x + w / 2 - 9}' y='{y + h - 22}' width='18' height='22' rx='2'/>"
        f"<text class='glyph' x='{x + w / 2}' y='{y + 40}' text-anchor='middle'>{glyph}</text>"
        f"<text class='plate' x='{x + w / 2}' y='{y + h + 22}' text-anchor='middle'>"
        f"{e(node['label'])}</text>"
        f"</g>"
    )


def _villager(x: float, y: float, label: str, mood: str = "resident",
              tint: str = "", ident: str = "") -> str:
    """A little person. The same shape whether resident or message."""
    ident_attr = f" id='{e(ident)}'" if ident else ""
    colour = tint or "var(--fg)"
    return (
        f"<g{ident_attr} class='villager {e(mood)}' transform='translate({x},{y})'>"
        f"<title>{e(label)}</title>"
        f"<ellipse class='ground' cx='0' cy='2' rx='7' ry='2.5'/>"
        f"<line class='leg leg-l' x1='-2.5' y1='-8' x2='-3.5' y2='0'/>"
        f"<line class='leg leg-r' x1='2.5' y1='-8' x2='3.5' y2='0'/>"
        f"<rect class='torso' x='-4.5' y='-18' width='9' height='11' rx='3.5' "
        f"fill='{colour}'/>"
        f"<circle class='head' cx='0' cy='-21.5' r='4.2' fill='{colour}'/>"
        f"</g>"
    )


def _village(firms: list) -> str:
    """The map: roads first, then buildings, then the residents standing about."""
    from .flow import diagram

    shape = diagram()
    positions = {n["id"]: n for n in shape["nodes"]}

    def door(node_id: str):
        node = positions[node_id]
        return node["x"] + BUILDING_W / 2, node["y"] + BUILDING_H

    roads = []
    for edge in shape["edges"]:
        x1, y1 = door(edge["from"])
        x2, y2 = door(edge["to"])
        if abs(y1 - y2) < 1:
            d = f"M{x1},{y1 + 8} L{x2},{y2 + 8}"
        else:
            # Lanes between rows bow outward so two of them never lie on top of
            # each other and become one unreadable line.
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            d = f"M{x1},{y1 + 8} Q{mid_x + 34},{mid_y + 12} {x2},{y2 + 8}"
        roads.append(
            f"<path id='edge-{e(edge['id'])}' class='road' d='{d}'/>"
            f"<path class='road-edge' d='{d}'/>"
        )

    buildings = [_building(node) for node in shape["nodes"]]

    # Residents: one per firm that actually exists, loitering in the yard below
    # the firm quarter. Not decoration — their posture is the firm's status, so
    # a paused firm is visibly sitting one out and a killed one has faded.
    #
    # The yard starts below the nameplate (BUILDING_H + 22) rather than beside
    # it, or the villagers stand on top of the building's own label.
    quarter = positions["firms"]
    residents = []
    for index, firm in enumerate(firms):
        x = quarter["x"] + 16 + (index % 4) * 22
        y = quarter["y"] + BUILDING_H + 62 + (index // 4) * 26
        mood = {"active": "resident", "paused": "paused", "killed": "gone"}.get(
            firm.status, "resident"
        )
        residents.append(
            _villager(x, y, f"{firm.firm_key} \u2014 {firm.status}", mood,
                      ident=f"resident-{firm.firm_key}")
        )

    return (
        "<svg viewBox='0 0 1060 500' class='village' role='img' "
        "aria-label='the village, as it runs'>"
        "<rect class='sky' x='0' y='0' width='1060' height='500'/>"
        + "".join(roads) + "".join(buildings) + "".join(residents)
        + "<g id='traffic'></g></svg>"
    )


FLOW_STYLE = """
.village { width: 100%; height: auto; }
.village .sky { fill: var(--card); }
.village .road { fill: none; stroke: var(--line); stroke-width: 9;
                 stroke-linecap: round; opacity: .55; }
.village .road-edge { fill: none; stroke: var(--bg); stroke-width: 3;
                      stroke-dasharray: 5 9; stroke-linecap: round; opacity: .8; }
.village .ground { fill: #000; opacity: .13; }
.village .walls, .village .roof { stroke: rgba(0,0,0,.25); stroke-width: 1; }
.village .door { fill: rgba(0,0,0,.42); }
.village .glyph { font-size: 15px; fill: rgba(255,255,255,.85); }
.village .plate { font-size: 11px; fill: var(--muted); }
.village .building { transition: filter .25s; }
.village .building.lit { filter: drop-shadow(0 0 8px var(--lit)); }
.village .building.lit .roof { stroke: var(--lit); stroke-width: 2; }

.villager .leg { stroke: var(--fg); stroke-width: 2; stroke-linecap: round; }
.villager.walking .leg-l { animation: step .42s linear infinite; }
.villager.walking .leg-r { animation: step .42s linear infinite reverse; }
.villager.resident { animation: idle 3.4s ease-in-out infinite; }
.villager.paused { opacity: .55; }
.villager.paused .leg { stroke-dasharray: 2 3; }
.villager.gone { opacity: .3; }
.villager.gone .head, .villager.gone .torso { fill: var(--muted); }
@keyframes step { 0% { transform: rotate(-22deg); } 50% { transform: rotate(22deg); }
                  100% { transform: rotate(-22deg); } }
@keyframes idle { 0%,100% { translate: 0 0; } 50% { translate: 0 -1px; } }
@media (prefers-reduced-motion: reduce) {
  .villager.walking .leg-l, .villager.walking .leg-r,
  .villager.resident { animation: none; }
}

#flow-log { list-style: none; padding: 0; margin: 0; max-height: 15rem;
            overflow-y: auto; font-size: .85rem; }
#flow-log li { padding: .25rem 0; border-bottom: 1px solid var(--line); }
#flow-log li.kind-blocked, #flow-log li.kind-refused { color: var(--warn); }
#flow-log li.kind-alarm { color: var(--bad); }
"""


@router.get("/village/flow", response_class=HTMLResponse)
def flow_page() -> HTMLResponse:
    from ..agents.web import page

    eco = ecosystem()
    try:
        recorder = eco.flow
        firms = eco.store.firms()
        village = _village(firms)
        recent = recorder.recent(20)
        after = recorder.latest_id()
        autonomy = (
            "Let it run ticks the village on a timer. The council is sitting: "
            "raises, kills and resumes are decided from the ledger, and anything "
            "the evidence does not settle still waits for you at the gatehouse."
            if eco.config.autonomy.council_decides else
            "Let it run ticks the village on a timer. The council is not sitting "
            "(TRADE_AUTONOMY=human), so kills and capital raises will queue up at "
            "the gatehouse for you."
        )
        history = "".join(
            f"<li class='kind-{e(ev.kind)}'>"
            f"{e(ev.firm + ' · ' if ev.firm else '')}{e(ev.label)}"
            f"{e(' — ' + ev.detail if ev.detail else '')}</li>"
            for ev in reversed(recent)
        )
    finally:
        eco.db.close()

    body = "".join([
        f"<style>{FLOW_STYLE}</style>",
        "<h1>The village</h1>",
        "<p class=muted>Every villager on a road is something that actually "
        "happened: a proposal walking to the temple, a fill going to the counting "
        "house, a refusal being turned back to the pound. Nobody wanders for "
        "effect — when the village is quiet, the roads are empty.</p>",
        _panel("", village),
        f"<p class=muted>The figures outside the firm quarter are the "
        f"{len(firms)} firm(s) that actually exist. A paused firm sits one out; "
        f"a killed one has faded.</p>",
        "<div class=card>"
        "<form method=post action='/village/actions/tick'>"
        "<button class=go>Run a tick</button></form>"
        "<button id=flow-live>Let it run</button> "
        "<button id=flow-pause>Pause</button> "
        "<span class=muted id=flow-status>waiting…</span>"
        f"<p class=muted id=flow-autonomy>{e(autonomy)}</p></div>",
        _panel("What just happened", f"<ul id=flow-log>{history or ''}</ul>"),
        "<p class=muted>Refused proposals walk to <strong>the pound</strong> and stop "
        "there — they do not carry on to the trading post in a different colour. "
        "Kills and capital raises walk to the <strong>gatehouse</strong>, which is "
        "where they wait for you. The courthouse, arena, bazaar and tavern sit off "
        "the main road because none of them is on the tick's critical path.</p>",
        "<p><a href='/village'>&larr; Mission Control</a></p>",
        f"<script>{FLOW_CORE}{FLOW_LIVE}</script>",
    ])
    html_page = page("The flow — the Village", body)
    # `after` rides on <body> so the first poll does not replay history.
    return HTMLResponse(
        html_page.body.decode().replace("<body>", f"<body data-after='{after}'>")
    )


@router.get("/village/solar", response_class=HTMLResponse)
def solar_page() -> HTMLResponse:
    """The village as a solar system — one planet per firm.

    Served from the app rather than opened as a file so the page's fetch of
    ``/api/firms`` is same-origin. A ``file://`` page polling localhost is a
    CORS request, and the fix for that is either a browser flag or opening the
    API to arbitrary origins; serving it here needs neither.
    """
    page = Path(__file__).parent / "static" / "solar.html"
    if not page.exists():  # pragma: no cover - only if the file is deleted
        return HTMLResponse("<p>solar.html is missing from src/trading/static/</p>",
                            status_code=404)
    return HTMLResponse(page.read_text())


@router.get("/village/flow/events")
def flow_events(after: int = 0, limit: int = 60) -> JSONResponse:
    """Everything the tick has recorded since `after`. Polled by the page."""
    eco = ecosystem()
    try:
        events = [ev.to_dict() for ev in eco.flow.since(after, limit)]
    finally:
        eco.db.close()
    return JSONResponse({"events": events})


__all__ = ["router"]
