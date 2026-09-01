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
from . import heartbeat
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
            # Read every pass, not at startup: the switch is flipped from a
            # different process, and a pause you have to restart to apply is
            # not a pause.
            # Stamped every pass, including a paused one: a paused loop is
            # still the process that owns this village, and the console must
            # not start ticking underneath it just because it went quiet.
            heartbeat.beat()
            if eco.settings.get("paused"):
                print("paused — nothing ticked", flush=True)
            else:
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
        brain = eco.log_brain()
        eco.audit.rebuild_index()
        print(f"written to {path}")
        print(f"brain: {brain['symbols']} symbol note(s), {brain['genomes']} new genome note(s)")
    else:
        print(report)
    return 0


def cmd_status(args) -> int:
    eco = _ecosystem(args)
    status = eco.status()
    print(f"as of        : {status['as_of']}  (data: {status['data_source']} "
          f"{eco.config.data.resolution.name} bars)")
    if status["as_of"] is None:
        # `as of: None` was the whole of what this said when an entire village
        # went blind, which is a symptom with no cause attached. The feed knows
        # exactly why it could not price each symbol; it just was not asked.
        print(_feed_trouble(eco))
    print(f"firms        : {status['firms']} "
          f"(active {status['active']}, paused {status['paused']}, killed {status['killed']})")
    print(f"capital      : {fmt_money(status['capital'])} deployed")
    print(f"equity       : {fmt_money(status['equity'])}")
    # An unpriceable holding contributes zero to equity, which reads as "worth
    # nothing" for a long and as free money for a short — the sale proceeds sit
    # in cash with no offsetting liability. Either way the total is not a
    # measurement, and a headline that does not say so is the same mistake as
    # killing a firm on it.
    blind = status.get("unmeasured") or []
    if blind:
        print(
            f"               NOT A MEASUREMENT — {len(blind)} firm(s) hold positions\n"
            f"               priced on nothing or on another feed: {', '.join(blind[:6])}\n"
            "               The total above counts them at zero. Re-run once the\n"
            "               feed has warmed up, or see `trade firms` per firm."
        )
    print(f"reconciled   : {'yes' if status['reconciled'] else 'NO — ' + status['reconciliation']}")
    print(f"approvals    : {status['pending_approvals']} pending")
    gateway = status["gateway"]
    print("gateway      : "
          + ("disabled (deterministic narration)" if not gateway["enabled"]
             else ("reachable" if gateway["reachable"] else f"UNREACHABLE — {gateway.get('detail')}")))
    return 0


def _feed_trouble(eco) -> str:
    """Ask the feed for one symbol and report what it actually says.

    Called only when there are no bars at all. One symbol is enough: if the
    credentials are wrong or the client is missing, every symbol fails the same
    way, and hammering a rate-limited API to say so twenty times would be its
    own kind of rude.
    """
    universe = eco.universe()
    if not universe:
        return "               no firms, so nothing to price. Run `trade init`."

    symbol = universe[0]
    try:
        bars = eco.feed.series(symbol)
    except Exception as exc:  # noqa: BLE001 - every feed has its own errors
        return (
            f"               NO PRICES. Asking for {symbol} gave:\n"
            f"                 {type(exc).__name__}: {str(exc)[:300]}\n"
            "               Every firm is blind until this resolves. Nothing will\n"
            "               be killed for it — see `trade revive` if something\n"
            "               already was."
        )
    if not bars:
        return (
            f"               NO PRICES. The feed answered for {symbol} but "
            "returned no bars."
        )
    return (
        f"               The feed can price {symbol} ({len(bars)} bars), so this is\n"
        "               a stale cursor rather than an outage. Try `trade tick`."
    )


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


