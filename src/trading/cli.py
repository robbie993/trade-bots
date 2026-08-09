"""The trading CLI — ``python -m src.main trade <command>``.

Same contract as the product village's CLI: every command that could move
money either prints what it would do, or writes an approval request. The only
commands that grant permission are ``approve``/``reject`` in the main CLI, and
``trade apply-approvals``, which does nothing except carry out decisions a
human has already made.

    trade init                 create firms from config/firm_config.yaml
    trade firms                what exists, and how it is doing
    trade show <firm>          one firm in full: kill table, positions, debate
    trade tick                 one pass of the whole ecosystem
    trade run                  the loop, forever
    trade simulate             replay N bars of history through the whole loop
    trade leaderboard          the ranking, with its evidence
    trade allocations          who holds what capital
    trade kill-status          every kill condition, per firm
    trade reconcile            do the books add up?
    trade backtest             every firm over the configured history
    trade evolve               one generation of genome evolution
    trade memory               what the brain remembers
    trade audit                the full audit report
    trade status               one-screen health check
    trade monitor              status, repeatedly
    trade court-submit <file>  put a strategy file on trial
    trade court-docket         recent strategy cases
    trade court-case <id>      one case in full, juror by juror
    trade tokens               the token standings (points, not capital)
    trade season               run every bout, award milestones
    trade market               what firms have for sale
    trade market-buy           buy a listing (capital needs an approval)
    trade sandbox              alliances, intrigue, shadow scoreboard
    trade frameworks           which external frameworks are installed
    trade live-request         ask for permission to trade a live venue
    trade apply-approvals      carry out what a human approved
"""

from __future__ import annotations

import time
from typing import Optional

from ..config import Config
from ..db.connection import Database
from ..money import D, fmt_money, fmt_pct
from .adapters import render_survey
from .black_market import ASSET_TYPES
from .brokerage.reconciliation import LedgerNotReconciled
from .competition.arena import METRICS
from .config import TradingConfig
from .ecosystem import Ecosystem
from .firms.kill_switch import kill_check_table
from .firms.spec import FirmSpecError


