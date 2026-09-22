# Which bot actually makes the money — 2026-09-22

Follow-on to `FLEET_PNL_2026-09-22.md`, which established that the shared
account is up $10.4k on paper with roughly nothing realised. This asks the
next question: **which bot?**

## The attribution problem, first

You cannot answer this from the broker. Every fleet bot submits to one account
(`PA3VG5AP6TO5`) and `client_order_id` is a bare UUID — no bot tag, no prefix,
nothing. Checked across all 1,895 journal records: every id is an anonymous
UUID. **The fleet is architecturally incapable of per-bot attribution at the
broker**, and no amount of reading the journal fixes that.

So the numbers below come from each bot's *own* log on its Railway volume.
That means they are self-reported, and the last section is about why that
matters.

## Realised P/L, as each bot reports it

| bot | realised | closes | source | shape |
|---|---|---|---|---|
| **wheel** | **+$3,436.00** | 20 | `wheel_learn.json` | aggregate only |
| picks_trader | −$356.64 | 60 trades | `picks_trader_state.json` | per-trade |
| btcc | −$73.20 | 345 | `btcc_trades.json` | per-trade |
| coinbase | −$4.43 | 45 | `cb_trades.json` | per-trade |
| fomo | — | 556 swaps | `fomo_trades.json` | unpaired swap log |
| scanner, form4, dividend | n/a | — | state files only | publishers, place no orders |
| keystone, fiveway | n/a | — | — | signals only, by design |

**Sum of the four that report: +$3,001.73.**

Corroboration worth noting: coinbase's −$0.10/trade average matches the
−$0.114/trade baseline recorded when its pre-registered freeze began, from a
completely separate measurement. That one is probably right.

## The gap, which is the actual finding

The broker says the account's realised is about **−$114** (total P/L
+$10,419.89 minus unrealised +$10,534). The bots say they realised
**+$3,002**. Those disagree by roughly **$3,100**.

One of them is wrong and it matters which, because +$3,436 from the wheel is
currently the entire case for "the fleet makes money".

**Hypothesis, not a finding:** the wheel sells options, and premium collected on
a short option is not realised while the option is still open. The account holds
exactly that — `HOOD261009C00114000` at −$1,031 unrealised and
`INTC261002C00120000` at −$649. If `wheel_learn.json` counts collected premium
as realised at the moment of sale, it books the good half of the trade and
leaves the bad half sitting in unrealised. That would produce this gap, in this
direction, at roughly this size.

This project has seen that exact shape twice before — Gatekeeper's fee-blind
+22%, and BTCC's "fake TP banking" — so it is a hypothesis with previous form,
not a wild guess.

It is also the one number with **no per-trade detail to check**:
`wheel_learn.json` is 75 bytes total, `{"cycles": 0, "closes": 20, "realized":
3436.0, "assignments": 3}`. Every other bot ships a trade list you can audit.

## What this says

- **The honest ranking today**: three of the four bots that report realised P/L
  are slightly negative, over decent samples (345, 60, 45 closes). The one
  positive number is the largest, the least detailed, and the one that
  disagrees with the broker.
- Not "the bots lose money". btcc's −$73 over 345 trades is approximately zero
  with a lot of activity, which is what an edgeless strategy in a fee-light
  paper account looks like.
- The equities sleeve's +$2,736 of *unrealised* gain (HOOD, INTC, TSLA) is
  still the most promising thing in the fleet, and none of these logs claims it.

## Next

1. Get per-trade detail out of the wheel, or reconcile its $3,436 against the
   journal's option activity. Until that resolves, do not quote it.
2. Pair `fomo_trades.json` into round-trips so it has a realised number at all.
3. Tag orders per bot. A `client_order_id` prefix per bot costs one line in each
   bot and makes every future question of this kind answerable from the broker
   alone, which is the only source nobody can accidentally flatter.
