# PREREG — do explicit crowd calls predict *equity* returns?

**Written 2026-09-14 night, before any arm has been run.** Everything below is
fixed in advance. If a criterion is changed after seeing a number, that fact
goes in the results section in the same words as this one.

This is a **new experiment**, not an amendment to `PREREG_crowd_calls.md`. That
document fixed its universe as four memecoins and forbade additions after
seeing results; changing the universe of a completed null is precisely what it
exists to prevent. Its answer stands as written: a null about StockTwits
memecoins.

## Why this exists

`PREREG_crowd_calls.md` ran on DOGE, SHIB, PEPE and WIF and returned 3 of 5 —
a null — on an analysable sample of **n=34**. That thin sample was not bad luck.
StockTwits is primarily an **equities** venue, and the memecoin corner of it is
close to empty. Measured 2026-09-14:

```
                msgs/hour
DOGE.X                4.4        NVDA        26.9
SHIB.X                1.0        SPY         52.5
PEPE.X                0.3        TSLA        10.9
WIF.X                 0.1        AMD          9.1
--------------------------       COIN         6.3
all four memecoins    5.8        ten equities 131
```

**Roughly 23x the message volume on ten equity names than on the entire
memecoin universe.** The earlier experiment tested the thinnest corner of the
venue and then reported the venue's answer. The interesting test was never run.

## The question

Unchanged in substance: does an explicit directional call from the crowd —
somebody saying they are *buying* or *selling* a named stock — predict that
stock's return over the next bars?

Explicit calls, not mentions. A name trending because it collapsed is mentioned
constantly, and a mention counter reads that as bullish.

## Fixed before running

* **Universe** — the **29 equities the village already trades**, taken from
  `fills` and fixed by `config/firm_config.yaml` long before this document:

  `AAPL AMD AMZN DBA DIA EEM EFA EWJ FXI GLD IEF IWM JNJ KO LQD META MSFT NVDA
  PG QQQ SHY SLV SPY TLT TSLA USO VGK VZ XOM`

  Chosen because nobody selected it for this test. **Declared bias:** I have
  already seen current message volume for 7 of these 29 (AAPL, AMD, AMZN, MSFT,
  NVDA, SPY, TSLA) while measuring the table above. I have seen no *returns*
  for any of them and no call-to-return relationship for any. The universe is
  not being trimmed, ranked or reordered on that knowledge, and all 29 run.
* **Source** — StockTwits `/streams/symbol/{SYM}.json`, keyless, cursor-paged,
  one stream per symbol.
* **Extraction** — `crowd.extract_calls` over message **text**. StockTwits'
  own `entities.sentiment` bull/bear tag is **not** used, for the same reason
  as the memecoin version: arm 3 only remains a control if the extractor under
  test is the one running.
* **Horizon** — forward return over 1, 4 and 24 bars, all three reported
  whether or not they agree.
* **Costs** — equities, not crypto. `0.82 bps/side` regular-hours slippage from
  the village's own measured values in `config/firm_config.yaml`, plus **0 bps
  commission** (Alpaca equities are commission-free). Round trip **1.64 bps**.
  This is the honest number for this asset class and it is far below the
  memecoin bar of 89.4 bps — which is itself a reason the equity test is worth
  running.
* **Shuffle seeds** — arm 2 is run 500 times with seeds 0..499.
* **Minimum sample** — **≥30 bars carrying a call and ≥100 calls that have a
  forward return at every horizon.** Note the change from the memecoin
  document: it counted *extracted* calls, and 188 of its 222 fell outside the
  price window, so it certified a sample it did not have. This counts priced
  calls, which is what the arms actually consume.

## Arms

| arm | what it trades on |
|---|---|
| **1 — Calls** | explicit calls from `extract_calls`, aggregated per bar |
| **2 — Shuffled calls** | same calls, same count, same timestamps, each reassigned to a *different* symbol from the same universe |
| **3 — Mentions** | every ticker occurrence counted, direction ignored |
| **4 — Buy and hold** | equal-weight the universe, no trading |

Arm 2 decides. A crowd-call strategy has a timing component and a selection
component, and only selection is an edge.

## Criteria — all five, fixed now

1. Arm 1 beats arm 2's median by more than zero, net of costs.
2. Arm 1 sits above the **95th percentile** of the 500 shuffled arms.
3. Arm 1 beats arm 3 (mentions), or the extraction added nothing.
4. The effect has the same sign at 1, 4 and 24 bars **and that sign is
   positive.** *(The memecoin version omitted the second half, and passed this
   criterion with all three horizons negative — a consistently losing arm
   satisfying a consistency test. Fixed here, before any number is seen.)*