def _table(rows: list, columns: Optional[list] = None) -> str:
    if not rows:
        return "(none)"
    columns = columns or list(rows[0].keys())
    widths = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = "  ".join(str(c).ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    body = ["  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows]
    return "\n".join([header, sep, *body])


def _ecosystem(args) -> Ecosystem:
    app_config = Config()
    if getattr(args, "database_url", None):
        app_config = Config(database_url=args.database_url)
    db = Database.from_url(app_config.database_url)
    trading = TradingConfig()
    if getattr(args, "firms_config", None):
        trading = TradingConfig(firms_config=args.firms_config)
    return Ecosystem(db, trading, app_config)


# =========================================================================
# commands
# =========================================================================
def cmd_init(args) -> int:
    eco = _ecosystem(args)
    eco.db.init_schema()
    try:
        firms = eco.init_firms()
    except FirmSpecError as exc:
        print(f"config error: {exc}")
        return 1
    rows = [
        {
            "firm": f.firm_key,
            "name": f.name[:28],
            "class": f.asset_class,
            "venue": f.venue,
            "allocation": fmt_money(f.allocation),
            "risk/trade": fmt_pct(D(f.risk_limit) * 100),
            "universe": ",".join(f.universe[:6]),
        }
        for f in firms
    ]
    print(_table(rows))
    print(
        f"\n{len(firms)} firm(s) ready. Nothing has traded yet — run `trade tick`.\n"
        "Existing firms keep the capital they already had; `init` never re-funds one."
    )
    return 0


def cmd_firms(args) -> int:
    eco = _ecosystem(args)
    market = eco.market()
    firms = eco.store.firms()
    if not firms:
        print("no firms. Run `trade init`.")
        return 0
    cards = {c.firm_id: c for c in eco.brokerage.evaluator.evaluate_all(firms, market)}
    rows = []
    for firm in firms:
        card = cards.get(firm.id)
        rows.append(
            {
                "firm": firm.firm_key,
                "status": firm.status,
                "venue": firm.venue,
                "allocation": fmt_money(firm.allocation),
                "cash": fmt_money(firm.cash),
                "equity": fmt_money(card.equity) if card else "-",
                "return": fmt_pct(card.return_pct) if card else "-",
                "score": f"{card.score}" if card and card.sufficient_data else
                         (f"({card.score})" if card else "-"),
                "trades": card.closed_trades if card else 0,
                "positions": len(eco.store.positions(firm.id)),
            }
        )
    print(_table(rows))
    return 0


def cmd_show(args) -> int:
    eco = _ecosystem(args)
    firm = eco.store.get_firm(args.firm)
    if firm is None:
        print(f"unknown firm {args.firm}")
        return 1
    market = eco.market()
    card = eco.brokerage.evaluator.evaluate(firm, market)

    purse = eco.store.cash_view(firm, market, eco.config.firm.cash_floor_pct)
    print(f"{firm.firm_key} — {firm.name} [{firm.status}]")
    print(f"  asset class : {firm.asset_class or '-'}   strategy: {firm.strategy or '-'}")
    print(f"  venue       : {firm.venue}")
    print(f"  allocation  : {fmt_money(firm.allocation)}")
    # Two different questions, two different answers: what the risk manager
    # may deploy, and what the brokerage may take back.
    print(
        f"  cash        : {fmt_money(purse.cash)} "
        f"(available {fmt_money(purse.available)}, reserve {fmt_money(purse.reserve)}, "
        f"withdrawable {fmt_money(purse.withdrawable)})"
    )
    print(f"  in positions: {fmt_money(purse.in_positions)}")
    print(f"  equity      : {fmt_money(card.equity)}   return: {fmt_pct(card.return_pct)}")
    print(f"  score       : {card.score}" + ("" if card.sufficient_data else "  (below sample gate)"))
    print(f"  genome      : {firm.genome}")
    if firm.kill_reason:
        print(f"  killed      : {firm.kill_reason}")

    print("\nKill conditions:")
    print(_table(kill_check_table(card.to_metrics(), eco.config.kill)))

    positions = eco.store.positions(firm.id)
    print("\nPositions:")
    print(
        _table(
            [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "avg price": p.avg_price,
                    "mark": market.mark(p.symbol),
                    "value": fmt_money(p.market_value(market.mark(p.symbol))),
                    "unrealised": fmt_money(p.unrealized_pnl(market.mark(p.symbol))),
                }
                for p in positions
            ]
        )
    )

    print("\nRecent proposals:")
    print(
        _table(
            [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "qty": p.quantity,
                    "conf": p.confidence,
                    "risk": p.risk_verdict,
                    "ethics": p.ethics_verdict,
                    "status": p.status,
                }
                for p in eco.store.proposals(firm.id, limit=args.limit)
            ]
        )
    )
    return 0


def cmd_tick(args) -> int:
    eco = _ecosystem(args)
    report = eco.tick()
    print(report.summary())
    return 1 if report.errors else 0


def cmd_run(args) -> int:  # pragma: no cover - long-running
    eco = _ecosystem(args)
    interval = args.interval or eco.config.tick_interval_s
    print(f"running every {interval}s — Ctrl-C to stop")
    while True:
        try:
            print(eco.tick().summary(), flush=True)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0
        except Exception as exc:  # noqa: BLE001 - the loop must survive one bad tick
            print(f"tick failed: {exc}", flush=True)
        time.sleep(interval)


