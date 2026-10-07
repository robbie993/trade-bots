"""Trade-by-trade 'why' table from public sources (news, filings, bills), and a
test of whether trades with a legislative/regulatory link did better.

Each row: decision date, tickers, side, best public explanation, catalyst type,
was the catalyst public before the trade, legislative/regulatory link, whether
that link favored the trade, confidence, sources. Private intent: not
determinable. Sources are listed in REPORT.md section 6."""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "out"
# date, tickers, side, explanation, catalyst, public_before, leg_link, leg_direction, confidence
ROWS = [
 ("2014-10-24","SUNE","buy","Solar growth story after TerraForm yieldco IPO; First Wind deal announced 24 days later (+29%)","later news","after","solar tax-credit debate (ITC)","favorable","low"),
 ("2014-11-05","HTZ","buy","Activist turnaround: Icahn stake, accounting restatement, CEO search; new CEO named 11-20","activist","before","none","n/a","medium"),
 ("2014-11-28","DIS","buy","Star Wars: The Force Awakens teaser released the same day","company news","same day","none","n/a","medium"),
 ("2015-07-15","HTZ","buy","Hertz completed its restatement 07-16, stock +14%","company news","same/next day","none","n/a","medium"),
 ("2016-01-13","AAPL","buy","Dip buy: AAPL fell below $100 on iPhone slowdown fears","dip","before","none","n/a","medium"),
 ("2016-05-17","AAPL,SQ","buy","Day after Buffett's Berkshire disclosed a $1B Apple stake","famous investor","before","none","n/a","high"),
 ("2018-02-02","AAPL","buy","Day after record Q1 earnings","post-earnings","before","none","n/a","high"),
 ("2018-03-27","DBX","buy","4 days after Dropbox IPO (+36% day one); Bay Area tech","IPO","before","none","n/a","medium"),
 ("2018-07-27","AMZN,FB","buy","Day after FB's record one-day crash and Amazon's earnings beat","dip + post-earnings","before","none","n/a","high"),
 ("2018-09-11","AAPL","buy","Day before iPhone XS launch event; strong momentum","event","before","none","n/a","low"),
 ("2018-10-09","AMZN,FB","buy","Into the Oct-2018 tech selloff","dip","before","none","n/a","medium"),
 ("2018-10-24","T","buy","Same day AT&T missed and fell ~8%; 6%+ yield","dip + post-earnings","before","none","n/a","high"),
 ("2019-06-14","CRM","buy","Days after the $15.7B Tableau deal pulled the stock back","dip after deal","before","none","n/a","medium"),
 ("2019-07-05","NFLX","buy","12 days before earnings; momentum","pre-earnings","n/a","none","n/a","low"),
 ("2019-07-22","AMZN,NFLX","buy","NFLX after first US subscriber loss (-10%); AMZN 3 days before earnings","dip + pre-earnings","before","House antitrust probe of Big Tech (hearing 07-16)","unfavorable","medium"),
 ("2020-02-20","MSFT,WORK","buy","Bought at the market top as the COVID crash began 02-24","momentum","n/a","Army IVAS HoloLens contract (awarded 2021-03-31)","favorable","low"),
 ("2020-02-27","GOOGL,MSFT","buy","Bought during the first COVID crash week","dip","before","none","n/a","medium"),
 ("2020-06-12","PYPL","buy","Pandemic e-commerce momentum (+52% in 3 months)","momentum","before","none","n/a","medium"),
 ("2020-06-24","AXP,PYPL","buy","Same theme; AXP recovery","momentum","before","none","n/a","low"),
 ("2020-09-03","CRWD","buy","2 days after CrowdStrike's earnings beat","post-earnings","before","none","n/a","high"),
 ("2020-12-22","AAPL,DIS,TSLA","buy","TSLA joined S&P 500 on 12-21; DIS after record Investor Day 12-10","index add + company news","before","EV credits in BBB/IRA (TSLA)","favorable","medium"),
 ("2020-12-22","AB","buy","AllianceBernstein units for income; repeated adds","income","n/a","none","n/a","low"),
 ("2021-03-10","RBLX","buy","Bought on Roblox's direct-listing day","IPO","same day","none","n/a","high"),
 ("2021-05-21","AAPL,AMZN","buy","AMZN: DoD cancelled JEDI and opened JWCC to Amazon 07-06 (cancellation reported as under review in May)","later news","partly public","Pentagon cloud contract (JEDI/JWCC)","favorable","medium"),
 ("2021-06-03","NVDA","buy","After 4:1 split announcement (05-21) and record Q1 (05-26); USICA chip subsidies passed Senate 06-08","split + post-earnings","before","USICA / CHIPS chip subsidies","favorable","medium"),
 ("2021-06-18","GOOGL","buy (exercise)","Exercised GOOGL calls days before House Judiciary passed six Big Tech antitrust bills (06-24)","option lifecycle","n/a","Big Tech antitrust bills","unfavorable","medium"),
 ("2021-07-23","NVDA","buy","Right after the split took effect (07-20); momentum","momentum","before","USICA / CHIPS chip subsidies","favorable","medium"),
 ("2021-12-17","DIS,GOOG","buy","Days after Pelosi defended member trading (12-15); dip buys off highs","dip","before","AICOA Big Tech bill pending (GOOG)","unfavorable","low"),
 ("2021-12-20","CRM,MU,RBLX","buy","Dip buys: CRM -20% after Dec guidance, RBLX -27% from Nov peak","dip","before","none","n/a","medium"),
 ("2022-05-13","AAPL","buy","Bear-market dip buy (AAPL -19% off high)","dip","before","AICOA Big Tech bill pending","unfavorable","medium"),
 ("2022-05-24","AAPL,MSFT","buy","Bear-market dip buy (MSFT -24% off high)","dip","before","AICOA Big Tech bill pending","unfavorable","medium"),
 ("2022-06-17","NVDA","buy (exercise)","Exercised 200 calls ~1 month before CHIPS Act votes (07-27/28)","option lifecycle","n/a","CHIPS and Science Act","favorable","medium"),
 ("2022-07-26","NVDA","sell","Sold 25,000 NVDA at a $341k loss the day before the CHIPS vote, after public pressure; filed next day","public pressure","before","CHIPS and Science Act","against-trade","high"),
 ("2022-12-20","DIS,GOOGL,PYPL,TSLA","sell","Year-end tax-loss selling; Pelosi had left leadership 11-17","tax","n/a","DOJ Google ad-tech suit (filed 2023-01-24; reported as coming since Aug 2022)","favorable","medium"),
 ("2022-12-28","AB,GOOGL,NFLX,PYPL,RBLX","sell","Year-end tax-loss selling","tax","n/a","DOJ Google ad-tech suit (see above)","favorable","medium"),
 ("2023-11-22","NVDA","buy","Day after NVDA Q3 FY24 earnings","post-earnings","before","none","n/a","high"),
 ("2024-02-12","PANW","buy","8 days before earnings; strong momentum","momentum","before","none","n/a","medium"),
 ("2024-02-21","PANW","buy","Day after PANW fell 28% on cut guidance","dip + post-earnings","before","none","n/a","high"),
 ("2024-06-24","AVGO","buy","After earnings and the 10-for-1 split announcement (06-12)","split + post-earnings","before","none","n/a","high"),
 ("2024-06-26","NVDA","buy","After NVDA's 10-for-1 split (06-10); AI momentum","momentum","before","none","n/a","medium"),
 ("2024-07-01","V","sell","Long-run trimming of the Visa IPO stake; DOJ debit suit came 09-24 (probe public since 2021)","rebalancing","n/a","DOJ Visa debit probe","favorable","medium"),
 ("2024-07-26","NVDA","buy","Dip buy: NVDA -17% off high","dip","before","none","n/a","medium"),
 ("2024-07-26","MSFT","sell","Sold 4 days before earnings, a week after the CrowdStrike/Microsoft outage","rebalancing","n/a","none","n/a","low"),
 ("2024-12-31","AAPL,NVDA","sell","Year-end gains realization; AAPL near all-time high","tax","n/a","none","n/a","medium"),
 ("2025-01-14","AMZN,GOOGL,NVDA,TEM,VST","buy","AI and data-center-power theme a week before inauguration; VST +33% in a month","theme momentum","before","none found","n/a","medium"),
 ("2025-12-24","AAPL,AMZN,NVDA","sell","Year-end; Pelosi announced retirement Nov 2025; paired with charity gifts","tax / estate","n/a","none","n/a","medium"),
 ("2025-12-30","AAPL,AMZN,GOOGL,NVDA","buy","Rolled into 1-year deep-ITM calls after selling stock (keeps exposure, frees cash)","roll","n/a","none","n/a","medium"),
 ("2025-12-30","DIS,PYPL","sell","Year-end; PYPL later fell on Feb-2026 guidance and CEO change (not public in Dec)","tax","n/a","none","n/a","medium"),
 ("2026-05-29","INTC","buy","Momentum: Intel +170% to records on reported Apple foundry deal (05-07); US government stake (Aug 2025)","momentum","before","US government 10% Intel stake / CHIPS grants","favorable","medium"),
 ("2026-05-29","UBER","buy","Contrarian: near lows on robotaxi fears (Waymo, Tesla)","dip","before","none","n/a","medium"),
 ("2026-07-24","INTC","buy","Day after Intel's Q2 blowout (rev +25%, AI +59%)","post-earnings","before","US government Intel stake","favorable","high"),
 ("2026-07-24","BE","buy","Dip buy after Bloom Energy's July crash; AI data-center power theme; earnings 07-28","dip + theme","before","none","n/a","medium"),
]
cols = ["date", "tickers", "side", "explanation", "catalyst", "public_before", "leg_link", "leg_direction", "confidence"]
W = pd.DataFrame(ROWS, columns=cols)
W.to_csv(OUT / "why_trades.csv", index=False)

