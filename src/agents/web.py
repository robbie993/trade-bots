"""Human Approval Gate — web UI (spec component 4, section 4.4).

Component 4 is specified as "Email/Slack/Web UI", and the kill notification in
section 4.4 ends in three buttons:

    [KILL] [CONTINUE] [REVIEW_DATA]

This module is those buttons. Server-rendered HTML, no JavaScript framework,
no build step — the operator opens a page on their phone, reads the numbers
that triggered the alert, and taps one thing.

    uvicorn src.agents.web:app --host 0.0.0.0 --port 8000
    ./scripts/start.sh

Two deliberate limits:

* It only ever *decides*. Every route writes to `human_approvals` through the
  same `HumanGate` the CLI uses; none of them spends money, kills an
  experiment or touches an ad platform. The orchestrator acts on the decision
  on its next tick, so the web tier has no privileged path into the ledger.
* There is no authentication. Bind it to localhost, or put it behind your own
  auth — anyone who can reach this page can approve spending. The startup
  banner and the page footer both say so.
"""

from __future__ import annotations

import html

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import Config
from ..db.connection import Database
from ..kill_criteria import should_kill_experiment
from ..money import fmt_money, fmt_pct
from ..notifications import build_notifier
from .cash_ledger import CashLedger
from .experiment_ledger import ExperimentLedger
from .human_gate import ApprovalAction, HumanGate
from .monitoring import HealthMonitor

app = FastAPI(title="MVV — Human Approval Gate", docs_url=None, redoc_url=None)

# Mission Control (src/trading/web.py) mounts alongside the gate: same app,
# same database, same process. The gate stays the only place a decision is
# granted; /village is where you watch everything else.
try:
    from ..trading.web import router as _village_router

    app.include_router(_village_router)
except Exception:  # pragma: no cover - the gate must serve with or without it
    _village_router = None

# The same numbers as JSON, for anything that is not this browser. Read-only:
# there is no POST in that router, so mounting it cannot widen what the web
# tier is able to do.
try:
    from ..trading.api import router as _api_router

    app.include_router(_api_router)
except Exception:  # pragma: no cover
    _api_router = None

# A hosted deployment runs read-only, and a broken one says why: see
# src/deploy.py. Installed last so the middleware wraps every router above it.
#
# Wrapped, because a serverless platform reports an import failure as a generic
# crash with the cause in a log nobody reads. If setting this up fails, the app
# still answers — with the traceback that explains it.
_BOOT_ERROR = ""
try:
    from ..deploy import install as _install_public

    _install_public(app)
except Exception:  # pragma: no cover - the point is to survive the unexpected
    import traceback

    _BOOT_ERROR = traceback.format_exc()

    @app.get("/{_path:path}", response_class=HTMLResponse)
    def _boot_failed(_path: str = "") -> HTMLResponse:
        return HTMLResponse(
            "<h1>The Village did not start</h1>"
            "<p>The application imported but could not finish setting itself "
            "up. This is the reason:</p>"
            f"<pre>{html.escape(_BOOT_ERROR)}</pre>",
            status_code=500,
        )


def context():
    """A fresh config/DB per request — no connection shared across threads."""
    config = Config()
    db = Database.from_url(config.database_url)
    gate = HumanGate(db, build_notifier(config), config.gate, config)
    ledger = ExperimentLedger(db)
    cash = CashLedger(db, config.cash)
    monitor = HealthMonitor(ledger, cash, config)
    return config, db, gate, ledger, cash, monitor


