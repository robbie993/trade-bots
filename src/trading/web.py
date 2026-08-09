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
into the ledger, and the two places a decision is granted are still the gate
at ``/`` and ``mvv approve``.

There is no authentication. Bind it to localhost.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import Config
from ..db.connection import Database
from ..money import D, ZERO, fmt_money, fmt_pct, money
from ..notifications import build_notifier
from .config import TradingConfig
from .ecosystem import Ecosystem

router = APIRouter()


def ecosystem() -> Ecosystem:
    """A fresh config/DB per request — no connection shared across threads."""
    app_config = Config()
    db = Database.from_url(app_config.database_url)
    return Ecosystem(db, TradingConfig(), app_config, build_notifier(app_config))


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
        "<a href='/village/flow'><button>Watch the flow &rarr;</button></a>",
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
        _brokerage_panel(eco, firms),
        _court_panel(eco),
        _arena_panel(eco),
        _market_panel(eco),
        _sandbox_panel(eco),
        "<p><a href='/'>&larr; approval gate</a> · "
        "<span class=muted>nothing on this page grants an approval</span></p>",
    ])


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
    )
    return _panel(
        "Strategy court",
        _table(rows)
        + "<p class=muted>The file is read, never executed. ACCEPT admits an "
        "<em>unselected</em> candidate genome — nothing trades because the court "
        "liked it.</p>",
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
            "<p><a href='/village'>&larr; Mission Control</a></p>",
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
        case = eco.court.submit(target, submitted_by="web")
        said = f"{name}: {case.ruling.verdict.upper()} — {case.ruling.reason[:120]}"
    except Exception as exc:  # noqa: BLE001
        said = f"could not try that file: {exc}"
    finally:
        eco.db.close()
    return _back(said)


@router.post("/village/actions/market-sell")
def action_market_sell(
    seller: str = Form(...), asset: str = Form(...), price: str = Form(...)
) -> RedirectResponse:
    eco = ecosystem()
    try:
        listing = eco.black_market.list_asset(seller, asset, price)
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
        result = eco.black_market.buy(buyer, listing)
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
        if action == "form":
            members = [m.strip() for m in target.split(",") if m.strip()]
            said = str(eco.sandbox.form(name, actor, members))
        elif action == "betray":
            said = str(eco.sandbox.betray(actor, name))
        elif action == "spy":
            said = str(eco.sandbox.spy(actor, target))
        elif action == "sabotage":
            said = str(eco.sandbox.sabotage(actor, target))
        else:
            said = f"unknown sandbox action {action!r}"
    except Exception as exc:  # noqa: BLE001
        said = f"refused: {exc}"
    finally:
        eco.db.close()
    return _back(said[:220])


# =========================================================================
# the flow diagram
#
# The dots are real. Every one is an event `Ecosystem.tick()` wrote as it
# happened, read back out of `flow_events`. If nothing is running, nothing
# moves — which is the point: an animation that always loops tells you only
# that the animation works.
# =========================================================================
FLOW_SCRIPT = """
const NS = 'http://www.w3.org/2000/svg';
const KIND_COLOUR = {ok:'var(--good)', blocked:'var(--warn)',
                     refused:'var(--warn)', alarm:'var(--bad)'};
let after = Number(document.body.dataset.after || 0);
let paused = false;

function pulse(nodeId, kind) {
  const el = document.getElementById('node-' + nodeId);
  if (!el) return;
  el.classList.add('lit');
  el.style.setProperty('--lit', KIND_COLOUR[kind] || 'var(--good)');
  setTimeout(() => el.classList.remove('lit'), 700);
}

function travel(edgeId, kind) {
  const path = document.getElementById('edge-' + edgeId);
  if (!path) return;
  const dot = document.createElementNS(NS, 'circle');
  dot.setAttribute('r', 5);
  dot.setAttribute('fill', KIND_COLOUR[kind] || 'var(--good)');
  path.parentNode.appendChild(dot);
  const length = path.getTotalLength();
  const started = performance.now();
  const duration = 650;
  function step(now) {
    const t = Math.min(1, (now - started) / duration);
    const at = path.getPointAtLength(t * length);
    dot.setAttribute('cx', at.x);
    dot.setAttribute('cy', at.y);
    if (t < 1) requestAnimationFrame(step); else dot.remove();
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
    if (ev.edge) travel(ev.edge, ev.kind);
    pulse(ev.node, ev.kind);
    log(ev);
  }, delay);
}

async function poll() {
  if (paused) return;
  try {
    const res = await fetch('/village/flow/events?after=' + after);
    const data = await res.json();
    // Space a burst out so a whole tick reads as a sequence rather than a flash.
    data.events.forEach((ev, i) => show(ev, i * 220));
    if (data.events.length) after = data.events[data.events.length - 1].id;
    const status = document.getElementById('flow-status');
    if (status) status.textContent = data.events.length
      ? data.events.length + ' step(s) just now'
      : 'idle — run a tick, or start `trade run`';
  } catch (e) { /* the page keeps trying; a dropped poll is not an error */ }
}
setInterval(poll, 900);
poll();

document.getElementById('flow-pause')?.addEventListener('click', (e) => {
  paused = !paused;
  e.target.textContent = paused ? 'Resume' : 'Pause';
});
"""


def _svg() -> str:
    from .flow import diagram

    shape = diagram()
    positions = {n["id"]: n for n in shape["nodes"]}
    width, height = 130, 54

    edges = []
    for edge in shape["edges"]:
        a, b = positions[edge["from"]], positions[edge["to"]]
        x1, y1 = a["x"] + width / 2, a["y"] + height / 2
        x2, y2 = b["x"] + width / 2, b["y"] + height / 2
        # Straight where the stages are in a row, gently curved where the flow
        # drops to a sink, so the two never overlap into an unreadable knot.
        if a["y"] == b["y"]:
            d = f"M{x1},{y1} L{x2},{y2}"
        else:
            d = f"M{x1},{y1} Q{(x1 + x2) / 2},{(y1 + y2) / 2 + 26} {x2},{y2}"
        edges.append(
            f"<path id='edge-{e(edge['id'])}' d='{d}' class='wire' "
            f"marker-end='url(#arrow)'/>"
        )

    nodes = []
    for node in shape["nodes"]:
        nodes.append(
            f"<g id='node-{e(node['id'])}' class='node'>"
            f"<title>{e(node['about'])}</title>"
            f"<rect x='{node['x']}' y='{node['y']}' width='{width}' height='{height}' "
            f"rx='8'/>"
            f"<text x='{node['x'] + width / 2}' y='{node['y'] + height / 2 + 5}' "
            f"text-anchor='middle'>{e(node['label'])}</text></g>"
        )

    return (
        "<svg viewBox='0 0 950 350' class='flow' role='img' "
        "aria-label='the tick, as it runs'>"
        "<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' "
        "markerWidth='6' markerHeight='6' orient='auto-start-reverse'>"
        "<path d='M0,0 L10,5 L0,10 z' class='arrowhead'/></marker></defs>"
        + "".join(edges) + "".join(nodes) + "</svg>"
    )


FLOW_STYLE = """
.flow { width: 100%; height: auto; }
.flow .wire { fill: none; stroke: var(--line); stroke-width: 2; }
.flow .arrowhead { fill: var(--line); }
.flow .node rect { fill: var(--card); stroke: var(--line); stroke-width: 2;
                   transition: stroke .2s, filter .2s; }
.flow .node text { fill: var(--fg); font-size: 13px; }
.flow .node.lit rect { stroke: var(--lit); stroke-width: 3; }
#flow-log { list-style: none; padding: 0; margin: 0; max-height: 16rem;
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
        recent = recorder.recent(20)
        after = recorder.latest_id()
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
        "<h1>The flow</h1>",
        "<p class=muted>Every dot is something that actually happened: a proposal, "
        "a fill, a refusal. Nothing loops for decoration — if the village is idle, "
        "the diagram is still.</p>",
        _panel("", _svg()),
        "<div class=card>"
        "<form method=post action='/village/actions/tick'>"
        "<button class=go>Run a tick</button></form>"
        "<button id=flow-pause>Pause</button> "
        "<span class=muted id=flow-status>waiting…</span></div>",
        _panel("Recent steps", f"<ul id=flow-log>{history or ''}</ul>"),
        "<p class=muted>Blocked and refused things travel to <strong>Blocked</strong> "
        "and stop there — they do not glide on to the venue in a different colour. "
        "Kills and capital raises travel to the <strong>Human gate</strong>, which is "
        "where they wait for you.</p>",
        "<p><a href='/village'>&larr; Mission Control</a></p>",
        f"<script>{FLOW_SCRIPT}</script>",
    ])
    html_page = page("The flow — the Village", body)
    # `after` rides on <body> so the first poll does not replay history.
    return HTMLResponse(
        html_page.body.decode().replace("<body>", f"<body data-after='{after}'>")
    )


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
