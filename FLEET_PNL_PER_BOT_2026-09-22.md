# Which bot actually makes the money — 2026-09-22

Follow-on to `FLEET_PNL_2026-09-22.md`. That one established the shared account
is up $10.4k on paper with roughly nothing realised. This asks: **which bot?**

Every bot on Railway, not a sample. An earlier draft of this file covered four
of them and asserted the rest from memory; those assertions were wrong in three
places, noted below.

## The attribution problem, first

You cannot answer this from the broker. Every fleet bot submits to one account
(`PA3VG5AP6TO5`) and `client_order_id` is a bare UUID — checked across all 1,895
journal records, every id is anonymous. **The fleet is architecturally incapable
of per-bot attribution at the broker.** So everything below is self-reported by
each bot, on its own volume, in its own units.

Note the mount paths differ: `sincere-appreciation`, `supportive-benevolence`,
`honest-hope` and `trade-bot-2` use `/data`; `keystone`, `fiveway` and
`supercrypto` use `/app/data`. Looking in the wrong one returns "No such file or
directory", which reads exactly like an idle bot.

## What each one reports

| bot | service | reports | result | n |
|---|---|---|---|---|
| **wheel** | sincere-appreciation | realised $ (aggregate) | **+$3,436.00** | 20 closes |
| **keystone #2** | pleasant-energy | per-trade return | **+11.42%** compounded | 26 |
| **keystone #1** | keystone | per-trade return | **+4.47%** compounded | 24 |
| **supercrypto** | supercrypto | daily equity, 3 cost tiers | **+1.37% / +1.28% / +1.12%** | 7 days |
| coinbase | honest-hope | realised $ | −$4.43 | 45 |
| btcc | supportive-benevolence | realised $ | −$73.20 | 345 |
| picks_trader | sincere-appreciation | realised $ | −$356.64 | 60 |
| fiveway | fiveway | 16 option orders w/ `order_id` | not yet computed | 16 |
| fomo | trade-bot-2 | unpaired swap log | none possible | 556 |
| sentinel | exciting-wisdom | **nothing — no volume** | — | — |
| gatekeeper | sublime-celebration | **nothing — no volume** | — | — |
| scanner, form4, dividend | sincere-appreciation | scan-day markers | publishers, no orders | — |

## Three things the earlier draft got wrong

1. **fiveway is not signals-only.** `fiveway_orders.jsonl` holds 16 real option
   orders with broker `order_id`s (e.g. `INTC261009P00090000`, limit 0.38).
   It trades. It is also the one bot whose orders *can* be joined to the
   journal, because it records the id.
2. **keystone is not one bot.** Two services run `BOT=keystone`, on separate
   volumes, and their books have diverged: 24 closed trades from 2026-08-08
   versus 26 from 2026-07-28, +4.47% versus +11.42%. Same bot, same Discord
   channel, same paper account, two different track records. Whichever file you
   happen to read decides the verdict.
3. **sentinel and gatekeeper have no volume at all.** Not an empty one — none.
   Whatever they have decided since the last redeploy exists only in memory and
   in Discord. Neither has a track record that survives a restart.

Also corrected: supercrypto's ledger is denominated at base 1000, not 1.0.
Reading it as 1.0 gives "+101,247%", which is what I computed first and did not
report. The real figure is ~+1.3% over seven days, and its own pre-registration
says the criterion is *tracking*, not P&L, until 2027-03-16. Seven days of
crypto is noise either way.

## The gap, which is still the actual finding

The four dollar-denominated bots sum to **+$3,001.73**. The broker says the
account's realised is about **−$114**. That is a ~$3,100 disagreement, and it
sits almost entirely on the wheel.

The wheel's +$3,436 is the fleet's entire profit story. It is also the largest
number, the only one with **no per-trade detail** — `wheel_learn.json` is 75
bytes, `{"cycles": 0, "closes": 20, "realized": 3436.0, "assignments": 3}` — and
the one that disagrees with the broker.

**Hypothesis, not a finding:** premium on a short option is not realised while
the option is open. The account holds exactly that shape —
`HOOD261009C00114000` at −$1,031 and `INTC261002C00120000` at −$649 unrealised.
Booking premium at sale banks the good half and leaves the bad half in
unrealised. Same shape as Gatekeeper's fee-blind +22% and BTCC's fake TP
banking, so it has form here.

## The honest ranking

- **Nothing is confirmed profitable.** The two credible-looking positives
  (keystone at +4.47%/+11.42%, wheel at +$3,436) are respectively *two
  contradictory answers from the same bot* and *an unauditable aggregate that
  disagrees with the broker*.
- The three bots with auditable per-trade dollar logs are all slightly negative
  over decent samples (345, 60, 45 closes).
- The most promising thing in the fleet remains the **+$2,736 unrealised** in
  the equities sleeve (HOOD, INTC, TSLA), which no bot log claims.

## Next

1. Reconcile the wheel's $3,436 against the journal's option activity. Until it
   resolves, do not quote it.
2. Pick one keystone and stop the other, or give them separate accounts. Two
   instances of one bot with divergent books is not a track record.
3. Give sentinel and gatekeeper a volume, or accept they have no history.
4. Compute fiveway's P/L via its `order_id`s — it is the only bot that made
   this possible, and the pattern the others should copy.
5. Tag `client_order_id` per bot. One line each, and every future version of
   this question is answerable from the source nobody can flatter.
