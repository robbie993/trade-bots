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