def cmd_simulate(args) -> int:
    eco = _ecosystem(args)
    reports = eco.simulate(args.days)
    if not reports:
        print("not enough market history to simulate")
        return 1
    filled = sum(r.filled for r in reports)
    proposals = sum(r.proposals for r in reports)
    blocked = sum(r.blocked_by_ethics + r.blocked_by_risk for r in reports)
    print(
        f"{len(reports)} tick(s): {proposals} proposal(s), {filled} filled, {blocked} blocked"
    )
    for report in reports:
        if report.oversight is None:
            continue
        for paused in report.oversight.paused:
            print(f"  PAUSED {paused['firm']}: {paused['reason']}")
        for change in report.oversight.allocation_changes:
            print(f"  {change}")
        if report.oversight.halted:
            print(f"  HALTED: {report.oversight.halted['reason']}")
    for error in {e for r in reports for e in r.errors}:
        print(f"  ERROR: {error}")
    print()
    print(eco.brokerage.leaderboard(eco.market()).render())
    return 0


def cmd_leaderboard(args) -> int:
    eco = _ecosystem(args)
    print(eco.brokerage.leaderboard(eco.market()).render())
    return 0


def cmd_allocations(args) -> int:
    eco = _ecosystem(args)
    firms = eco.store.firms()
    rows = [
        {
            "firm": f.firm_key,
            "status": f.status,
            "initial": fmt_money(f.initial_allocation),
            # Net capital still with the firm. A killed firm that returned more
            # than it was given shows a negative allocation, which is what the
            # ledger says; "returned" spells the same fact out in plain terms.
            "allocation": fmt_money(f.allocation),
            "returned": fmt_money(max(D(0), D(f.initial_allocation) - D(f.allocation))),
            "cash": fmt_money(f.cash),
            # What the brokerage could take back on the next pass.
            "withdrawable": fmt_money(
                eco.store.cash_view(f, cash_floor_pct=eco.config.firm.cash_floor_pct).withdrawable
            ),
            "vs initial": fmt_pct(
                (D(f.allocation) - D(f.initial_allocation))
                / D(f.initial_allocation)
                * D(100)
            )
            if f.initial_allocation
            else "-",
        }
        for f in firms
    ]
    print(_table(rows))
    deployed = sum((D(f.allocation) for f in firms if not f.is_killed), D(0))
    print(f"\ntotal deployed: {fmt_money(deployed)} "
          f"of {fmt_money(eco.config.brokerage.total_capital)} available "
          f"(killed firms excluded)")
    events = eco.store.events(limit=10, event_type="allocation")
    if events:
        print("\nrecent changes:")
        for event in events:
            print(f"  {event.get('created_at')}  {event.get('detail')}")
    return 0


def cmd_kill_status(args) -> int:
    eco = _ecosystem(args)
    market = eco.market()
    for firm in eco.store.firms():
        card = eco.brokerage.evaluator.evaluate(firm, market)
        print(f"\n{firm.firm_key} [{firm.status}]"
              + ("" if card.sufficient_data else "  (below sample gate — no verdict yet)"))
        print(_table(kill_check_table(card.to_metrics(), eco.config.kill)))
    print("\nEcosystem:")
    cards = eco.brokerage.evaluator.evaluate_all(eco.store.firms(), market)
    halted, reason, state = eco.brokerage.kill_switch.check(cards)
    print(f"  firms {state.firms} (active {state.active}, killed {state.killed})")
    print(f"  equity {fmt_money(state.equity)} of {fmt_money(state.capital)} deployed "
          f"— drawdown {fmt_pct(state.drawdown_pct)}")
    print(f"  KILL_ALL: {'TRIGGERED — ' + reason if halted else 'not triggered'}")
    return 0


def cmd_reconcile(args) -> int:
    eco = _ecosystem(args)
    report = eco.brokerage.reconcile(eco.market())
    print(report.summary())
    return 0 if report.ok else 1


def cmd_backtest(args) -> int:
    eco = _ecosystem(args)
    results = eco.backtest(args.firm, args.days)
    if not results:
        print("no firms to backtest. Run `trade init`.")
        return 0
    for result in results:
        print(result.summary())
    return 0