# -- rendering ------------------------------------------------------------
STYLE = """
:root { color-scheme: light dark; --fg:#111; --bg:#fff; --muted:#666;
        --line:#ddd; --warn:#b45309; --bad:#b91c1c; --good:#15803d; --card:#fafafa; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e7e7e7; --bg:#111; --muted:#9a9a9a; --line:#333;
          --warn:#fbbf24; --bad:#f87171; --good:#4ade80; --card:#1a1a1a; }
}
* { box-sizing: border-box; }
body { font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif;
       margin: 0 auto; padding: 1.5rem; max-width: 60rem; color: var(--fg);
       background: var(--bg); }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; }
a { color: inherit; }
.muted { color: var(--muted); font-size: .875rem; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 1rem;
        margin: .75rem 0; background: var(--card); }
.card.alarm { border-left: 4px solid var(--bad); }
table { border-collapse: collapse; width: 100%; font-size: .9rem;
        display: block; overflow-x: auto; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line);
         white-space: nowrap; }
th { color: var(--muted); font-weight: 600; }
.bad { color: var(--bad); font-weight: 600; }
.good { color: var(--good); }
.warn { color: var(--warn); }
form { display: inline; }
button { font: inherit; padding: .5rem 1rem; margin: .25rem .25rem 0 0;
         border-radius: 6px; border: 1px solid var(--line); cursor: pointer;
         background: var(--bg); color: var(--fg); }
button.kill { border-color: var(--bad); color: var(--bad); }
button.go { border-color: var(--good); color: var(--good); }
footer { margin-top: 3rem; border-top: 1px solid var(--line); padding-top: 1rem; }
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
        f"{body}"
        f"<footer class=muted>MVV Phase 1 — Human Approval Gate &middot; "
        f"<a href='/village'>Mission Control</a>. "
        f"This page has no authentication: anyone who can reach it can approve "
        f"spending. Bind it to localhost or put it behind your own auth."
        f"</footer></body></html>"
    )


def e(value) -> str:
    return html.escape(str(value))


def rows_to_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "<p class=muted>(none)</p>"
    head = "".join(f"<th>{e(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{r.get(c, '')}</td>" for c in columns) + "</tr>" for r in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def decision_buttons(approval) -> str:
    """[KILL] [CONTINUE] on a kill request; [Approve] [Reject] otherwise."""
    if approval.action == ApprovalAction.KILL_EXPERIMENT.value:
        yes, no, yes_class, no_class = "KILL", "CONTINUE", "kill", "go"
    else:
        yes, no, yes_class, no_class = "Approve", "Reject", "go", ""
    review = (
        f"<a href='/experiments/{approval.experiment_id}'><button>REVIEW_DATA</button></a>"
        if approval.experiment_id
        else ""
    )
    return (
        f"<form method=post action='/approvals/{approval.id}/approve'>"
        f"<input type=hidden name=approved_by value='web'>"
        f"<button class='{yes_class}'>{yes}</button></form>"
        f"<form method=post action='/approvals/{approval.id}/reject'>"
        f"<input type=hidden name=approved_by value='web'>"
        f"<button class='{no_class}'>{no}</button></form>{review}"
    )


# -- routes ---------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    config, db, gate, ledger, cash, monitor = context()
    try:
        pending = gate.pending()
        cash_status = cash.status()
        conditions = monitor.stop_conditions()

        stop_html = ""
        fired = [c for c in conditions if c["triggered"]]
        if fired:
            items = "".join(f"<li>{e(c['detail'])}</li>" for c in fired)
            stop_html = (
                f"<div class='card alarm'><strong class=bad>🛑 KILL_ALL — stop condition met"
                f"</strong><ul>{items}</ul></div>"
            )

        approvals_html = ""
        for a in pending:
            exp = ledger.get(a.experiment_id) if a.experiment_id else None
            title = exp.product_name if exp else a.action
            amount = f" — {fmt_money(a.amount)}" if a.amount is not None else ""
            detail = ""
            if exp and a.action == ApprovalAction.KILL_EXPERIMENT.value:
                detail = (
                    f"<table><tr><th>Impressions</th><th>Sessions</th><th>Orders</th>"
                    f"<th>CTR</th><th>Conversion</th><th>CAC</th>"
                    f"<th>Contribution</th><th>Refund rate</th></tr>"
                    f"<tr><td>{exp.impressions}</td><td>{exp.sessions}</td><td>{exp.orders}</td>"
                    f"<td>{fmt_pct(exp.ctr)}</td><td>{fmt_pct(exp.conversion_rate)}</td>"
                    f"<td>{fmt_money(exp.cac)}</td>"
                    f"<td>{fmt_money(exp.contribution_margin)}</td>"
                    f"<td>{fmt_pct(exp.refund_rate)}</td></tr></table>"
                    f"<p class=muted>Ads are already paused. This experiment is not killed yet.</p>"
                )
            css = " alarm" if a.action == ApprovalAction.KILL_EXPERIMENT.value else ""
            approvals_html += (
                f"<div class='card{css}'>"
                f"<strong>{e(a.action)}{e(amount)}</strong> — {e(title)}<br>"
                f"<span class=muted>#{a.id} · requested {e(a.requested_at)}</span>"
                f"<p>{e(a.reason)}</p>{detail}{decision_buttons(a)}</div>"
            )
        if not pending:
            approvals_html = "<p class=muted>Nothing waiting on you.</p>"

        exp_rows = []
        for exp in ledger.all():
            should_kill, reason = should_kill_experiment(exp)
            flag = exp.pending_kill_reason or (reason if should_kill else "")
            exp_rows.append(
                {
                    "id": f"<a href='/experiments/{exp.id}'>#{exp.id}</a>",
                    "product": e(exp.product_name),
                    "status": e(exp.status),
                    "gate": "yes" if exp.meets_sample_gate else "no",
                    "orders": exp.orders,
                    "CTR": fmt_pct(exp.ctr),
                    "CAC": fmt_money(exp.cac),
                    "contribution": fmt_money(exp.contribution_margin),
                    "flag": f"<span class=bad>{e(flag)}</span>" if flag else "",
                }
            )

        cash_class = "bad" if cash_status["negative"] else (
            "warn" if cash_status["buffer_breached"] else "good"
        )
        body = (
            f"<h1>Human Approval Gate</h1>"
            f"<p class=muted>Nothing here spends money. Decisions are recorded; the "
            f"orchestrator acts on them on its next tick.</p>"
            f"<div class=card><a href='/village'><button class=go>"
            f"Mission Control &rarr;</button></a> "
            f"<span class=muted>firms, brokerage, court, competition, market, "
            f"sandbox</span></div>"
            f"{stop_html}"
            f"<div class=card>Cash: <span class={cash_class}>"
            f"{fmt_money(cash_status['available_cash'])} available</span> · "
            f"{fmt_money(cash_status['held'])} held · "
            f"{fmt_money(cash_status['spendable'])} spendable after buffer</div>"
            f"<h2>Waiting on you ({len(pending)})</h2>{approvals_html}"
            f"<h2>Experiments</h2>"
            f"{rows_to_table(exp_rows, ['id', 'product', 'status', 'gate', 'orders', 'CTR', 'CAC', 'contribution', 'flag'])}"
            f"<h2>Health</h2><div class=card>"
            + "".join(f"{e(str(a))}<br>" for a in monitor.daily_health_check())
            + "</div>"
        )
        return page("MVV — Approvals", body)
    finally:
        db.close()


@app.get("/experiments/{experiment_id}", response_class=HTMLResponse)
def review_data(experiment_id: int) -> HTMLResponse:
    """The REVIEW_DATA button of spec 4.4 — every condition, not just the
    first one that fired."""
    config, db, gate, ledger, cash, monitor = context()
    try:
        exp = ledger.get(experiment_id)
        if exp is None:
            return page("Not found", "<h1>No such experiment</h1><a href='/'>Back</a>")

        gap = exp.missing_for_gate
        checks = [
            {
                "condition": e(c["condition"]),
                "value": e(c["value"]),
                "kill when": e(c["kill_when"]),
                "triggered": "<span class=bad>YES</span>" if c["triggered"] else "no",
            }
            for c in exp.kill_checks()
        ]
        should_kill, reason = should_kill_experiment(exp)
        events = "".join(
            f"<tr><td>{e(ev['created_at'])}</td><td>{e(ev['event_type'])}</td>"
            f"<td>{e(ev['detail'] or '')}</td></tr>"
            for ev in ledger.events(experiment_id, limit=20)
        )
        body = (
            f"<h1>#{exp.id} {e(exp.product_name)}</h1>"
            f"<p class=muted>{e(exp.status)} · {e(exp.source_platform)} · {e(exp.supplier)} · "
            f"ads {'PAUSED' if exp.ads_paused else 'RUNNING'} at "
            f"{fmt_money(exp.daily_ad_budget)}/day</p>"
            + (f"<div class='card alarm'><strong class=bad>KILL PENDING:</strong> "
               f"{e(exp.pending_kill_reason)}</div>" if exp.pending_kill_reason else "")
            + (f"<div class=card><strong>KILLED:</strong> {e(exp.kill_reason)}</div>"
               if exp.kill_reason else "")
            + f"<h2>Unit economics</h2><div class=card>"
            f"cost {fmt_money(exp.unit_cost)} · price {fmt_money(exp.selling_price)} · "
            f"unit margin {fmt_money(exp.margin_abs)} ({fmt_pct(exp.margin_pct)})</div>"
            f"<h2>Sample gate</h2><div class=card>"
            f"impressions {exp.impressions}/{exp.minimum_impressions} "
            f"(need {gap['impressions']} more)<br>"
            f"sessions {exp.sessions}/{exp.minimum_sessions} (need {gap['sessions']} more)<br>"
            f"orders {exp.orders}/{exp.minimum_orders} (need {gap['orders']} more)<br>"
            + ("<span class=good>MET — decisions are valid</span>"
               if exp.meets_sample_gate
               else "<span class=warn>NOT MET — no decision can be made</span>")
            + "</div><h2>Kill conditions</h2>"
            + rows_to_table(checks, ["condition", "value", "kill when", "triggered"])
            + f"<p>Verdict: <strong>{e(reason or 'healthy, keep running')}</strong></p>"
            f"<h2>Events</h2><table><thead><tr><th>when</th><th>event</th><th>detail</th>"
            f"</tr></thead><tbody>{events}</tbody></table>"
            f"<p><a href='/'>← Back to approvals</a></p>"
        )
        return page(f"#{exp.id} {exp.product_name}", body)
    finally:
        db.close()


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: int, approved_by: str = Form("web"), notes: str = Form("")):
    config, db, gate, ledger, cash, monitor = context()
    try:
        gate.approve(approval_id, approved_by, notes)
    except ValueError:
        pass  # already decided; the redirect shows current state
    finally:
        db.close()
    return RedirectResponse("/", status_code=303)


@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: int, approved_by: str = Form("web"), notes: str = Form("")):
    config, db, gate, ledger, cash, monitor = context()
    try:
        gate.reject(approval_id, approved_by, notes)
    except ValueError:
        pass
    finally:
        db.close()
    return RedirectResponse("/", status_code=303)


@app.get("/api/health")
def api_health() -> dict:
    """JSON for a monitor or a cron job. Mirrors `mvv health`."""
    config, db, gate, ledger, cash, monitor = context()
    try:
        conditions = monitor.stop_conditions()
        return {
            "alerts": [str(a) for a in monitor.daily_health_check()],
            "cash": {
                "available": str(cash.available_cash()),
                "spendable": str(cash.spendable()),
            },
            "pending_approvals": len(gate.pending()),
            "stop_conditions": conditions,
            "kill_all": any(c["triggered"] for c in conditions),
        }
    finally:
        db.close()
