# PRE-REGISTRATION: Atlas R3 (frozen 2026-10-02, before any R3 data was seen)

R1 and R2 used up the independent historical test budget: about 11,700
genomes, no out-of-sample edge over SPY, and GA champions no better than
hand-written defaults. So R3 is three genuinely different hypotheses, each
with **one fixed rule set chosen by hand**. They are tested **forward only**.

* No GA and no parameter search, on any data, ever, for R3.
* No historical backtest of these rules is run or looked at. The only runs
  before the forward period use synthetic prices, to test the mechanics.
* The rules are frozen by `atlas_research/r3/rules.py`. The forward ledger
  records that file's SHA-256 and refuses to keep going if it changes. A
  bug fix is allowed only if it makes the code do what this document already
  says, and it goes in the change log at the bottom. A rule change of any
  kind starts a new hypothesis (R4) with its own forward clock.
* **Forward start: the first trading day after this file is merged into the
  working branch.** Every trading day from then on counts. Days missed by
  the runner are filled in later from the same daily bars, so a sleeping PC
  cannot change the record.

Everything is shadow paper: a ledger of what each book would hold and earn,
with the costs below. No orders are sent anywhere.

## Shared mechanics

* $100,000 starting equity per book, except R3-C, which starts at
  $1,000,000 so that whole option contracts can track its delta target.
* Decide at close t, fill at close t+1 (the same one-day lag as R1 and R2).
* Stocks and ETFs pay a half-spread plus 2 bp slippage each way. The
  half-spread is 1 bp for the benchmark ETFs and the 100 universe stocks with
  the highest 60-day dollar volume, and 3 bp for every other stock. Cash
  earns the 3-month T-bill rate.
* Options fill at the ask when buying and the bid when selling. If no quote
  was captured that day, the fill is the bar close +/- 3% and is labelled
  MODELED. Fees are $0.03 per contract.
* **Universe**: S&P 500 constituents, snapshotted on the first trading day of
  each quarter (the snapshot is saved, never re-fetched for a past quarter).
  Liquidity filter: price >= $10 and 60-day average dollar volume >= $50M.

## R3-A: concentrated relative strength + SPY core

*Alpha source: stock selection. Hypothesis: the strongest liquid large caps
keep outperforming over the next month (Jegadeesh & Titman 1993), and
concentrating in them beats SPY in strong markets.*

* 50% SPY, held permanently.
* 50% sleeve. On the first trading day of each month, rank the universe by
  126-day return, skipping the last 5 days. A stock is eligible only if it
  is (a) at or above the 80th percentile of that ranking, (b) ahead of SPY
  over the same window, and (c) above its 200-day moving average.
* Hold the top 20 eligible names, sized by inverse 63-day volatility, with
  no name above 10% of the sleeve. Sleeve money with no eligible name to buy
  goes to SPY.
* Every Friday, any holding that closes below its 200-day average is sold at
  the next close, and the money goes to SPY until the next monthly rebalance.

## R3-B: earnings-reaction drift (price acceleration after news)

*Alpha source: information that prices absorb slowly. Hypothesis: stocks
with a large positive market reaction to earnings keep drifting up for
about a quarter (Bernard & Thomas 1989; Chan, Jegadeesh & Lakonishok 1996;
Brandt et al. 2008).*

* **Event**: an 8-K with Item 2.02 (Results of Operations) on SEC EDGAR, for
  a universe stock. Day 0 is the filing date, or the next trading day if
  EDGAR accepted it after 16:00 ET.
* **Reaction**: the stock's return minus SPY's, from the close of day -1 to
  the close of day +1.
* **Entry**, decided at the close of day +1 and filled at day +2:
  * the reaction is at least +4%, and
  * it is in the top 10% of all universe reactions over the trailing 63
    trading days, and
  * the stock's 63-day return is above its return over the 63 days before
    that (acceleration), and
  * the stock is above its 200-day average.
* **Book**: up to 20 positions at 5% each. When the book is full, a new
  signal is skipped, not queued. Each position is held 60 trading days, then sold. Money not
  in positions is held in SPY, so the baseline is the market, not cash.

## R3-C: trend + convexity (SPY calls)

*Alpha source: asymmetric payoff. Hypothesis: deep-ish in-the-money SPY
calls give over 100% upside participation while the trend is up, and the
premium caps the loss when it breaks.*

* The book holds SPY call options plus T-bills, nothing else.
* **Delta target**: 1.2 x equity while SPY closes above its 200-day average,
  and 0.5 x equity below it, checked every close.
* **Contract**: the listed SPY call with 60-120 days to expiry and delta
  closest to 0.70, preferring the monthly expiry nearest 90 days out.
* **Rebalance**: roll a position at 30 days to expiry, or when its delta
  leaves 0.50-0.90. Resize when book delta is more than 15% from target.
* **Missed days**: option chains with greeks exist only on the day they are
  captured. On a day the runner fills in later, R3-C keeps what it holds,
  marks it from option bars (labelled MODELED) and places no new trades.
* **Premium cap**: total premium paid for open calls may never exceed 25% of
  equity. If the delta target would need more, the book holds less delta.

## Benchmarks (run in the same ledger, same costs)

SPY buy-and-hold; QQQ buy-and-hold; simple 12-month momentum (the 3 of the
R1 ETFs with the best 252-day return, skipping 5 days, rebalanced monthly,
each slot in cash if its own return is negative).

## Success, and when it can be claimed

Robbie's table, all against SPY over the same forward days:

| metric | required |
|---|---|
| CAGR | > SPY |
| max drawdown | shallower than SPY's |
| excess CAGR | positive |
| Sharpe (over T-bills) | >= SPY's |
| turnover | reported; costs at 2x the assumption must not flip the verdict |
| up-market capture (monthly) | > 100% |
| down-market capture (monthly) | materially below 100%: <= 80% |
| rolling 12-month excess | positive in at least 60% of windows |

* **Month 6**: bug and implementation review only. No verdict.
* **Month 12**: interim read. A book stops early only for an implementation
  bug, or a drawdown more than 15 points deeper than SPY's over the same days.
* **Month 24**: verdict. A book that meets every row is a "forward-paper
  success" and can be considered for a small real-money trial. Anything
  else is reported as it stands.

Across the three books, the verdict also reports White's Reality Check on
the forward days (3 models vs SPY). Three hypotheses means a lucky winner
is likely, and the verdict will say so.

## Change log

(empty: no change since freezing)