def cmd_dashboard(args) -> int:
    """Freeze the village into one file you can send to somebody."""
    from pathlib import Path

    from . import snapshot

    eco = _ecosystem(args)
    path = snapshot.write(
        eco, Path(args.out), events=args.events, note=args.note or ""
    )
    size = path.stat().st_size
    print(f"wrote {path} ({size:,} bytes)")
    print("self-contained: open it in a browser, or attach it to an email.")
    print("It is a snapshot — the buttons and the approval gate live on "
          "`python -m src.main serve`.")
    return 0


def cmd_benchmark(args) -> int:
    """Did the village beat just buying the index?"""
    from . import benchmark

    eco = _ecosystem(args)
    market = eco.market()
    market.register([args.symbol])
    firms = sorted(eco.store.firms(), key=lambda f: f.firm_key)
    cards = {c.firm_id: c for c in eco.brokerage.evaluator.evaluate_all(firms, market)}

    comparisons = []
    for firm in firms:
        spec = eco._specs.get(firm.firm_key)
        costs = spec.costed(eco.config.data) if spec else eco.config.data
        bars = args.bars or benchmark.bars_lived(eco.store, firm.id)
        comparisons.append(benchmark.compare(
            firm, cards.get(firm.id), market, bars, args.symbol,
            slippage_bps=costs.slippage_bps, fee_bps=costs.fee_bps))

    print(_table(benchmark.table(comparisons)))
    result = benchmark.village(comparisons)
    print(f"\n{result['summary']}")
    if not result["measurable"]:
        return 0
    print(f"\n  capital      : {fmt_money(result['capital'])}")
    print(f"  the village  : {fmt_money(result['equity'])}   ({result['return_pct']}%)")
    print(f"  buy-and-hold : {fmt_money(result['hold_equity'])}   "
          f"({result['benchmark_pct']}%)")
    print(f"  EXCESS       : {result['excess_pct']}%")
    if any(not c.enough_data for c in comparisons if c.measurable):
        print(f"\nA firm with fewer than {benchmark.MIN_BARS} bars is not a verdict, "
              "whichever way it\nis pointing. Two good weeks is a fortnight of luck.")
    return 0


def cmd_postmortem(args) -> int:
    """Where the money went, per firm, from the ledger."""
    from . import postmortem

    eco = _ecosystem(args)
    market = eco.market()
    firms = ([eco.store.get_firm(args.firm)] if args.firm
             else sorted(eco.store.firms(), key=lambda f: f.firm_key))
    if firms == [None]:
        print(f"unknown firm {args.firm}")
        return 1

    cards = {c.firm_id: c for c in eco.brokerage.evaluator.evaluate_all(firms, market)}
    worst = []
    for firm in firms:
        pm = postmortem.examine(eco.store, firm, cards.get(firm.id))
        worst.append(pm)
        if args.firm or pm.fills:
            print(f"\n=== {pm.firm_key} ===")
            print(_table(postmortem.table(pm)))
            bits = []
            if pm.win_rate is not None:
                bits.append(f"win rate {pm.win_rate}%")
            if pm.breakeven_win_rate is not None:
                bits.append(f"needs {pm.breakeven_win_rate}%")
            if pm.payoff is not None:
                bits.append(f"avg loss / avg win {pm.payoff}x")
            if pm.cost_share is not None:
                bits.append(f"costs {pm.cost_share}% of winnings")
            if bits:
                print("  " + " · ".join(bits))
            for note in pm.notes:
                print(f"\n  {note}")

    if not args.firm:
        losing = sorted((p for p in worst if p.net < 0), key=lambda p: p.net)
        print("\n=== the village ===")
        total = sum((p.net for p in worst), D(0))
        costs = sum((p.costs for p in worst), D(0))
        print(f"  net across {len(worst)} firm(s): {fmt_money(total)}")
        print(f"  paid in fees and slippage:      {fmt_money(costs)}")
        if losing:
            print("  losing firms: "
                  + ", ".join(f"{p.firm_key} ({fmt_money(p.net)})" for p in losing[:5]))
    return 0


