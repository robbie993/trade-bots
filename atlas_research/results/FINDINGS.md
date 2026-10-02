# Atlas Core, first run: what the numbers say (2026-10-02)

Hand-written reading of `REPORT.md`. The numbers are the script's; the
interpretation is mine and can be argued with.

**Verdict: research failure.** The frozen champion did not beat SPY on
return in its validation years (2023-24: +2.7% a year vs +26.0%), so under
the ladder it never became a candidate. This is the first and only run of
the design as specified; nothing was tuned after seeing a test or OOS result.

1. **The walk-forward record is the headline, and it is poor.** Stitched
   test years 2012-2024: Atlas +5.1% a year vs SPY +14.6%, max drawdown
   -24.8% vs -33.7%. It beat SPY in 2 of 13 calendar years and passed every
   gate in one window of seven (2020-21).
2. **The in-sample "win" is 2002 and 2008.** 2002-2024 in sample looks
   great (+11.5% vs +9.3%, drawdown -17% vs -55%), but 2008 alone was +55
   points of excess and the largest year is 211% of the total excess. Every
   training window contains 2008, so every genome that sidesteps one crash
   passes the train gates (547 of 564 in window 2). The gates then select
   for crash avoidance, and in a bull decade that costs return every year.
3. **The search did not find skill beyond luck vs SPY.** Reality Check
   p = 0.43, Hansen SPA p = 0.46, probability of backtest overfitting 58%.
   The deflated Sharpe is high, but that is Sharpe over *cash*, not over
   SPY: the strategy is a decent cash-plus asset allocator, not a SPY beater.
4. **The frozen OOS beat SPY (+42.7% vs +17.8%, drawdown -11.4% vs -18.8%),
   and it does not change the verdict.** The book returned +74% in total
   (SPY +29%). Summing each holding's daily contribution, silver and gold
   gave about 52 points of the roughly 60 the holdings added; they were 46%
   of the average book. Simple
   momentum (benchmark C) made +36% the same way. One 19-month precious
   metals run is one regime, not evidence of an edge, and the protocol said
   in advance that a champion which failed validation stays failed.
5. **Layers that helped in sample:** value (+1.4 pts CAGR) and low-vol
   (+2.0 pts) tilts; vol targeting cut drawdown 4 pts for 0.3 pts of return;
   the panic regime and trend filter added little on top of vol targeting.
6. **Costs are not the problem.** 83 bp a year at base; at 3x spread and 3x
   slippage the in-sample CAGR drops from 11.5% to 9.8%.

## What would be worth testing next (each a new pre-registered run)

* Train windows that do not all share 2008, or a train fitness that scores
  each year against SPY instead of the whole period, so crash avoidance in
  one year cannot buy a pass.
* Allowing the book to stay near 100% in calm bull regimes (the regime table
  shows 93% exposure in bull markets still trailing SPY by 6 points a year:
  that is selection, not sizing).
* The stock layers, once point-in-time fundamentals exist.