ev = pd.read_csv(OUT / "events_realistic.csv", parse_dates=["tdate"])
def fwd(r):
    t = set(r.tickers.replace("FB", "META").replace("SQ", "XYZ").split(","))
    m = ev[(ev.tdate == pd.Timestamp(r.date)) & ev.ticker.isin(t)]
    return m["x252_SPY"].mean() if len(m) else np.nan
B = W[W.side == "buy"].copy()
B["x252"] = B.apply(fwd, axis=1)
B["leg"] = ~B.leg_link.isin(["none", "none found"])
print("buy decisions", len(B), "with a legislative/regulatory link", int(B.leg.sum()))
print(B.groupby("leg").x252.agg(["count", "mean", "median"]).round(3))
print(B.groupby("leg_direction").x252.agg(["count", "mean"]).round(3))
cat = B.catalyst.str.split(" ").str[0].replace({"post-earnings": "post-earnings", "dip": "dip"})
B["kind"] = np.select([B.catalyst.str.contains("dip"), B.catalyst.str.contains("earnings|split"),
                       B.catalyst.str.contains("momentum"), B.catalyst.str.contains("IPO")],
                      ["dip buy", "earnings/split", "momentum", "IPO"], "other")
print(B.groupby("kind").x252.agg(["count", "mean", "median"]).round(3))
print("public catalyst before or same day:", (B.public_before.isin(["before", "same day", "same/next day"])).mean().round(2))
B.to_csv(OUT / "why_buys_scored.csv", index=False)