def cmd_evolve(args) -> int:
    eco = _ecosystem(args)
    try:
        generations = eco.evolve(args.firm, args.generations)
    except LedgerNotReconciled as exc:
        print(f"refusing to evolve: {exc}")
        return 1
    if not generations:
        print("nothing to evolve")
        return 0
    for generation in generations:
        print(generation.summary())
    return 0


def cmd_memory(args) -> int:
    eco = _ecosystem(args)
    if args.search:
        memories = eco.memory.search(args.search, limit=args.limit)
    else:
        memories = eco.memory.recall(symbol=args.symbol, limit=args.limit)
    if not memories:
        print("(nothing remembered yet)")
        return 0
    for memory in memories:
        print(f"  {memory}")
    lessons = eco.memory.lessons(limit=10)
    if lessons:
        print("\nLessons:")
        for lesson in lessons:
            print(f"  - {lesson.summary}")
    return 0


def cmd_audit(args) -> int:
    eco = _ecosystem(args)
    report = eco.audit_report()
    if args.write:
        path = eco.audit.log_note("Audit report", report)
        eco.audit.rebuild_index()
        print(f"written to {path}")
    else:
        print(report)
    return 0


def cmd_status(args) -> int:
    eco = _ecosystem(args)
    status = eco.status()
    print(f"as of        : {status['as_of']}  (data: {status['data_source']})")
    print(f"firms        : {status['firms']} "
          f"(active {status['active']}, paused {status['paused']}, killed {status['killed']})")
    print(f"capital      : {fmt_money(status['capital'])} deployed")
    print(f"equity       : {fmt_money(status['equity'])}")
    print(f"reconciled   : {'yes' if status['reconciled'] else 'NO — ' + status['reconciliation']}")
    print(f"approvals    : {status['pending_approvals']} pending")
    gateway = status["gateway"]
    print("gateway      : "
          + ("disabled (deterministic narration)" if not gateway["enabled"]
             else ("reachable" if gateway["reachable"] else f"UNREACHABLE — {gateway.get('detail')}")))
    return 0


def cmd_monitor(args) -> int:  # pragma: no cover - long-running
    while True:
        cmd_status(args)
        if not args.watch:
            return 0
        print("-" * 60, flush=True)
        time.sleep(args.interval)


# =========================================================================
# the strategy court
# =========================================================================
def cmd_court_submit(args) -> int:
    from .court import EvidenceError

    eco = _ecosystem(args)
    try:
        case = eco.try_strategy(
            args.file, firm_key=args.firm or "", submitted_by=args.by or ""
        )
    except EvidenceError as exc:
        print(f"cannot read that as a strategy: {exc}")
        return 1
    print(case.transcript())
    return 0 if case.ruling.verdict != "reject" else 1


def cmd_court_docket(args) -> int:
    eco = _ecosystem(args)
    rows = eco.court.docket(args.limit)
    if not rows:
        print("(no cases yet — `trade court-submit <file>`)")
        return 0
    print(
        _table(
            [
                {
                    "id": r["id"],
                    "file": (r.get("file_name") or "")[:28],
                    "ruling": (r.get("ruling") or "").upper(),
                    "conf": r.get("confidence"),
                    "fitness": r.get("fitness"),
                    "genome": r.get("genome_id") or "-",
                    "when": (r.get("created_at") or "")[:16],
                }
                for r in rows
            ]
        )
    )
    print("\nACCEPT admits a CANDIDATE genome. Nothing trades because the court liked it.")
    return 0


def cmd_court_case(args) -> int:
    eco = _ecosystem(args)
    row = eco.court.case(args.id)
    if row is None:
        print(f"no case {args.id}")
        return 1
    import json as _json

    print(f"CASE #{row['id']} — {row['file_name']}  sha256 {(row['file_sha256'] or '')[:16]}…")
    print(f"  ruling: {(row['ruling'] or '').upper()} (confidence {row['confidence']})")
    print(f"  reason: {row['ruling_reason']}\n")
    try:
        findings = _json.loads(row.get("findings") or "[]")
    except ValueError:
        findings = []
    print(
        _table(
            [
                {
                    "juror": f["juror"],
                    "verdict": f["verdict"],
                    "weight": f["weight"],
                    "veto": "yes" if f.get("veto") else "",
                    "reason": f["reason"][:60],
                }
                for f in findings
            ]
        )
    )
    print(f"\n{row.get('prosecution') or ''}\n\n{row.get('defence') or ''}")
    return 0