def cmd_switches(args) -> int:
    """The controls on the wall, from a terminal.

    These live in the database rather than the environment because the process
    that ticks is not the process serving Mission Control — so a switch flipped
    here reaches a running village on its next pass, with no restart. Until
    now the only way to touch them was the web page, which is no use to
    somebody already in a shell.
    """
    from .settings import KNOWN, UnknownSetting

    eco = _ecosystem(args)
    default_living = eco.config.living.enabled

    if not args.name:
        rows = []
        for name, about in KNOWN.items():
            default = False if name in ("paused", "evolution") else default_living
            on = eco.settings.get(name, default=default)
            rows.append({
                "switch": name,
                "state": "ON" if on else "off",
                "what it does": about,
            })
        print(_table(rows))
        print("\nFlip one with:  python -m src.main trade switch <name>")
        print("           or:  python -m src.main trade switch <name> --on")
        return 0

    name = args.name.strip().lower()
    default = False if name in ("paused", "evolution") else default_living
    try:
        if args.on:
            now_on = eco.settings.set(name, True, by=args.by)
        elif args.off:
            now_on = eco.settings.set(name, False, by=args.by)
        else:
            now_on = eco.settings.toggle(name, default=default, by=args.by)
    except UnknownSetting:
        print(f"there is no switch called {name!r}. Known: {', '.join(KNOWN)}")
        return 1

    print(f"{name} is now {'ON' if now_on else 'off'} — {KNOWN[name]}")
    if name == "evolution" and now_on:
        print(
            f"\nEvery {eco.config.brain.evolve_every} market bars each paper firm "
            "will mutate its\ngenome, and keep a mutant only if it beats the "
            "incumbent on bars it was\nnot fitted on. Live firms are never "
            "touched. Watch it with:\n"
            "  ./scripts/village.sh logs\n"
            "  python -m src.main trade show <firm>"
        )
    return 0


def cmd_autonomy(args) -> int:
    """What the village decides for itself, and what it still asks you."""
    from .council.council import PANELS

    eco = _ecosystem(args)
    autonomy = eco.config.autonomy
    print(f"mode        : {autonomy.mode}"
          + ("  (the council rules on pending decisions)" if autonomy.council_decides
             else "  (every gated decision waits for you)"))
    print(f"margin      : {autonomy.margin} — below this the council defers to you")
    print("\nthe council may decide:")
    print("  allocate_capital  — raise a firm's allocation, or move capital "
          "between two firms")
    print("  kill_firm         — end a firm the ledger has already concluded "
          "against")
    print("  resume_firm       — un-pause a firm whose condition has cleared")
    print(f"  ({len(PANELS)} panels of jurors, one per decision)")
    print("\nit may never decide:")
    print("  live_trading   — an order to a real broker leaves this system and "
          "cannot be taken back")
    print("\nA deferred decision is still a pending approval. Autonomy narrows what "
          "you are asked\nabout; it does not remove you.")
    if not autonomy.council_decides:
        print("\nTurn it on with:  export TRADE_AUTONOMY=council")
    return 0


def cmd_council(args) -> int:
    """The rulings, most recent first."""
    eco = _ecosystem(args)
    rows = eco.council.recent(args.limit)
    if not rows:
        print("no rulings yet.")
        if not eco.config.autonomy.council_decides:
            print("The council is not sitting — TRADE_AUTONOMY is "
                  f"'{eco.config.autonomy.mode}'.")
        return 0
    width = max((len(r["firm_key"] or "-") for r in rows), default=4)
    print(f"{'id':>4}  {'verdict':8} {'action':17} {'firm':{width}} reason")
    for row in rows:
        print(f"{row['id']:>4}  {row['verdict']:8} {row['action']:17} "
              f"{(row['firm_key'] or '-'):{width}} {(row['reason'] or '')[:60]}")
    print("\n`trade council-case <id>` for one ruling, juror by juror.")
    return 0


