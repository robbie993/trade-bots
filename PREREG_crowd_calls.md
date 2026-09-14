# PREREG — do explicit crowd calls predict memecoin returns?

**Written 2026-09-14, before any arm has been run.** Everything below is fixed
in advance. If a criterion is changed after seeing a number, that fact goes in
the results section in the same words as this one.

Nothing in `src/trading/crowd.py` is wired to a firm. It is apparatus, and it
stays apparatus until this document has been run and answered.

## The question

Does an explicit directional call from the crowd — somebody saying they are
*buying* or *selling* a named coin — predict that coin's return over the next
bars, on `firm_i_memecoins`' universe of DOGE, SHIB, PEPE and WIF?

Explicit calls, not mentions. A name trending because it collapsed is
mentioned constantly, and a mention counter reads that as bullish. The
literature's effect is on recommendations, not attention, and the two are
opposite on exactly the days that matter.

## Why this is being asked now, and not before

It was blocked on a cost error, and the block is recorded in
`config/firm_config.yaml` in the memecoin desk's own comment:

> at 60 a side a real signal would have had to clear 120 bps round trip to
> show up at all. The true bar is 39.

Measured 2026-09-11 from 1,000 quotes each: DOGE 19.64 bps a side, WIF 19.65,
against an assumed 35. Any test run before that correction was asking whether
the signal could clear a bar three times higher than the real one, and would
have returned a null whether or not an effect existed. **A null measured
against the wrong cost is not evidence of absence.**

`firm_i_memecoins` is also the only desk in the village with zero
session-contaminated fills — crypto trades around the clock, so the 499 fills
booked while the US equity market was shut never touched it. Its trade history
is the one that did not need repairing.

## Arms

All arms run over the same window, the same bars, the same costs (25 bps fee +
19.7 bps slippage a side, from the measured values), the same risk limits and
the same universe. Only the mapping from calls to symbols differs.

| arm | what it trades on |
|---|---|
| **1 — Calls** | explicit calls from `extract_calls`, aggregated per bar |
| **2 — Shuffled calls** | the same calls, same count, same timestamps, each reassigned to a *different* symbol from the same universe (`shuffle_symbols`) |
| **3 — Mentions** | every ticker occurrence counted, direction ignored |
| **4 — Buy and hold** | equal-weight the four coins, no trading |

### Why arm 2 is the one that decides

A crowd-call strategy has a timing component and a selection component, and
only selection is an edge. The crowd is loudest when everything is moving, so
an arm that trades when the crowd is loud inherits volatility clustering it did
nothing to earn. Arm 2 keeps all of the timing and destroys all of the
selection.

**Arm 1 must beat arm 2. Beating arm 4 alone proves nothing**, for the same
reason `PREREG_universe_discovery.md` gives about its own control: a control
drawn from a different pool measures the pool rather than the effect.

### Why arm 3 exists

If arm 1 and arm 3 are indistinguishable, then the call extraction — the entire
contribution of `crowd.py` — did nothing, and what is being measured is
attention. That is a publishable answer and it is not the same answer as
"crowd signals work".

## Fixed before running

* **Universe** — DOGE-USD, SHIB-USD, PEPE-USD, WIF-USD. The desk's own four.
  No additions after seeing results.
* **Source** — `RedditSource` in `src/trading/news.py`, the keyless public
  JSON endpoint, on subreddits fixed at run time and listed in the results.
* **Horizon** — forward return over 1, 4 and 24 bars. All three reported
  whether or not they agree. Reporting only the one that worked is the failure
  this whole file exists to prevent.
* **Costs** — 25 bps fee, 19.7 bps slippage, per side. Round trip 89.4 bps.
* **Shuffle seeds** — arm 2 is run 500 times with seeds 0..499. The reported
  statistic is arm 1's return against the *distribution* of arm 2, not against
  one draw.
* **Minimum sample** — 30 bars carrying at least one call. Below that the
  answer is "insufficient data", not a number.

## Criteria — all five, fixed now