5. At least 30 bars with a call and at least 100 **priced** calls.

**Fewer than five of five is a null.** Not "promising", not "directionally
encouraging". The fleet's record is nine nulls and one hindsight artefact.

## What a pass would and would not license

One scanner, publishing readings, wired to no firm by default. It does not
license a strategy, a capital allocation, or a live order.

## Known limits, stated before the result

* **StockTwits is not the crowd.** One venue, retail-skewed, with its own bots.
  A null here is a null about StockTwits equities.
* **Look-ahead through deletion.** Removed posts are invisible now and were
  visible then. Not correctable in this design.
* **No timestamp fidelity below the bar**; a post is assigned to the bar it was
  fetched in.
* **The extractor is a word matcher.** Sarcasm, quoting, and "I should have
  bought NVDA" all read as calls.
* **Equity bars are session-bound.** Overnight and weekend messages land on
  bars with no forward return inside the session and will be dropped by the
  priced-call requirement — which is why criterion 5 counts priced calls.
* **Price history caps the sample.** The feed serves a bounded number of
  15-minute bars per symbol; calls older than that window have no forward
  return. This is the defect that reduced the memecoin run to n=34, named here
  in advance so the same surprise cannot be reported as a finding.

---

# RESULTS — 2026-09-14 night

Run after this document was committed, with no criterion altered. Reproduce
with `CROWD_PAGES=12 python scripts/run_crowd_prereg_equities.py`.

## The answer: 1 of 5. A null, and a cleaner one than the memecoins gave.

```
8,634 unique messages across 29 streams
1,113 explicit calls extracted; 222 priced at all horizons; 599 bars with a call

ARMS — mean net return per call, after the 1.64 bps round trip

 horizon    n    arm1 calls   arm2 median   arm2 p95   arm3 mentions
  1 bar   381      -0.0073%      -0.0192%    0.0008%       -0.0103%
  4 bar   369      -0.0274%      -0.0263%    0.0089%       -0.0153%
 24 bar   222      -0.0418%      -0.0136%    0.0860%       +0.0178%

arm 4 (equal-weight buy and hold over the window): +0.21%
```

```
[FAIL]  1. arm1 beats arm2's median, net of costs
[FAIL]  2. arm1 above the 95th percentile of 500 shuffles
[FAIL]  3. arm1 beats arm3 (mentions)
[FAIL]  4. same sign at 1, 4, 24 bars AND that sign is positive
[PASS]  5. >=30 bars and >=100 priced calls
```

## Why this null is worth more than the memecoin one

**The sample is 6.5x larger and the cost bar is 54x lower.** 222 priced calls
against 34; 1.64 bps round trip against 89.4. The two escape hatches available
to the earlier result — too few observations, costs swamping a real effect —
are both closed. At these costs an edge of a tenth of a basis point would show,
and the arms are negative.

**Arm 3 settles the question arm 3 exists to ask.** Counting mentions beats
extracting explicit calls at 4 and 24 bars, and at 24 bars mentions is
**positive (+0.0178%) while calls is −0.0418%**. The extraction is not merely
failing to add value, it is subtracting it — on this venue, over this window,
`crowd.py`'s entire contribution is worse than ignoring direction and counting
how often a name comes up.

**Criterion 4 failed because it was repaired first.** All three horizons share
a sign, and that sign is negative. Under the memecoin document's wording —
"the effect has the same sign at 1, 4 and 24 bars" — this would have **passed**,
exactly as it wrongly passed there. The repair was written into this prereg
before any number was seen, and it turned a false PASS into a true FAIL. That
is the clearest evidence in this project that the criterion was broken rather
than merely inelegant.

**Buy and hold beat every trading arm.** +0.21% against three negative arms.

## What this licenses

`crowd.py` stays apparatus, wired to nothing. Two independent universes, two
nulls, and on the larger one the extraction is worse than the control it was
built to beat. **Do not wire a crowd scanner**, and do not ask this question a
third time on a third venue without a reason better than "the last two venues
were wrong".

Counted in `data/pvalue_ledger.json` as `crowd:stocktwits_equities`.

## Limits, as stated in advance

A null about StockTwits equities over one week. The deletion bias, the
sub-bar timestamp smearing and the word-matcher weaknesses all still apply.
891 of the 1,113 extracted calls had no forward return at all three horizons —
overnight and weekend messages, and calls older than the price window — which
is the session-bound limitation this document named before running.