def cmd_court_watch(args) -> int:
    eco = _ecosystem(args)
    cases = eco.court.watch(args.dir)
    if not cases:
        print("nothing to review (drop .py/.yaml/.json strategies into the directory)")
        return 0
    for case in cases:
        print(f"{case.evidence.name:28} {case.verdict.upper():7} {case.ruling.reason[:70]}")
    return 0


# =========================================================================
# competition
# =========================================================================
def cmd_tokens(args) -> int:
    eco = _ecosystem(args)
    print(eco.arena.render())
    events = eco.tokens.events(args.firm, limit=args.limit)
    if events:
        print("\nrecent:")
        for event in events:
            print(f"  {(event['created_at'] or '')[:16]}  {event['firm_key']:<14} "
                  f"{str(event['amount']):>8}  {event['reason']}")
    return 0


def cmd_season(args) -> int:
    eco = _ecosystem(args)
    result = eco.run_season(args.metric)
    for fight in result["bouts"]:
        print(f"  {fight}")
    for firm, milestone, amount in result["milestones"]:
        print(f"  MILESTONE {firm}: {milestone} (+{amount})")
    print()
    print(eco.arena.render())
    return 0


def cmd_bout(args) -> int:
    eco = _ecosystem(args)
    cards = eco.brokerage.evaluator.evaluate_all(eco.store.firms(), eco.market())
    print(f"  {eco.arena.bout(args.challenger, args.opponent, cards, args.metric)}")
    return 0


# =========================================================================
# the black market
# =========================================================================
def cmd_market(args) -> int:
    eco = _ecosystem(args)
    listings = eco.black_market.listings(args.status)
    if listings:
        print(_table([
            {
                "id": listing.id,
                "seller": listing.seller,
                "asset": listing.asset_type,
                "title": listing.title[:28],
                "price": f"{listing.price} {listing.currency}",
                "status": listing.status,
            }
            for listing in listings
        ]))
    else:
        print(f"(no {args.status} listings)")
    pending = eco.black_market.pending_transfers()
    if pending:
        print("\ncapital transfers awaiting approval (no capital has moved):")
        for row in pending:
            print(f"  transaction #{row['id']}  {row['seller']} -> {row['buyer']}  "
                  f"{fmt_money(D(row['price']))}  approval #{row['approval_id']}")
    return 0


def cmd_market_sell(args) -> int:
    from .black_market import MarketError

    eco = _ecosystem(args)
    try:
        listing = eco.list_asset(
            args.seller, args.asset, args.price, title=args.title or ""
        )
    except MarketError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"listed: {listing}")
    if listing.currency == "cash":
        print("  capital listings settle only through an approved transfer")
    return 0


def cmd_market_buy(args) -> int:
    from .black_market import MarketError

    eco = _ecosystem(args)
    try:
        result = eco.buy_listing(args.buyer, args.listing)
    except MarketError as exc:
        print(f"refused: {exc}")
        return 1
    if result["settled"]:
        print(f"settled: transaction #{result['transaction']}")
        delivery = result.get("delivery") or {}
        if delivery.get("kind") == "genome":
            print(f"  candidate genome #{delivery['genome_id']} — {delivery['note']}")
    else:
        print(f"transaction #{result['transaction']} opened; {result['note']}")
        print(f"  python -m src.main approve {result['approval_id']} --by you")
    return 0


def cmd_market_settle(args) -> int:
    from .black_market import MarketError

    eco = _ecosystem(args)
    try:
        result = eco.black_market.settle_capital_transfer(args.transaction)
    except MarketError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"transferred {fmt_money(D(result['amount']))} from {result['from']} to {result['to']}")
    print(eco.brokerage.reconcile(eco.market()).summary())
    return 0