1. Arm 1 beats arm 2's median by more than zero, net of costs.
2. Arm 1 sits above the **95th percentile** of the 500 shuffled arms.
3. Arm 1 beats arm 3 (mentions), or the extraction added nothing.
4. The effect has the same sign at 1, 4 and 24 bars.
5. At least 30 bars with a call, and at least 100 calls in total.

**Fewer than five of five is a null.** Not "promising", not "directionally
encouraging" — a null. The fleet's record is eight nulls and one hindsight
artefact, and each of those looked promising at four of five.

## What a pass would and would not license

A pass licenses **one scanner, publishing readings, wired to no firm by
default**. It does not license a memecoin strategy, a capital allocation, or a
live order. A scanner informs a decision and never makes one
(`src/trading/signals.py`), and the sibling repo's insider/options verdict
records "No scanner shipped on purpose" as a deliberate choice rather than an
oversight.

## Status — attempted 2026-09-14, could not be run

**No criterion below has been changed, because no number was ever seen.** This
records an attempt that got as far as the data and stopped there.

The source this document fixes in advance — `RedditSource`, "the keyless public
JSON endpoint" — **returns HTTP 403 to every request**, on every subreddit
tried:

```
reddit:wallstreetbets     0 stories   HTTP 403
reddit:stocks             0 stories   HTTP 403
reddit:CryptoCurrency     0 stories   HTTP 403
reddit:dogecoin           0 stories   HTTP 403
yahoo-finance            50 stories   ok
```

Not a TLS fault: `_get` uses the shared certifi context, `verify_mode=2`, and
the Yahoo feed over the same path returns 50 stories. Reddit no longer serves
anonymous JSON to this client.

Nor can the test be run retrospectively. **There is no stored corpus.**
`news.py` persists derived per-symbol scores, never raw posts, and the
`signals` table holds no Reddit rows at all — the four memecoin symbols carry
only `example`-publisher data, which is synthetic. So criterion 5 (≥30 bars
with a call, ≥100 calls) is not merely unmet, it is unreachable: the number of
obtainable calls is zero.

**This is a blocked experiment, not a null.** A null requires a measurement.
Recorded here in the same words as the rest of the file, because "we ran it and
found nothing" and "we could not run it" are different claims and the second
one decays into the first if nobody writes it down.

Unblocking it needs a decision, not work: an authenticated Reddit client, a
different venue, or a forward collection period against a source that answers.
Note also that this design implies forward collection — "a post is assigned to
the bar it was fetched on" only makes sense while fetching — and the document
never says for how long. That should be fixed here before any arm runs.

### Collateral, found on the way

The same 403 means **two of the village's three live news sources are dead**.
`build_sources()` defaults to `reddit:wallstreetbets`, `reddit:stocks` and
Yahoo RSS; the news desk has been running on Yahoo alone. It is *reported* —
every tick writes `news source quiet — reddit:wallstreetbets: HTTP 403` — so
this is visible-but-unread rather than silent. The word "quiet" was doing too
much work for an HTTP error and has been changed.

## Known limits, stated before the result

* **Reddit is not the crowd.** It is one venue, English-language, with its own
  demographics and its own bots. A null here is a null about Reddit.
* **Look-ahead through deletion.** Posts that were removed by moderators are
  invisible now and were visible then. This biases the corpus toward posts that
  survived, and nothing in this design corrects for it. Recorded because it
  cannot be fixed, not because it is small.
* **No timestamp fidelity below the bar.** A post is assigned to the bar it was
  fetched on, not the minute it was written. On a 15-minute bar that is a real
  smearing of up to one bar and it can only hurt the signal, never flatter it.
* **The extractor is a word matcher, not a language model.** Sarcasm, quoting
  somebody else's call, and "I should have bought DOGE" all read as calls.
  `NEGATORS` catches the commonest inversion and nothing catches the rest.
* **Four symbols is a narrow cross-section.** With four names, one coin's
  regime can carry the whole result, so arm 1 vs arm 2 is reported per symbol
  as well as pooled.