def cmd_council_case(args) -> int:
    import json as _json

    eco = _ecosystem(args)
    row = eco.db.query_one("SELECT * FROM council_rulings WHERE id = ?", (args.id,))
    if row is None:
        print(f"no ruling #{args.id}")
        return 1
    print(f"ruling #{row['id']} — {row['verdict'].upper()} {row['action']} "
          f"{row['firm_key'] or ''}")
    print(f"  {row['reason']}")
    print(f"  for {row['for_weight']} / against {row['against_weight']} "
          f"(confidence {row['confidence']})")
    print(f"  decided {row['created_at']}"
          + (f", approval #{row['approval_id']}" if row["approval_id"] else ""))
    print("\njurors:")
    for finding in _json.loads(row["findings"] or "[]"):
        mark = {"for": "+", "against": "-"}.get(finding["verdict"], " ")
        veto = "  [VETO]" if finding.get("veto") else ""
        print(f"  {mark} {finding['juror']:18} {finding['reason']}{veto}")
    print("\nthe ledger as the council saw it:")
    for key, value in sorted(_json.loads(row["evidence"] or "{}").items()):
        if value not in ("", [], (), {}, None):
            print(f"  {key:24} {value}")
    return 0


def cmd_signals(args) -> int:
    """What the scanners published, and who is listening.

    Answers the two questions that come up when a screener seems to do
    nothing: did it publish, and is it publishing for *this* bar. A reading
    from an older bar is shown but explicitly marked stale, because no firm
    is hearing it.
    """
    eco = _ecosystem(args)
    specs = eco.scanners.specs
    if eco.scanners.error:
        print(f"  {eco.scanners.error}")
    if not specs:
        print("no scanners configured. Add a `scanners:` block to the firm config —")
        print("see bots/example_scanner.py.")
    else:
        print(_table([
            {
                "scanner": spec.name,
                "file": str(spec.path),
                "universe": ", ".join(spec.universe)[:34] or "(the whole village)",
                "on": "yes" if spec.enabled else "no",
            }
            for spec in specs
        ]))

    listening = [
        f.firm_key for f in eco.store.firms()
        if "signals" in {
            str(a).strip().lower() for a in (eco.specs().get(f.firm_key).analysts
                                             if eco.specs().get(f.firm_key) else ())
        }
    ]
    print(f"\nlistening ({len(listening)}): " + (", ".join(listening) or "nobody yet — "
          "add `signals` to a firm's analysts"))

    rows = eco.signals.recent(limit=args.limit, publisher=args.publisher or "")
    if not rows:
        print("\nnothing published yet.")
        return 0

    current = _signals_stamp(eco)
    print("\npublished:")
    print(_table([
        {
            "bar": str(row["as_of"])[:16],
            "publisher": str(row["publisher"]),
            "symbol": str(row["symbol"]),
            "score": f'{_D(row["score"]):+.2f}',
            "conf": f'{_D(row["confidence"]):.2f}',
            "heard": "yes" if str(row["as_of"]) == current else "stale",
            "note": str(row["note"] or "")[:40],
        }
        for row in rows
    ]))
    return 0


def _D(value):
    from ..money import D

    try:
        return D(value)
    except Exception:  # noqa: BLE001 - a column that will not parse still prints
        return D(0)


def _signals_stamp(eco) -> str:
    """The bar the village is standing on, as the board stores it."""
    from .signals import stamp

    try:
        return stamp(eco.market().as_of())
    except Exception:  # noqa: BLE001 - a feed that will not answer is not fatal here
        return ""