# =========================================================================
# the sandbox
# =========================================================================
def cmd_sandbox(args) -> int:
    eco = _ecosystem(args)
    print(eco.sandbox.render())
    alliances = eco.sandbox.alliances.all()
    if alliances:
        print("\nalliances:")
        for alliance in alliances:
            print(f"  {alliance}")
    events = eco.sandbox.events(args.limit)
    if events:
        print("\nevents:")
        for event in events:
            print(f"  [{event['event_type']}] {event['actor']} -> {event['target'] or '-'}: "
                  f"{(event['detail'] or '')[:80]}")
    print(
        "\nNothing here touches the ledger: the sandbox holds a read-only view of it "
        "and can write only its own two tables."
    )
    return 0


def cmd_sandbox_action(args) -> int:
    from .sandbox import SandboxViolation

    eco = _ecosystem(args)
    try:
        result = eco.intrigue(
            args.sandbox_action,
            args.actor,
            name=getattr(args, "name", "") or "",
            target=",".join(args.members) if args.sandbox_action == "form"
            else getattr(args, "target", "") or "",
        )
    except SandboxViolation as exc:
        print(f"refused: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - InsufficientTokens and friends
        print(f"refused: {exc}")
        return 1
    print(f"  {result}")
    return 0


def cmd_frameworks(args) -> int:
    print(render_survey(TradingConfig()))
    return 0


def cmd_live_request(args) -> int:
    eco = _ecosystem(args)
    approval = eco.request_live_trading(args.venue, args.reason or "")
    print(
        f"requested approval #{approval.id} to trade LIVE on {args.venue}.\n"
        "Nothing will be sent to the venue until this is approved:\n"
        f"  python -m src.main approve {approval.id} --by you"
    )
    return 0


def cmd_apply_approvals(args) -> int:
    eco = _ecosystem(args)
    applied = eco.apply_approvals()
    if not applied:
        print("nothing approved is waiting to be carried out")
        return 0
    for line in applied:
        print(f"  {line}")
    return 0


def cmd_resume(args) -> int:
    eco = _ecosystem(args)
    result = eco.brokerage.resume_firm(args.firm, args.by)
    print(f"{result['firm']} resumed by {result['by']}")
    return 0


# =========================================================================
# parser
# =========================================================================
def add_trade_parser(subparsers) -> None:
    """Attach `trade ...` to the main CLI's subparsers."""
    parser = subparsers.add_parser("trade", help="the trading ecosystem (multi-firm)")
    sub = parser.add_subparsers(dest="trade_command", required=True)

    def add(name: str, help_text: str, func):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--database-url", help="override DATABASE_URL")
        p.add_argument("--firms-config", help="override config/firm_config.yaml")
        p.set_defaults(func=func)
        return p

    add("init", "create firms from config", cmd_init)
    add("firms", "list firms and how they are doing", cmd_firms)

    p = add("show", "one firm in full", cmd_show)
    p.add_argument("firm")
    p.add_argument("--limit", type=int, default=10)

    add("tick", "run one pass of the ecosystem", cmd_tick)

    p = add("run", "run the loop forever", cmd_run)
    p.add_argument("--interval", type=int, default=0)

    p = add("simulate", "replay N bars through the whole ecosystem", cmd_simulate)
    p.add_argument("--days", type=int, default=30)

    add("leaderboard", "the ranking, with its evidence", cmd_leaderboard)
    add("allocations", "who holds what capital", cmd_allocations)
    add("kill-status", "every kill condition, per firm", cmd_kill_status)
    add("reconcile", "check the books add up", cmd_reconcile)

    p = add("backtest", "backtest firms on the configured history", cmd_backtest)
    p.add_argument("--firm")
    p.add_argument("--days", type=int, help="limit the number of bars")

    p = add("evolve", "one generation of genome evolution", cmd_evolve)
    p.add_argument("--firm")
    p.add_argument("--generations", type=int, default=1)

    p = add("memory", "what the brain remembers", cmd_memory)
    p.add_argument("--symbol")
    p.add_argument("--search")
    p.add_argument("--limit", type=int, default=20)

    p = add("audit", "the full audit report", cmd_audit)
    p.add_argument("--write", action="store_true", help="write into the Obsidian vault")

    add("status", "one-screen health check", cmd_status)

    p = add("monitor", "status, repeatedly", cmd_monitor)
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=60)

    # -- the strategy court ------------------------------------------------
    p = add("court-submit", "put a strategy file on trial", cmd_court_submit)
    p.add_argument("file")
    p.add_argument("--firm", help="whose universe to backtest against")
    p.add_argument("--by", help="who submitted it")

    p = add("court-docket", "recent strategy cases", cmd_court_docket)
    p.add_argument("--limit", type=int, default=20)

    p = add("court-case", "one case in full, juror by juror", cmd_court_case)
    p.add_argument("id", type=int)

    p = add("court-watch", "try every strategy file in a directory", cmd_court_watch)
    p.add_argument("--dir")

    # -- competition -------------------------------------------------------
    p = add("tokens", "the token standings (points, not capital)", cmd_tokens)
    p.add_argument("--firm")
    p.add_argument("--limit", type=int, default=10)

    p = add("season", "run every bout and award milestones", cmd_season)
    p.add_argument("--metric", default="score", choices=sorted(METRICS))

    p = add("bout", "one head-to-head", cmd_bout)
    p.add_argument("challenger")
    p.add_argument("opponent")
    p.add_argument("--metric", default="score", choices=sorted(METRICS))

    # -- the black market --------------------------------------------------
    p = add("market", "what is for sale", cmd_market)
    p.add_argument("--status", default="active")

    p = add("market-sell", "list an asset", cmd_market_sell)
    p.add_argument("seller")
    p.add_argument("--asset", required=True, choices=list(ASSET_TYPES))
    p.add_argument("--price", required=True)
    p.add_argument("--title")

    p = add("market-buy", "buy a listing", cmd_market_buy)
    p.add_argument("buyer")
    p.add_argument("listing", type=int)

    p = add("market-settle", "apply an approved capital transfer", cmd_market_settle)
    p.add_argument("transaction", type=int)

    # -- the sandbox -------------------------------------------------------
    p = add("sandbox", "alliances, intrigue and the shadow scoreboard", cmd_sandbox)
    p.add_argument("--limit", type=int, default=15)

    p = add("sandbox-form", "form an alliance", cmd_sandbox_action)
    p.add_argument("name")
    p.add_argument("actor")
    p.add_argument("members", nargs="*")
    p.set_defaults(sandbox_action="form")

    p = add("sandbox-betray", "break an alliance for a reward", cmd_sandbox_action)
    p.add_argument("actor")
    p.add_argument("name")
    p.set_defaults(sandbox_action="betray")

    p = add("sandbox-spy", "copy a rival's genome into the sandbox record", cmd_sandbox_action)
    p.add_argument("actor")
    p.add_argument("target")
    p.set_defaults(sandbox_action="spy")

    p = add("sandbox-sabotage", "attack a rival's shadow standing", cmd_sandbox_action)
    p.add_argument("actor")
    p.add_argument("target")
    p.set_defaults(sandbox_action="sabotage")

    add("frameworks", "which external frameworks are installed", cmd_frameworks)

    p = add("live-request", "ask for permission to trade a live venue", cmd_live_request)
    p.add_argument("--venue", required=True)
    p.add_argument("--reason")

    add("apply-approvals", "carry out what a human approved", cmd_apply_approvals)

    p = add("resume", "un-pause a firm (human decision)", cmd_resume)
    p.add_argument("firm")
    p.add_argument("--by", required=True)


__all__ = ["add_trade_parser"]
