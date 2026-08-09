"""Command line interface — the operator's half of the loop.

    python -m src.main init-db
    python -m src.main cash open --once
    python -m src.main scout
    python -m src.main approvals
    python -m src.main approve 3 --by robbie
    python -m src.main metrics 1 --impressions 12000 --sessions 400 --orders 60 ...
    python -m src.main evaluate
    python -m src.main health
    python -m src.main tick
    python -m src.main serve          # the web gate (spec 4.4)

Every command that could move money either prints what it would do or writes a
pending approval. `approve`/`reject` are the only commands that grant one, and
they are the only place a human decision enters the system.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .agents.cash_ledger import CashLedger
from .config import Config
from .db.connection import Database, utcnow
from .agents.economic_calculator import EconomicCalculator
from .agents.human_gate import ApprovalAction, HumanGate
from .kill_criteria import should_kill_experiment
from .agents.experiment_ledger import ExperimentLedger, LedgerError
from .agents.metrics import build_metrics_provider
from .models import CashCategory, Experiment, Order, Status
from .agents.monitoring import HealthMonitor, format_alerts, format_weekly_summary
from .money import D, ZERO, fmt_money, fmt_pct
from .notifications import build_notifier
from .agents.orchestrator import Orchestrator, TickReport
from .agents.scout import ProductCandidate, build_scout
from .court.file_court import FileCourt
from .trading.cli import add_trade_parser


def _context(args) -> tuple[Config, Database]:
    config = Config()
    if getattr(args, "database_url", None):
        config = Config(database_url=args.database_url)
    return config, Database.from_url(config.database_url)


def _table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "(none)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    body = [
        "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows
    ]
    return "\n".join([header, sep, *body])


# =========================================================================
# commands
# =========================================================================
def cmd_init_db(args) -> int:
    config, db = _context(args)
    db.init_schema()
    print(f"schema applied to {config.database_url} ({db.dialect})")
    print("tables: " + ", ".join(db.table_names()))
    return 0


def cmd_scout(args) -> int:
    config, db = _context(args)
    scout = build_scout(config)
    econ = EconomicCalculator(config.cash, config.discovery)
    candidates = scout.discover(limit=args.limit)
    if not candidates:
        print("no candidates found")
        return 0
    rows = []
    for c in candidates:
        worth, why = econ.is_worth_testing(c)
        price = c.selling_price or econ.suggest_price(c.unit_cost, c.shipping_cost)
        e = econ.unit_economics(c.unit_cost, price, c.shipping_cost)
        rows.append(
            {
                "product_id": c.product_id,
                "name": c.product_name[:32],
                "platform": c.source_platform,
                "landed": fmt_money(e.landed_cost),
                "price": fmt_money(price),
                "gross%": fmt_pct(e.gross_margin_pct),
                "breakeven CAC": fmt_money(e.breakeven_cac),
                "test?": "yes" if worth else "no",
            }
        )
    print(_table(rows, list(rows[0].keys())))
    print("\nNothing has been written. Run `tick` to create experiments.")
    return 0


def _slug(text: str, limit: int = 24) -> str:
    kept = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(kept).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:limit] or "product"


def cmd_experiment_add(args) -> int:
    """Enter a real product by hand — spec section 5.1, the manual Scout.

    This is the same path `tick` takes for a scouted candidate, in the same
    order and with the same refusals: platform gate, duplicate check, then the
    economic filter. It exists because a source connector needs API
    credentials, and the business question does not: 20 products typed in by
    hand answer it exactly as well as 20 pulled from an API.

    It creates a `discovered` row and nothing else. No money moves here — the
    sample order and the ad budget are still separate human approvals.
    """
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    econ = EconomicCalculator(config.cash, config.discovery)

    platform = (args.platform or "").strip().lower()
    if platform not in config.gate.approved_platforms:
        # Adding a platform is a human decision (spec section 6) — the same
        # rule the orchestrator applies to scouted candidates.
        gate = HumanGate(db, build_notifier(config), config.gate, config)
        approval = gate.request_platform(platform)
        print(f"platform {platform!r} is not approved — nothing was written.")
        print(f"approval {approval.id} requested; approve it then run this again.")
        print(f"approved today: {', '.join(config.gate.approved_platforms)}")
        return 1

    product_id = args.product_id or f"manual-{platform}-{_slug(args.name)}"
    if ledger.exists(product_id):
        existing = ledger.get_by_product_id(product_id)
        print(f"experiment {existing.id} already exists for product_id {product_id!r}")
        print("pass --product-id to record a genuinely different product")
        return 1

    candidate = ProductCandidate(
        product_id=product_id,
        product_name=args.name,
        source_platform=platform,
        supplier=args.supplier or platform,
        unit_cost=D(args.cost),
        shipping_cost=D(args.shipping),
        selling_price=D(args.price) if args.price else ZERO,
        url=args.url or "",
    )

    worth_it, why = econ.is_worth_testing(candidate)
    if not worth_it and not args.force:
        print(f"not worth testing: {why}")
        print("nothing was written. Re-run with --force to record it anyway.")
        return 1

    price = candidate.selling_price or econ.suggest_price(
        candidate.unit_cost, candidate.shipping_cost
    )
    unit = econ.unit_economics(candidate.unit_cost, price, candidate.shipping_cost)

    experiment = Experiment(
        product_id=candidate.product_id,
        product_name=candidate.product_name,
        source_platform=candidate.source_platform,
        supplier=candidate.supplier,
        product_url=candidate.url,
        # Landed cost is the COGS the kill logic must use; shipping is not
        # free just because the supplier lists it separately.
        unit_cost=econ.landed_cost(candidate.unit_cost, candidate.shipping_cost),
        selling_price=price,
        status=Status.DISCOVERED.value,
    )
    note = why if worth_it else f"FORCED past the economic filter: {why}"
    stored = ledger.create_experiment(experiment, note=f"manual entry — {note}")

    print(f"experiment {stored.id} created: {stored.product_name}")
    print(f"  product_id     {stored.product_id}")
    print(f"  landed cost    {fmt_money(unit.landed_cost)}  "
          f"(unit {fmt_money(unit.unit_cost)} + shipping {fmt_money(unit.shipping_cost)})")
    print(f"  selling price  {fmt_money(price)}" + ("" if args.price else "  (suggested)"))
    print(f"  gross margin   {fmt_money(unit.gross_margin_abs)}  "
          f"({fmt_pct(unit.gross_margin_pct)})")
    print(f"  breakeven CAC  {fmt_money(unit.breakeven_cac)}  "
          f"— ad cost per order above this loses money")
    if not worth_it:
        print(f"  ⚠ forced past the economic filter: {why}")
    print("\nnothing has been spent. Next:")
    print(f"  tick                      request the sample order for {stored.id}")
    print("  approvals                 see what it is waiting on")
    return 0


def cmd_list(args) -> int:
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    experiments = ledger.by_status(*args.status) if args.status else ledger.all()
    rows = []
    for e in experiments:
        should_kill, reason = should_kill_experiment(e)
        rows.append(
            {
                "id": e.id,
                "product": (e.product_name or e.product_id)[:28],
                "status": e.status,
                "gate": "yes" if e.meets_sample_gate else "no",
                "orders": e.orders,
                "CTR": fmt_pct(e.ctr),
                "CR": fmt_pct(e.conversion_rate),
                "CAC": fmt_money(e.cac),
                "contrib": fmt_money(e.contribution_margin),
                "flag": e.pending_kill_reason or (reason if should_kill else ""),
            }
        )
    print(_table(rows, list(rows[0].keys()) if rows else []))
    return 0


def cmd_show(args) -> int:
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    exp = ledger.get(args.experiment_id)
    if exp is None:
        print(f"experiment {args.experiment_id} not found", file=sys.stderr)
        return 1

    print(f"#{exp.id}  {exp.product_name}  [{exp.status}]")
    print(f"  product_id  {exp.product_id}   platform {exp.source_platform}   supplier {exp.supplier}")
    print(f"  unit cost   {fmt_money(exp.unit_cost)}   price {fmt_money(exp.selling_price)}   "
          f"unit margin {fmt_money(exp.margin_abs)} ({fmt_pct(exp.margin_pct)})")
    print(f"  ads         {'PAUSED' if exp.ads_paused else 'RUNNING'} at {fmt_money(exp.daily_ad_budget)}/day")
    if exp.pending_kill_reason:
        print(f"  ** KILL PENDING: {exp.pending_kill_reason} — awaiting human decision")
    if exp.kill_reason:
        print(f"  ** KILLED: {exp.kill_reason} at {exp.killed_at}")

    print("\nRaw metrics")
    print(f"  impressions {exp.impressions}   clicks {exp.clicks}   sessions {exp.sessions}")
    print(f"  orders {exp.orders}   revenue {fmt_money(exp.revenue)}   ad spend {fmt_money(exp.ad_spend)}")
    print(f"  refunds {exp.refunds}   chargebacks {exp.chargebacks}   "
          f"avg delivery {exp.avg_delivery_days} days")

    print("\nSample gate")
    gap = exp.missing_for_gate
    print(f"  impressions {exp.impressions}/{exp.minimum_impressions} (need {gap['impressions']} more)")
    print(f"  sessions    {exp.sessions}/{exp.minimum_sessions} (need {gap['sessions']} more)")
    print(f"  orders      {exp.orders}/{exp.minimum_orders} (need {gap['orders']} more)")
    print(f"  => {'MET — decisions are valid' if exp.meets_sample_gate else 'NOT MET — no decision'}")

    print("\nKill conditions")
    rows = [
        {
            "condition": c["condition"],
            "value": str(c["value"]),
            "kill when": c["kill_when"],
            "triggered": "YES" if c["triggered"] else "no",
        }
        for c in exp.kill_checks()
    ]
    print(_table(rows, ["condition", "value", "kill when", "triggered"]))
    should_kill, reason = should_kill_experiment(exp)
    print(f"  => verdict: {reason if reason else 'healthy, keep running'}")

    events = ledger.events(exp.id, limit=args.events)
    if events:
        print("\nRecent events")
        for ev in events:
            print(f"  {ev['created_at']}  {ev['event_type']:<18} {ev['detail'] or ''}")
    return 0


def cmd_metrics(args) -> int:
    """Enter observed metrics by hand (spec 5.4 MEASURE)."""
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    exp = ledger.record_metrics(
        args.experiment_id,
        impressions=args.impressions,
        clicks=args.clicks,
        sessions=args.sessions,
        orders=args.orders,
        revenue=args.revenue,
        ad_spend=args.ad_spend,
        refunds=args.refunds,
        chargebacks=args.chargebacks,
        avg_delivery_days=args.avg_delivery_days,
        mode="add" if args.add else "set",
    )
    print(f"#{exp.id} {exp.product_name}: CTR {fmt_pct(exp.ctr)}, CR {fmt_pct(exp.conversion_rate)}, "
          f"CAC {fmt_money(exp.cac)}, contribution {fmt_money(exp.contribution_margin)}")
    print(f"sample gate: {'MET' if exp.meets_sample_gate else 'not met'}")
    should_kill, reason = should_kill_experiment(exp)
    if should_kill:
        print(f"KILL TRIGGER: {reason} — run `evaluate` to pause ads and notify")
    return 0


def cmd_evaluate(args) -> int:
    """Run the kill logic over live experiments (spec 5.5 EVALUATE)."""
    config, db = _context(args)
    return _evaluate(Orchestrator(db, config))


def _evaluate(orch: Orchestrator) -> int:
    report = TickReport(started_at=utcnow())
    orch.evaluate(report)
    orch.advance_lifecycle(report)
    if report.kill_triggered:
        for item in report.kill_triggered:
            print(f"KILL TRIGGERED on #{item['experiment_id']}: {item['reason']} "
                  f"(ads paused, awaiting decision)")
    if report.killed:
        print(f"killed (human-approved): {report.killed}")
    if report.continued:
        print(f"continued (human override): {report.continued}")
    if not (report.kill_triggered or report.killed or report.continued):
        print("no kill triggers")
    return 0


def cmd_health(args) -> int:
    config, db = _context(args)
    monitor = HealthMonitor(ExperimentLedger(db), CashLedger(db, config.cash), config)
    print(format_alerts(monitor.daily_health_check()))
    print("\nStop conditions (spec 1.3) — any one ends Phase 1:")
    conditions = monitor.stop_conditions()
    for c in conditions:
        mark = "TRIGGERED" if c["triggered"] else "ok"
        print(f"  [{mark:>9}] {c['condition']}: {c['detail']}")
    triggered = any(c["triggered"] for c in conditions)
    if triggered:
        print("\n🛑 KILL_ALL — stop the system.")
    return 2 if triggered else 0


def cmd_weekly(args) -> int:
    config, db = _context(args)
    monitor = HealthMonitor(ExperimentLedger(db), CashLedger(db, config.cash), config)
    summary = monitor.weekly_summary()
    print(format_weekly_summary(summary))
    if args.notify:
        build_notifier(config).send("MVV weekly summary", format_weekly_summary(summary))
    return 0


def cmd_approvals(args) -> int:
    config, db = _context(args)
    gate = HumanGate(db, build_notifier(config), config.gate, config)
    approvals = gate.history(args.limit) if args.all else gate.pending()
    rows = [
        {
            "id": a.id,
            "action": a.action,
            "exp": a.experiment_id if a.experiment_id is not None else "-",
            "amount": fmt_money(a.amount) if a.amount is not None else "-",
            "status": a.status,
            "reason": a.reason[:56],
        }
        for a in approvals
    ]
    print(_table(rows, ["id", "action", "exp", "amount", "status", "reason"]))
    if not args.all and rows:
        print("\napprove <id> --by <you>    |    reject <id> --by <you>")
    return 0


def cmd_approve(args) -> int:
    config, db = _context(args)
    gate = HumanGate(db, build_notifier(config), config.gate, config)
    approval = gate.approve(args.approval_id, args.by, args.notes)
    print(f"approval {approval.id} ({approval.action}) APPROVED by {approval.approved_by}")
    if approval.action == ApprovalAction.KILL_EXPERIMENT.value:
        print("run `tick` (or `evaluate`) to execute the kill")
    return 0


def cmd_reject(args) -> int:
    config, db = _context(args)
    gate = HumanGate(db, build_notifier(config), config.gate, config)
    approval = gate.reject(args.approval_id, args.by, args.notes)
    verb = "CONTINUE" if approval.action == ApprovalAction.KILL_EXPERIMENT.value else "REJECTED"
    print(f"approval {approval.id} ({approval.action}) {verb} by {approval.approved_by}")
    return 0


def cmd_resume(args) -> int:
    """Restart paused ads. Deliberately manual.

    The loop pauses ads on its own authority but never restarts them, so a
    stale approval can't quietly undo a kill trigger. Resuming names the
    approval that authorises the spend.
    """
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    cash = CashLedger(db, config.cash)
    gate = HumanGate(db, build_notifier(config), config.gate, config)

    exp = ledger.get(args.experiment_id)
    if exp is None:
        print(f"experiment {args.experiment_id} not found", file=sys.stderr)
        return 1
    if exp.pending_kill_reason:
        print(f"refusing: #{exp.id} has an undecided kill trigger "
              f"({exp.pending_kill_reason}). Decide it first.", file=sys.stderr)
        return 1

    budget = D(args.budget)
    approval = gate.get(args.approval_id)
    if not approval.is_approved:
        print(f"approval {approval.id} is {approval.status}, not approved", file=sys.stderr)
        return 1
    if approval.experiment_id != exp.id:
        print(f"approval {approval.id} is for experiment {approval.experiment_id}", file=sys.stderr)
        return 1
    if approval.amount is not None and budget > approval.amount:
        print(f"approval {approval.id} covers {fmt_money(approval.amount)}/day, "
              f"asked for {fmt_money(budget)}/day", file=sys.stderr)
        return 1
    ok, why = cash.can_spend(budget)
    if not ok:
        print(f"refusing: {why}", file=sys.stderr)
        return 1

    updated = ledger.resume_ads(exp.id, budget, approval.id)
    print(f"#{updated.id} ads resumed at {fmt_money(updated.daily_ad_budget)}/day "
          f"under approval {approval.id}")
    return 0


def cmd_cash(args) -> int:
    config, db = _context(args)
    cash = CashLedger(db, config.cash)
    if args.cash_command == "open":
        entry = cash.open_account(args.amount, once=args.once)
        if entry is None:
            print("cash ledger already open — leaving the balance alone")
        else:
            print(f"opening balance {fmt_money(entry.amount)} recorded")
    elif args.cash_command == "add":
        amount = D(args.amount)
        entry = (
            cash.credit(args.description, amount, args.category)
            if amount >= 0
            else cash.debit(args.description, amount, args.category)
        )
        print(f"recorded {fmt_money(entry.amount)} [{entry.category}] {entry.description}")
    status = cash.status()
    print(f"\ntotal balance    {fmt_money(status['total_balance'])}")
    print(f"available cash   {fmt_money(status['available_cash'])}")
    print(f"held             {fmt_money(status['held'])}")
    print(f"emergency buffer {fmt_money(status['emergency_buffer'])}")
    print(f"spendable        {fmt_money(status['spendable'])}")
    if status["negative"]:
        print("\n!! CASH IS NEGATIVE — Phase 1 cash survival criterion failed")
    elif status["buffer_breached"]:
        print("\n!! emergency buffer breached — no further spend should be approved")
    if status["upcoming_releases"]:
        print("\nupcoming releases:")
        for r in status["upcoming_releases"][:10]:
            print(f"  {r['release_date']:%Y-%m-%d}  {fmt_money(r['amount'])}  {r['description']}")
    return 0


def cmd_order(args) -> int:
    """Record a real order and its cash consequences (spec 3.2 + 7)."""
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    cash = CashLedger(db, config.cash)
    exp = ledger.get(args.experiment_id)
    if exp is None:
        print(f"experiment {args.experiment_id} not found", file=sys.stderr)
        return 1
    econ = EconomicCalculator(config.cash, config.discovery)
    customer_paid = D(args.customer_paid if args.customer_paid is not None else exp.selling_price)
    supplier_cost = D(args.supplier_cost if args.supplier_cost is not None else exp.unit_cost)

    order = Order(
        experiment_id=exp.id,
        external_id=args.external_id,
        customer_paid=customer_paid,
        supplier_cost=supplier_cost,
        shipping_cost=D(args.shipping_cost or 0),
        ad_cost=D(args.ad_cost or 0),
        payment_processor_fee=econ.processor_fee(customer_paid),
        delivered=bool(args.days_to_delivery),
        days_to_delivery=args.days_to_delivery,
    )
    with db.transaction():
        ledger.record_order(order)
        cash.record_customer_payment(customer_paid, experiment_id=exp.id, order_id=order.id)
        cash.debit(
            f"Supplier: {exp.product_name}",
            supplier_cost + D(args.shipping_cost or 0),
            CashCategory.SUPPLIER_PAYMENT.value,
            experiment_id=exp.id,
            order_id=order.id,
        )
    print(f"order {order.id} recorded, net contribution {fmt_money(order.net_contribution)}")
    print(f"available cash now {fmt_money(cash.available_cash())}")
    # Aggregate metrics are left alone: they may be coming from `mvv metrics`
    # instead, and overwriting them from a partial orders table would corrupt
    # the numbers the kill logic runs on.
    print(f"experiment metrics unchanged — run `recompute {exp.id}` if the orders "
          f"table is your source of truth for orders and revenue")
    return 0


def cmd_recompute(args) -> int:
    config, db = _context(args)
    ledger = ExperimentLedger(db)
    exp = ledger.recompute_from_orders(args.experiment_id, allow_decrease=args.force)
    print(f"#{exp.id} recomputed from {exp.orders} order(s): revenue {fmt_money(exp.revenue)}, "
          f"refunds {exp.refunds}, avg delivery {exp.avg_delivery_days} days")
    print("(ad spend is platform data and was not touched)")
    return 0


def cmd_tick(args) -> int:
    config, db = _context(args)
    provider = build_metrics_provider(
        args.metrics_source, Path(args.metrics_file) if args.metrics_file else None
    )
    orch = Orchestrator(db, config, metrics_provider=provider)
    report = orch.tick(discover_limit=args.limit)
    print("\n".join(report.summary_lines()))
    print()
    print(format_alerts(report.alerts))
    print(f"\nstop condition: {report.stop_reason}")
    pending = orch.gate.pending()
    if pending:
        print(f"\n{len(pending)} approval(s) waiting — run `approvals`")
    return 2 if report.stop_triggered else 0


def cmd_run(args) -> int:  # pragma: no cover - long-running
    config, db = _context(args)
    provider = build_metrics_provider(
        args.metrics_source, Path(args.metrics_file) if args.metrics_file else None
    )
    Orchestrator(db, config, metrics_provider=provider).run_forever(args.interval)
    return 0


def cmd_export(args) -> int:
    """Export the ledger for analysis (spec 11.2 lists pandas).

    Reporting only. Money in the export is a string, not a float — the CSV is
    for looking at, and anything that has to *decide* reads the ledger through
    src/money.py instead.
    """
    config, db = _context(args)
    rows = db.query(f"SELECT * FROM {args.table} ORDER BY id")
    if not rows:
        print(f"{args.table}: no rows")
        return 0

    try:
        import pandas as pd
    except ImportError:
        import csv

        with open(args.out, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.out} (csv module; pandas not installed)")
        return 0

    frame = pd.DataFrame(rows)
    frame.to_csv(args.out, index=False)
    print(f"wrote {len(frame)} rows x {len(frame.columns)} columns to {args.out}")
    if args.table == "experiments" and "status" in frame:
        print("\nby status:")
        print(frame["status"].value_counts().to_string())
    return 0


def cmd_court_review(args) -> int:
    config, db = _context(args)
    court = FileCourt(db, config.court)
    case = court.process_file(args.file)
    score = case.risk_score if case.risk_score is not None else "n/a"
    print(f"{case.file_name}: {case.verdict}  (risk {score})")
    print(f"  {case.summary}")
    for outcome in case.outcomes:
        mark = "ok  " if outcome.counted else "SKIP"
        detail = str(outcome.score) if outcome.counted else outcome.error
        print(f"  [{mark}] {outcome.backend}: {detail}")
    if case.unreviewed:
        print("\nNo reviewer ran. This file is UNREVIEWED, not cleared —")
        print("run `court doctor` to see what is missing.")
    return 0


def cmd_court_docket(args) -> int:
    config, db = _context(args)
    rows = FileCourt(db, config.court).cases(args.limit)
    print(
        _table(
            [
                {
                    "id": r["id"],
                    "file": str(r["file_name"])[:36],
                    "verdict": r["verdict"],
                    "risk": r["risk_score"] if r["risk_score"] is not None else "-",
                }
                for r in rows
            ],
            ["id", "file", "verdict", "risk"],
        )
    )
    return 0


def cmd_court_pending(args) -> int:
    config, db = _context(args)
    rows = FileCourt(db, config.court).pending(args.limit)
    if not rows:
        print("nothing pending")
        return 0
    print(
        _table(
            [
                {"id": r["id"], "file": str(r["file_name"])[:40], "summary": str(r["summary"])[:48]}
                for r in rows
            ],
            ["id", "file", "summary"],
        )
    )
    print("\nThese need a reviewer you have to trigger yourself, in Claude Code:")
    print("  /tribunal                        adversarial review")
    print("  /roundtable:agent-review-panel   multi-agent jury")
    return 0


def cmd_court_watch(args) -> int:
    from .court.watcher import Watcher

    config, db = _context(args)
    watcher = Watcher(FileCourt(db, config.court), config.court)
    if args.once:
        cases = watcher.process_once()
        print(f"{len(cases)} file(s) processed")
        return 0
    watcher.run_forever()  # pragma: no cover - long-running
    return 0


def cmd_court_doctor(args) -> int:
    """Say plainly which reviewers can actually run. No silent defaults."""
    from .court.backends import build_backends

    config, _ = _context(args)
    backends = build_backends(config.court)
    usable = 0
    for tier in ("tier1", "tier2", "tier3"):
        for backend in backends.get(tier, []):
            ok, why = backend.is_available()
            usable += 1 if ok else 0
            print(f"[{'OK ' if ok else '   '}] {tier} {backend.name}: {why or 'ready'}")
    print()
    if usable:
        print(f"{usable} reviewer(s) can score a file.")
    else:
        print("No reviewer can score a file — every case will come back")
        print("UNREVIEWED until you install one. Start with CodeTribunal:")
        print("  git clone https://huggingface.co/spaces/amine-yagoub/CodeTribunal")
        print(f"  (expected at {config.court.codetribunal_path})")
    return 0


def cmd_serve(args) -> int:  # pragma: no cover - long-running server
    """Serve the web approval gate (spec component 4, section 4.4)."""
    try:
        import uvicorn
    except ImportError:
        print(
            "the web UI needs fastapi and uvicorn: pip install -r requirements.txt\n"
            "(the CLI gate works without them: `mvv approvals`, `mvv approve <id>`)",
            file=sys.stderr,
        )
        return 1

    config, _ = _context(args)
    print(f"Approval gate    http://{args.host}:{args.port}/")
    print(f"Mission Control  http://{args.host}:{args.port}/village")
    print(f"  (db: {config.database_url})")
    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "WARNING: this page has no authentication and anyone who can reach it can "
            f"approve spending. You are binding to {args.host}.",
            file=sys.stderr,
        )
    uvicorn.run("src.agents.web:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_config(args) -> int:
    config, db = _context(args)
    print(f"database_url        {config.database_url}")
    print(f"scout_source        {config.scout_source}")
    print(f"approved_platforms  {', '.join(config.gate.approved_platforms)}")
    print(f"initial_budget      {fmt_money(config.cash.initial_budget)}")
    print(f"emergency_buffer    {fmt_money(config.cash.emergency_buffer)}")
    print(f"scale approval > {fmt_money(config.gate.scale_approval_daily_threshold)}/day")
    print(f"min margin to test  {config.discovery.min_margin_pct}%")
    print(f"KILL_ALL after      {config.stop.kill_all_after_experiments} experiments")
    print(f"block on approval   {config.gate.block_on_approval}")
    return 0


# =========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mvv", description="Minimum Viable Village — Phase 1")
    parser.add_argument("--database-url", help="override DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables").set_defaults(func=cmd_init_db)
    sub.add_parser("config", help="show effective configuration").set_defaults(func=cmd_config)

    p = sub.add_parser("scout", help="discover products (writes nothing)")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_scout)

    p = sub.add_parser("experiment", help="enter products by hand")
    exp_sub = p.add_subparsers(dest="experiment_command", required=True)
    c = exp_sub.add_parser("add", help="record a real product you found yourself")
    c.add_argument("--name", required=True, help="product name")
    c.add_argument("--cost", required=True, help="supplier unit cost, e.g. 12.50")
    c.add_argument("--price", help="your selling price (omit to get a suggestion)")
    c.add_argument("--shipping", default="0", help="per-unit shipping cost")
    c.add_argument("--supplier", help="supplier name (defaults to the platform)")
    c.add_argument("--platform", required=True, help="source platform, e.g. temu")
    c.add_argument("--url", help="listing URL, for the audit trail")
    c.add_argument("--product-id", dest="product_id", help="override the generated id")
    c.add_argument(
        "--force",
        action="store_true",
        help="record it even if the economic filter says it is not worth testing",
    )
    c.set_defaults(func=cmd_experiment_add)

    p = sub.add_parser("list", help="list experiments")
    p.add_argument("--status", nargs="*", default=None)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="full audit view of one experiment")
    p.add_argument("experiment_id", type=int)
    p.add_argument("--events", type=int, default=10)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("metrics", help="record observed metrics")
    p.add_argument("experiment_id", type=int)
    p.add_argument("--impressions", type=int)
    p.add_argument("--clicks", type=int)
    p.add_argument("--sessions", type=int)
    p.add_argument("--orders", type=int)
    p.add_argument("--revenue", type=Decimal)
    p.add_argument("--ad-spend", dest="ad_spend", type=Decimal)
    p.add_argument("--refunds", type=int)
    p.add_argument("--chargebacks", type=int)
    p.add_argument("--avg-delivery-days", dest="avg_delivery_days", type=Decimal)
    p.add_argument("--add", action="store_true", help="increment instead of replace")
    p.set_defaults(func=cmd_metrics)

    sub.add_parser("evaluate", help="run kill logic now").set_defaults(func=cmd_evaluate)
    sub.add_parser("health", help="daily health check").set_defaults(func=cmd_health)

    p = sub.add_parser("weekly", help="weekly summary report")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("approvals", help="list approval requests")
    p.add_argument("--all", action="store_true", help="include decided requests")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_approvals)

    p = sub.add_parser("approve", help="approve a request (KILL on a kill request)")
    p.add_argument("approval_id", type=int)
    p.add_argument("--by", default="operator")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="reject a request (CONTINUE on a kill request)")
    p.add_argument("approval_id", type=int)
    p.add_argument("--by", default="operator")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("resume", help="restart paused ads under a named approval")
    p.add_argument("experiment_id", type=int)
    p.add_argument("--approval-id", dest="approval_id", type=int, required=True)
    p.add_argument("--budget", type=Decimal, required=True, help="daily ad budget")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("cash", help="cash flow ledger")
    p.add_argument("cash_command", nargs="?", default="status", choices=["status", "open", "add"])
    p.add_argument("--amount", type=Decimal)
    p.add_argument("--once", action="store_true",
                   help="no-op if the ledger is already open (for startup scripts)")
    p.add_argument("--description", default="manual entry")
    p.add_argument("--category", default=CashCategory.OTHER.value)
    p.set_defaults(func=cmd_cash)

    p = sub.add_parser("order", help="record a real order")
    p.add_argument("experiment_id", type=int)
    p.add_argument("--external-id", dest="external_id")
    p.add_argument("--customer-paid", dest="customer_paid", type=Decimal)
    p.add_argument("--supplier-cost", dest="supplier_cost", type=Decimal)
    p.add_argument("--shipping-cost", dest="shipping_cost", type=Decimal)
    p.add_argument("--ad-cost", dest="ad_cost", type=Decimal)
    p.add_argument("--days-to-delivery", dest="days_to_delivery", type=int)
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("recompute", help="rebuild order metrics from the orders table")
    p.add_argument("experiment_id", type=int)
    p.add_argument("--force", action="store_true",
                   help="overwrite even if it lowers the recorded order count")
    p.set_defaults(func=cmd_recompute)

    p = sub.add_parser("tick", help="run one pass of the orchestrator loop")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--metrics-source", default="manual", choices=["manual", "file"])
    p.add_argument("--metrics-file")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("export", help="export a table to CSV for analysis")
    p.add_argument(
        "table",
        nargs="?",
        default="experiments",
        choices=["experiments", "orders", "cash_flow", "human_approvals", "experiment_events"],
    )
    p.add_argument("--out", default="export.csv")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("court", help="File Court — review a file, or the docket")
    court_sub = p.add_subparsers(dest="court_command", required=True)
    c = court_sub.add_parser("review", help="put one file through the court")
    c.add_argument("file")
    c.set_defaults(func=cmd_court_review)
    c = court_sub.add_parser("docket", help="recent cases")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(func=cmd_court_docket)
    c = court_sub.add_parser("pending", help="cases still awaiting a reviewer")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(func=cmd_court_pending)
    c = court_sub.add_parser("watch", help="watch uploads/ and review what lands")
    c.add_argument("--once", action="store_true", help="one sweep, then exit")
    c.set_defaults(func=cmd_court_watch)
    c = court_sub.add_parser("doctor", help="report which reviewers are installed")
    c.set_defaults(func=cmd_court_doctor)

    p = sub.add_parser("serve", help="serve the web approval gate")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("run", help="run the loop forever")
    p.add_argument("--interval", type=int)
    p.add_argument("--metrics-source", default="manual", choices=["manual", "file"])
    p.add_argument("--metrics-file")
    p.set_defaults(func=cmd_run)

    # The Trading Edition (src/trading). Same database, same approvals table,
    # same asymmetry: `mvv approve` is still the only place permission is
    # granted, whether the subject is a product or a firm.
    add_trade_parser(sub)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LedgerError as exc:
        print(f"ledger refused: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