def cmd_recruit(args) -> int:
    """Drop a bot file in; the court rules, and a cleared file becomes a firm."""
    from ..money import D

    eco = _ecosystem(args)
    result = eco.recruit(
        args.file,
        submitted_by=args.by or "",
        stake=D(args.stake) if args.stake else None,
        name=args.name or "",
    )
    case = result.case
    print(f"{case.evidence.name}: court says {case.ruling.verdict.upper()} "
          f"(confidence {case.ruling.confidence})")
    print(f"  {case.ruling.reason}")
    if not result.accepted:
        print(f"\nnot recruited — {result.reason}")
        return 1
    print(f"\n{result}")
    print("The firm exists, is paused, and holds nothing. It starts trading when "
          "the funding is approved:")
    print(f"  python -m src.main approve {result.approval_id} --by you")
    print("  python -m src.main trade apply-approvals")
    return 0


def cmd_recruit_watch(args) -> int:
    """Try every bot in a directory."""
    from pathlib import Path as _Path

    eco = _ecosystem(args)
    results = eco.recruit_all(
        args.dir,
        submitted_by=args.by or "",
        move_to=_Path(args.move_to) if args.move_to else None,
    )
    if not results:
        print(f"nothing to recruit in {args.dir}")
        return 0
    taken = [r for r in results if r.accepted]
    for result in results:
        print(("  + " if result.accepted else "  - ") + str(result))
    print(f"\n{len(taken)} of {len(results)} recruited, all paused and unfunded.")
    if taken:
        print("Fund them:  python -m src.main approvals")
    return 0


def cmd_recruits(args) -> int:
    """Recruited firms still waiting to be funded."""
    from .recruit import pending_recruits

    eco = _ecosystem(args)
    rows = pending_recruits(eco)
    if not rows:
        print("no recruits waiting. Drop a bot in with `trade recruit <file>`.")
        return 0
    print(f"{'firm':20} {'from':28} {'asking':>12}  approval")
    for row in rows:
        print(f"{row['firm']:20} {(row['file'] or '')[:28]:28} "
              f"{row['requested']:>12}  #{row['approval']}")
    return 0


def cmd_import(args) -> int:
    """Read a bot that was never written for this village, and adapt it."""
    from pathlib import Path

    from .importer import scan, scan_all, write

    target = Path(args.path)
    reports = scan_all(target) if target.is_dir() else [scan(target)]
    if not reports:
        print(f"nothing to import in {target}")
        return 0

    written = 0
    for report in reports:
        print(report.summary())
        if args.dry_run:
            print()
            continue
        path = write(report, args.out)
        if path is not None:
            written += 1
            print(f"  written   : {path}")
        elif not report.error and not report.secrets:
            print("  not written: no symbols found — add a UNIVERSE by hand")
        print()

    if args.dry_run:
        print("dry run: nothing written.")
        return 0
    print(f"{written} of {len(reports)} written to {args.out}/")
    if written:
        print("Read them, then:")
        print(f"  python -m src.main trade recruit-watch --dir {args.out}")
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


def cmd_live_status(args) -> int:
    """How close each firm is to being allowed near real money.

    Grants nothing and changes nothing. It is a report, and the point of it is
    to be watched over weeks — every criterion, its actual value, and the
    threshold, so a firm can be seen getting closer and an unmet criterion can
    be argued with.
    """
    from . import promotion

    eco = _ecosystem(args)
    market = eco.market()
    feed_name = getattr(eco.feed, "name", "")
    firms = eco.store.firms()
    cards = {c.firm_id: c for c in eco.brokerage.evaluator.evaluate_all(firms, market)}
    reconciled = eco.brokerage.reconcile(market).ok
    cfg = promotion.LiveReadiness()

    if args.firm:
        firms = [f for f in firms if f.firm_key == args.firm]
        if not firms:
            print(f"unknown firm {args.firm}")
            return 1

    ready_now = []
    for firm in firms:
        verdict = promotion.assess(
            eco.store, firm, cards.get(firm.id), feed_name, cfg, reconciled
        )
        if verdict.ready:
            ready_now.append(verdict)
        if args.firm or not args.summary:
            print(f"\n{firm.firm_key} — {verdict.summary()}")
            print(_table(promotion.table(verdict)))

    print(f"\n{len(ready_now)} of {len(firms)} firm(s) meet every criterion.")
    if ready_now:
        print(
            "Nothing has been granted. Promotion is per firm, needs your\n"
            "approval, and starts at "
            f"{fmt_money(cfg.max_start_capital)} — the first live run is for\n"
            "finding what paper cannot show you, not for profit."
        )
    else:
        print(
            "Which is the expected answer for a young village. The criteria are\n"
            "env-overridable (TRADE_LIVE_MIN_TRADES, TRADE_LIVE_MIN_BARS,\n"
            "TRADE_LIVE_MIN_T, ...) and are meant to be argued with, not obeyed."
        )
    return 0


def cmd_go_live(args) -> int:
    """Ask a human to put ONE firm on real money. Grants nothing itself."""
    from . import promotion

    eco = _ecosystem(args)
    firm = eco.store.get_firm(args.firm)
    if firm is None:
        print(f"unknown firm {args.firm}")
        return 1
    market = eco.market()
    card = eco.brokerage.evaluator.evaluate(firm, market)
    verdict = promotion.assess(
        eco.store, firm, card, getattr(eco.feed, "name", ""),
        promotion.LiveReadiness(), eco.brokerage.reconcile(market).ok,
    )
    print(_table(promotion.table(verdict)))

    try:
        approval = eco.brokerage.request_promotion(
            args.firm, verdict, args.venue, args.by
        )
    except ValueError as exc:
        print(f"\nREFUSED: {exc}")
        return 1

    print(
        f"\nRequested. Nothing is live yet — approval #{approval.id} is waiting "
        f"for you:\n"
        f"  python -m src.main approve {approval.id} --by {args.by}\n"
        f"  python -m src.main trade apply-approvals\n\n"
        f"It would start at {fmt_money(verdict.start_capital)} on {args.venue}. "
        "Any guard tripping\nputs it straight back on paper without asking."
    )
    return 0


def cmd_all_to_paper(args) -> int:
    """Everything off real money, now. Needs nobody's permission."""
    eco = _ecosystem(args)
    done = eco.brokerage.all_to_paper(args.reason or "operator pulled everything back")
    changed = [d for d in done if d.get("changed")]
    if not changed:
        print("nothing was on real money.")
        return 0
    for entry in changed:
        print(f"  {entry['firm']} -> paper")
    print(f"\n{len(changed)} firm(s) back on paper. Open positions are not "
          "liquidated —\nselling is the firm's job and goes through the venue "
          "like any other trade.")
    return 0


def cmd_revive(args) -> int:
    """Reverse kills that were decided on a feed that could not price the book.

    `--all` is the case this exists for: a feed outage does not kill one firm,
    it kills every firm holding anything, and asking a human to type eleven
    names to undo one outage is a punishment for the system's mistake.
    """
    eco = _ecosystem(args)
    market = eco.market()
    killed = [f for f in eco.store.firms() if f.is_killed]
    if args.firm:
        killed = [f for f in killed if f.firm_key == args.firm]
        if not killed:
            print(f"{args.firm} is not a killed firm")
            return 1
    elif not args.all:
        print("name a firm, or pass --all to review every killed firm")
        return 2
    if not killed:
        print("no killed firms")
        return 0

    revived, refused = [], []
    for firm in killed:
        try:
            result = eco.brokerage.revive_firm(firm.firm_key, args.by, market)
            revived.append(result)
            print(f"  REVIVED {firm.firm_key} — was: {result['was'][:60]}")
        except ValueError as exc:
            refused.append((firm.firm_key, str(exc)))
            print(f"  kept dead {firm.firm_key}: {exc}")

    print(f"\n{len(revived)} revived, {len(refused)} left killed.")
    if revived:
        print(
            "Revived firms are active with whatever allocation they had left — a\n"
            "kill releases capital, and giving it back is an increase in risk, so it\n"
            "still goes through the gate:\n"
            "  python -m src.main trade allocations"
        )
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

    # -- scanners ----------------------------------------------------------
    p = add("signals", "what the scanners published, and who hears it", cmd_signals)
    p.add_argument("--publisher", help="only this scanner")
    p.add_argument("--limit", type=int, default=25)

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

    p = add("dashboard", "freeze the village into one self-contained HTML file",
            cmd_dashboard)
    p.add_argument("--out", default="dashboard/village.html")
    p.add_argument("--events", type=int, default=120,
                   help="how many recorded events to embed")
    p.add_argument("--note", help="a line for the banner, e.g. what this is")

    add("autonomy", "what the village decides for itself", cmd_autonomy)

    p = add("council", "the council's rulings", cmd_council)
    p.add_argument("--limit", type=int, default=20)

    p = add("council-case", "one ruling, juror by juror", cmd_council_case)
    p.add_argument("id", type=int)

    p = add("import", "adapt a bot written for something else", cmd_import)
    p.add_argument("path", help="a file or a directory of them")
    p.add_argument("--out", default="bots", help="where to write village-format files")
    p.add_argument("--dry-run", action="store_true", help="report only, write nothing")

    p = add("recruit", "drop a bot file in; a cleared file becomes a firm", cmd_recruit)
    p.add_argument("file")
    p.add_argument("--stake", help="capital to ask for (default: the configured per-firm)")
    p.add_argument("--name", help="override the firm name")
    p.add_argument("--by", help="who is submitting it")

    p = add("recruit-watch", "try every bot in a directory", cmd_recruit_watch)
    p.add_argument("--dir", default="bots")
    p.add_argument("--move-to", help="move recruited files here")
    p.add_argument("--by")

    add("recruits", "recruited firms waiting to be funded", cmd_recruits)

    add("frameworks", "which external frameworks are installed", cmd_frameworks)

    p = add("live-request", "ask for permission to trade a live venue", cmd_live_request)
    p.add_argument("--venue", required=True)
    p.add_argument("--reason")

    add("apply-approvals", "carry out what a human approved", cmd_apply_approvals)

    p = add("resume", "un-pause a firm (human decision)", cmd_resume)
    p.add_argument("firm")
    p.add_argument("--by", required=True)

    p = add("go-live", "ask to put ONE firm on real money", cmd_go_live)
    p.add_argument("firm")
    p.add_argument("--venue", default="alpaca")
    p.add_argument("--by", required=True)

    p = add("benchmark", "did it beat just buying the index?", cmd_benchmark)
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--bars", type=int, help="compare over exactly this many bars")

    p = add("post-mortem", "where the money went, and which cause it was",
            cmd_postmortem)
    p.add_argument("firm", nargs="?")

    p = add("switches", "the controls on the wall, and how to flip them",
            cmd_switches)
    p.add_argument("name", nargs="?")
    p.add_argument("--on", action="store_true", help="turn it on, whatever it was")
    p.add_argument("--off", action="store_true", help="turn it off, whatever it was")
    p.add_argument("--by", default="cli")

    p = add("switch", "flip one control on the wall", cmd_switches)
    p.add_argument("name")
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.add_argument("--by", default="cli")

    p = add("all-to-paper", "pull every firm off real money, now", cmd_all_to_paper)
    p.add_argument("--reason")

    p = add("live-status", "how close each firm is to real money (grants nothing)",
            cmd_live_status)
    p.add_argument("firm", nargs="?")
    p.add_argument("--summary", action="store_true", help="just the count")

    p = add("revive", "undo a kill decided on a feed that had no prices", cmd_revive)
    p.add_argument("firm", nargs="?")
    p.add_argument("--all", action="store_true", help="review every killed firm")
    p.add_argument("--by", required=True)


__all__ = ["add_trade_parser"]
