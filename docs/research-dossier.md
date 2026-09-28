# Trading Research Dossier

Everything assembled, verified, and concluded across one research session — plus a
concrete proposal for what to build with it.

**Compiled:** September 2026
**Method:** 117 live web searches across five verification rounds
**Inputs:** four research documents supplied by the user, containing ~98 distinct claims
**Status:** 70 sources verified, 8 unlocated, 4 disputed claims, 6 of my own verdicts reversed

Companion artifacts (readable, with live links):

- **Trading Research Ledger** — every verified source with corrected identifiers
- **Unverified Citations Register** — what could not be located, and why

---

## 0. Provenance and reliability

Four documents were supplied over the session. Each failed differently, and the
differences are diagnostic — they tell you how much weight the contents carry.

| Batch | Contents | Verified | Failure mode |
|---|---|---|---|
| **A** | 11 papers — trader skill, information edge, classic strategy | 8 | **Fabricated entries with clean citations.** Hardest to catch: nothing looks wrong until you check. |
| **B** | 14 papers — momentum, options, crypto, market making, stat arb | 12 | **Real papers, understated summaries.** Findings consistently weaker or vaguer than the source. |
| **C** | ~45 entries — AI trading bots, agent villages, LLM systems | ~40 | **Real papers, corrupted bibliographies.** Half the URLs were truncated placeholders; the papers behind them were mostly genuine. |
| **D** | ~28 claims — HFT economics, firm margins, PFOF, colocation | ~22 | **Real papers, drifted numbers.** Wrong units, wrong journals, rounded-up figures, one paper cited backwards. |

A fifth document arrived claiming to be a transcript of this session. It was not — it
described a different conversation with roles inverted (it claimed the assistant could
not browse and that the user did the verification; here the reverse was true). Its
Parts 5 and 6 accurately reproduced this session's findings; Parts 11–22 were new
material, checked separately and recorded below as Batch D.

**The rule that emerged:** a *specific identifier that resolves to nothing* is strong
negative evidence. A *title* that returns nothing often just means the title was
incomplete. Three of my own false negatives were incomplete queries, not bad citations.

---

## 1. Batch A — Trader skill, information edge, classic strategy

### A1. Insider Trading and the Importance of an Ivy Education
Didisheim, Fraschini & Somoza · SSRN 5294442 · June 2025

Hedge fund managers sharing an elite alma mater show abnormal return correlation
(Columbia, Harvard, Penn, Stanford, NYU). Causal evidence is a difference-in-differences
design around the **2009 Galleon Capital scandal** — elite-school managers' returns drop
once information-sharing became radioactive. Elite-school managers raise **55% more
capital** launching a first fund.

> **Correction:** the original summary described generic "network advantage." The paper
> documents an *illegal information channel*. Legitimate use is as a detection signature —
> unexplained co-movement among similarly networked managers — never as a strategy.

### A2. Intraday Market Return Predictability Culled from the Factor Zoo
Aleti, Bollerslev & Siggaard · *Management Science* 71(9), 7731–7751 · Sept 2025

Predicts intraday market return from the lagged high-frequency cross-section of the
factor zoo, using ML regularization to tame signal count and financial econometrics to
separate continuous from theoretically unpredictable jump increments. Out-of-sample
Sharpe and alpha on liquid ETFs after costs.

### A3. Attention Factors for Statistical Arbitrage
Epstein, Wang, Choi (Hanwha Life) & Pelger (Stanford) · arXiv 2510.11616 · ICAIF 2025

Conditional latent factors from firm-characteristic embeddings jointly identify similar
assets, mispricing, and a trading policy. **Sharpe >4 gross over 24 years; 2.3 net of
transaction costs.** 84% improvement in net Sharpe over prior work.

> **Correction:** Batch A quoted ">4" without the gross/net distinction. Batch C's
> "Stanford & Hanwha Life" attribution was the accurate one.

### A4. Can Individual Investors Beat the Market?
Coval, Hirshleifer & Shumway · *Review of Asset Pricing Studies* 11(3), 552 · 2021

Top-decile investors earn **15 bps/day** on trades. The load-bearing finding is
persistence: first-half top-decile investors earn ~**6%/year** risk-adjusted in the
second half. Skill exists, is rare, and is detectable only by split-sample testing.

### A5. Strategic Sophistication and Trading Profits
Angrisani, Cipriani & Guarino · NY Fed Staff Report 1044

56 professional traders plus student controls. **Strategic sophistication (level-k) is
the only significant predictor of professional traders' profits** — cognitive ability is
not. The relationship inverts for students. Profits come from trading at favorable
prices, not from better information.

### A6. A Note on Trader Sharpe Ratios
Coates & Page · *PLOS ONE* 4(11):e8036 · 2009

53 City of London traders. Beginners Sharpe **0.39**; experienced **1.02**.

> **Correction:** the experience result is the control variable. The paper's actual
> subject — omitted from the original summary — is the **2D:4D digit ratio** as a
> prenatal-androgen biomarker predicting trading success.

### A7. Mental Capabilities, Heterogeneous Trading Behaviour and Performance
Hefti, Heinke & Schneider · *The Economic Journal* 135(671), 2161 · 2025

Analytical and mentalizing capability are **independent and non-convertible**.
Performance is non-monotonic in either alone; only traders strong in both succeed.

### A8. In Search of Sustainable Successful Traders in Times of Crisis
HEC Lausanne, St Gallen & ZHAW · *Management international* 28(5) · 2024

Conscientiousness + intelligence + **moderate** (not high) financial risk-taking predicts
sustained responsible performance. Framed around the 2011 UBS rogue-trader loss.

---

## 2. Batch B — Strategy frontier, 2024–2026

### Momentum

**B1. Enhanced Momentum with Momentum Transformers** — arXiv 2412.12516.
Attention + LSTM. **4.14% annualized, Sharpe 1.12.** An explicit extension of a
futures/index paper into equities; authors attribute the lower Sharpe to equity vol.

**B2. Taming Momentum Crashes** — Bianchi, De Polis & Petrella, CEPR DP19030.
Crash indicator is the **interaction of conditional volatility AND skewness**, not skew
alone. Dynamic skewness-adjusted max-Sharpe beats vol scaling. Also: momentum skewness
*cannot be fully reconciled with asymmetric market exposure* — it is structural to
momentum, which is what makes it hard to hedge.

**B3. Deep Momentum Networks with Market Trend Dynamics** — *PLOS ONE* 2025.
99 continuous futures, 1995–2021. Real finding: an **8-week fast lookback wins in stable
regimes; a 20-week window outperforms and recovers faster during breaks like COVID.**
The actionable claim is regime-conditional lookback length.

### Options

**B4. Options Wheel with LLM-Generated Bayesian Networks** — arXiv 2512.01123.
LLM builds network structure and retrieves analogous scenarios from an 18.75-year,
8,919-trade dataset; the Bayesian network does inference. **15.3% annualized,
Sharpe 1.08 vs 0.62, max drawdown −8.2% vs −60%, 0% assignment.**

> **Open question that decides everything:** is the LLM's scenario retrieval strictly
> walk-forward? If it can pull from anywhere in the 18.75-year window, the drawdown
> figure partly reflects having seen how similar setups resolved.

**B5. Italian Electricity Derivatives (Grigolin, Padova)** — **NOT FOUND.** The
underlying logic is independently sound: trend following as a liquidity-friendly proxy
for long-straddle convexity, winning whenever implied vol exceeds realized.

### Crypto and social signals

**B6. Technical Analysis Meets Machine Learning: Bitcoin** — arXiv 2511.00665.
LSTM ~65.23% cumulative in under a year, beating LightGBM, EMA, MACD+ADX and buy-hold.
*Caveat: the test window sits inside the post-ETF bull run the paper cites as its own
motivation. Single-seed LSTM results on crypto are unstable run to run.*

**B7. Crypto DRL with XGBoost feature selection** — *Applied Soft Computing* 175:113029.
XGBoost selects features → DDQN with LSTM/BiLSTM/GRU. **On-chain variables carry
information price-only models cannot.** Test window **Jul 2021 – Mar 2023**, spanning
Terra/Luna and FTX — far more demanding than B6.

**B8. Wisdom of the Crowd Signals** — Haase, Celig, Rath & Schoder, *Electronic Markets*
35:64. 28,000+ **explicit** buy/sell calls (not aggregate sentiment) from X, Reddit,
Stocktwits, Telegram. Predicts short-term moves, strongest for low-cap and
recently-negative assets. *The paper's own finding that automated accounts propagate
these signals is the decay clock built into the result.*

**B9. Reddit WSB + FinBERT (Contino, LUISS)** — **NOT FOUND.** Notably the only entry in
Batch B with no quantitative results claimed at all.

### Market making

**B10. The Market Maker's Dilemma** — Albers, Cucuringu, Howison & Shestopaloff,
arXiv 2502.18625. **Live Binance perpetuals maker experiment** — real fills, the highest
evidentiary standard in the entire corpus. Fill probability and post-fill return are
negatively related. **All imbalance-based maker strategies produced negative returns, and
the taker strategy was worst after fees despite being best before them.**

**B11. Event-Based LOB Simulation under a Neural Hawkes Process** — *Applied Mathematical
Finance* 32(2). Twelve LOB event types, LSTM-driven Hawkes intensities.
**Infrastructure, not a strategy** — it produces a training environment, not an edge.

### Mean reversion and statistical arbitrage

**B12. Deep Mean-Reversion / ORCA** — ICAIF 2025, Hanyang. ORCA = **O**rnstein-Uhlenbeck
**R**eversion and **C**ontrastive **A**rbitrage. Contrastive representation learning for
pair formation; the execution rule is a deliberately trivial static threshold, so any
edge lives entirely in the clustering step.

**B13. Optimal Mean-Reversion Arbitrage with Advanced Hedge Ratios** — Aalto thesis.
S&P 500, Jan 2018 – Dec 2023. PCA + OPTICS clustering with cointegration testing. Six
hedge-ratio estimators compared: OLS, TLS, Johansen, Box-Tiao, Minimum Half-Life,
Minimum ADF. **Minimum Half-Life and Box-Tiao consistently beat OLS/TLS with significant
alpha.**

> **The most actionable finding in the corpus.** Hedge-ratio estimator choice materially
> changes realized performance, is one variable, and most implementations default to OLS.

**B14. PCA Pairs Trading, Chinese Equity Market** — Yufei Sun, University of Warsaw
WP 2025-21. Companion paper WP 2025-23 stress-tests the regime-robustness claim during
extreme events.

---

## 3. Batch C — AI trading systems and agents

### Agentic systems

- **TiMi (Trade in Minutes)** — arXiv 2510.04787, ICLR 2026, with Microsoft Research.
  Decouples strategy development from minute-level deployment. **Explicitly argues
  against anthropomorphic agents** because simulating human roles imports emotional bias.
- **ATLAS** — arXiv 2510.15949, ACL 2026. **Order-aware action space** so outputs are
  executable orders, not abstract signals. Adaptive-OPRO prompt optimization under
  delayed, noisy reward.
- **Adaptive Alpha Weighting with PPO** — arXiv 2509.01393, Chen & Kawashima, *IJDSA*
  2026. PPO weights 50 LLM-generated alphas. **Does not consistently produce the highest
  returns** — produces better Sharpe and smaller drawdowns. The most honest reporting in
  the batch.
- **AgenticAITA** — arXiv 2605.12532, Letteri. Z-score trigger gating LLM inference to
  anomalous conditions only; **Analyst → Risk Manager → Executor** with typed JSON
  contracts and a deterministic hard-gate safety layer. Nearly a spec of this repo.
- **FinRL-DeepSeek** — arXiv 2502.07393. CVaR-PPO plus LLM risk signals from news.
  **PPO wins in bull markets, CPPO-DeepSeek in bear markets** — regime-dependent, not a
  uniform improvement.

### Multi-agent simulations

- **TradingAgents** — arXiv 2412.20138, UCLA/MIT/Tauric. Bull and Bear researcher agents
  argue against each other; analysts, traders, risk managers.
- **TwinMarket** — arXiv 2502.01506. BDI architecture, 1,000 agents, reproduces fat tails
  and volatility clustering. *Credential correction: Best Paper at the Financial AI
  **Workshop** @ ICLR 2025, not an ICLR main-conference best paper.*
- **MarketSim** — ICML 2026 poster **65297** (not 22666). 15,000+ participants,
  nanosecond-resolution CDA, grounded in 12k news documents. *The `parkercarrus/MarketSim`
  GitHub describes RL traders, not generative LLM agents — link unconfirmed.*
- **StockSim** — arXiv **2507.09255** (not …226). Dual-mode order-level simulator that
  explicitly models **latency, slippage and order-book microstructure**. The most
  directly reusable infrastructure in the batch.
- **Agent Trading Arena** — **EMNLP 2025 Findings 2025.findings-emnlp.294**.
  **LLMs reason poorly about numbers as plain text but markedly better when the same data
  is given as charts.** Follow-up DecoupledMarket accepted to ICML 2026.
- **FinMem** — arXiv 2311.13743. Profiling / layered Memory / Decision-making.
- **FinRobot** — **two** real papers: arXiv 2405.14767 (platform) and 2411.08804 (equity
  research, Data-CoT → Concept-CoT → Thesis-CoT). Canonical repo is AI4Finance-Foundation.

### Stock trading

- **"Terminator" AI fund manager** — Ed deHaan (Stanford GSB) + Boston College. Random
  forest retro-applied to 3,300+ mutual fund portfolios 1990–2020, beating 93% of human
  managers — **substantially by de-risking into broad index funds.** Press coverage only;
  no live fund, no alpha discovery.
- **Trading-R1** — arXiv 2509.11420. UCLA/UW/Stanford/Tauric. **4B Qwen3 backbone.**
  Held-out Jun–Aug 2024: NVDA Sharpe **2.72**, 70.0% hit rate, max DD 3.80% vs 7.88% for
  baselines; SPY Sharpe 1.60, 64.0% hit rate.
- **AI-Powered Trading, Algorithmic Collusion, and Price Efficiency** — Dou, Goldstein &
  Ji, **NBER w34054**. The only citation supplied completely correctly, and substantively
  the most important for a multi-agent system: **RL traders learn to collude without
  explicit coordination**, via price-trigger punishment or **homogenised learning biases**.
- **Increase Alpha** — arXiv 2509.16707. 800+ US equities, daily directional signals.
  Deliberately **rejects large transformers** for feed-forward/recurrent nets.
- **Autonomous AI Trader in Experimental Asset Markets** — Ackert et al., SSRN 5691233,
  *JEBO*. The AI is consistently top performer; **human wealth declines significantly;
  mispricing does not improve.** "No evidence that an autonomous AI trader promotes the
  health of capital markets." A redistribution result, not an efficiency result.
- **AI-Trader benchmark** — arXiv 2512.10971, Tianyu Fan et al., HKU. First live,
  data-uncontaminated LLM trading benchmark across NASDAQ-100, SSE 50 and 10 crypto
  assets. **"General intelligence does not automatically translate to effective trading
  capability; most agents exhibited poor returns and weak risk management."** Risk control,
  not reasoning, determined cross-market robustness.

### Crypto

- **FinNLP Crypto Trading Challenge** — COLING 2025 (not ACL). Task overview
  2025.finnlp-1.46; entries 1.47 (Sam's Fans, FinMem-based) and 1.48 (300k/ns).
- **Bitcoin DQN** — *Cogent Economics & Finance* 13(1), Nguyen Thi Thu Hoan, Diplomatic
  Academy of Vietnam. DQN selects among RSI / SMA crossover / Bollinger / Momentum-20d /
  VWAP reversion. **Claims >120× NAV growth 2022–mid-2025. Neither verification pass
  could source that figure. Bitcoin itself did ~4–5× over the window. Do not use.**
- **Adaptive Multi-Agent Bitcoin** — arXiv 2510.08068, UCL. Verbal feedback loop: a
  Reflect agent writes daily/weekly critiques injected into future prompts, no weight
  updates.
- **Zero-Shot Multi-Agent BTC** — Hae Sun Jung et al., *IPM* Vol 63, 2026. 1,400 days;
  technical + on-chain + macro + textual via role-specific agents; GPT-4o meta-agent beat
  time-series baselines on a three-day window.

### Options, RL, surveys

- **OQL — Natural Language to Executable Option Strategies** — arXiv 2603.16434. A
  domain-specific intermediate language reduces the LLM to a **semantic parser**; a
  deterministic engine validates and executes.
- **GEX-LLM Patterns** — arXiv 2512.17923, IEEE LLM-Finance 2025. LLMs reconstruct dealer
  positioning from raw strike-level data, outperforming pre-calculated metrics. **Best
  finding is negative: detection stays stable at 68–74% quarterly while economic
  profitability collapses** — structural mechanics, not exploitable anomalies.
- **FLAG-TRADER** — ACL Findings **2025.findings-acl.716**. A partially fine-tuned LLM
  *as the policy network*, optimized by PPO.
- **GEMs-LLM** — XJTLU/Springer. Hierarchical goal-setting controller + low-level executor,
  DeepSeek-V3 refinement. Beats DDPG and OPD on annualized Sharpe and downside deviation.
- **Surveys** — LLM Agents in Finance (EMNLP Findings 972); The New Quant (arXiv
  2510.05533, Weilong Fu, Columbia — flags temporal leakage, hallucination, deployment
  economics); LLMs in Equity Markets (*Frontiers in AI* 8:1608365, 84 studies).

### Institutional

- **Large (and Deep) Factor Models** — Kelly, Malamud & Zhou, arXiv 2402.06635 +
  *Journal of Finance* 2024 + NBER w31689. A wide, arbitrarily deep NN maximizing SDF
  Sharpe is equivalent to a large linear factor model over nonlinear characteristics.
  **Out-of-sample performance increases with depth, saturating around 100 hidden layers.**
- **AQuA** — arXiv 2608.12841, Princeton/Ant Group/Stanford. Two independent
  self-improving research loops sharing no agents, memory or state. US equity strategy
  Sharpe **2.5**, positive every year across five years; held-out model IC 0.0843 vs
  0.0613 for GRU. Framed as learning from its own backtests **without cheating.**

### Critical perspectives

- **Homo Silicus is Hyper-Rational** — SSRN 5901742, John Garcia. LLM agents show a
  **reversed disposition effect — selling losers 3:1 relative to winners.** Even when
  explicitly prompted to exhibit FOMO, they retreat once costs are calculable.
  **"A strict constraint hierarchy in which quantitative optimization overrides
  persona-level instructions."** Persona prompts are cosmetic.
- **AI-Driven Alpha Decay** — arXiv 2605.23905. Signal crowding, performative erosion,
  Red Queen competition. Alpha half-life `h(φ) = ln2/[θ+δ(φ)]` implying **~18 months vs
  5–7 years pre-AI.** *Theoretical model with calibrated parameters, not a measurement.*
- **The Growth and Performance of AI in Asset Management** — Chen, Sialm & Xu,
  **NBER w35273**. 116,000+ SEC adviser disclosures, 2012–2024. AI hedge funds
  outperformed early; **that outperformance declined over time even among early adopters.**
  And critically: **AI funds show *lower* return comovement than non-AI peers —
  "contrary to concerns about strategy homogeneity."**
- **Profit Mirage** — arXiv 2510.07920. Backtest returns evaporate past the knowledge
  window because of **pre-training contamination**: LLMs memorize past price moves *and
  the post-hoc explanations written about them*.
- **The Alpha Illusion** — arXiv 2605.16895. Names **FinCon, FinMem, TradingAgents,
  FinAgent, QuantAgent and FLAG-Trader** — four of which are in this dossier — and argues
  their reported alpha must survive tests for **temporal integrity, real-world frictions,
  counterfactual robustness, predictive calibration, numerical execution, and multi-agent
  disaggregation** before it counts as deployment evidence.
- **No Lasting Edge** — Alexander Izydorczyk, ex-head of data science at Coatue, now NX1
  Capital: **no AI trading bot he tracks has shown a lasting edge.** His caveat is the
  sharper half: *"When LLM agent trading strategies start working, you will not hear about
  it for a while."*
- **CNMV ten-month live study** — *"Large Language Models and Stock Investing: Is the
  Human Factor Required?"*, Crisóstomo & Mykhalyuk, SSRN 6612061 / arXiv 2603.19944.
  April 2025 – January 2026 on the Ibex 35 with ChatGPT, Gemini, DeepSeek, Perplexity.
  The paper's answer to its own title is **yes**: recommendations suffer "financial
  misconceptions, carryover errors, and reliance on outdated or hallucinated information";
  outperformance is conditional on substantial human oversight.

---

## 4. Batch D — HFT economics, firm margins, market structure

### Confirmed

| Claim | Verified |
|---|---|
| Jane Street ~69% operating margin Q2 2025 | **~68%** — $6.9bn profit on $10.1bn revenue |
| Baron/Brogaard/Hagströmer/Kirilenko, HFT Sharpe | Real, *JFQA* 2019 54(3). Annualized **4.3**; median **4.5** across firms |
| Menkveld single HFT market maker Sharpe **9.35** | Exact — derived from **€2.052M max capital committed per stock**. *JFM* 2013 |
| Cornell, Medallion $100 → **$398.7M**, 63.3% compound | Exact. *JPM* 46(4) 2020. Never a negative year in 31; beta and factor loadings all negative |
| Aggressive HFTs earn **~45% of revenue by adversely selecting other HFTs** | Confirmed — Baron/Brogaard/Kirilenko, E-mini S&P 500 |
| Relative tick size benefits HFT market makers | Real — **O'Hara, Saar & Zhong**, unique NYSE data |
| Virtu FY2024 communications & data processing **$236.4M** | Exact — $236,446 thousand |
| Chicago–NY: fiber **6.6ms**, microwave **4.1ms** | Confirmed. 1ms on that route ≈ $100M/yr to a large firm |
| CME matching engine, Aurora IL | Confirmed — 428,000 sq ft, sold to CyrusOne 2016. Colocation ~**$12,000/month** |
| AMD Alveo UL3524 sub-3ns transceiver latency | Confirmed (7× improvement, 64 ULL transceivers) |
| PFOF field experiment, German neo-broker | Real — Elsas-Nicolle, Johanning & Theissen, SSRN 4304916, DGF best paper |
| Strategy Distinctiveness Index | Real — Sun, Wang & Zheng, *RFS* 2012 |
| Medallion capacity cap | **~$10bn**, enforced by distributing profits back annually. Closed to outside capital since 1993 |

### Wrong, stale, or mis-measured

- **Charles-Cadogan is cited backwards, and it changes the conclusion.** Presented as
  evidence of HFT profitability. The paper argues the reported **4.3-to-5,000 Sharpe range
  is misspecified**, notes experienced traders run "much less than 2," and introduces a
  corrected Efficient Sharpe Ratio: **aggressive 1.15, medium 2.88, passive 1.43,
  industry-wide 1.07–1.87.**
- **QJE paper misattributed.** "Quantifying the HFT Arms Race" (QJE 2022) is **Aquilina,
  Budish & O'Neill** — not Budish, Cramton & Shim, who wrote the *different* 2015 paper on
  frequent batch auctions. Real figures: ~1 race/minute/symbol, modal race 5–10
  microseconds, top 6 firms >80% of race wins, **~20% of trading volume**. The "17%
  liquidity cost reduction / $5B annually" did not surface.
- **Rule 605 claim is wrong and out of date.** Claimed to exclude "over 65% of executed
  volume." Pre-2024 it excluded sub-100-share orders — ~45% of *trades* but only ~10% of
  *volume* — and the **SEC amended Rule 605 in March 2024** to include odd lots, fractional
  shares and larger orders.
- **Garvey & Murphy is a pre-decimalization study.** Real (*JEF* 2005, 12(5)): 15-person
  team, **$1.4M over three months in 2000, before commissions**, **$24.30 per round trip**
  on 1,000–1,200 share lots. Decimalization ended that market in 2001.
- **"Displaced by Big Data" has a units error.** Bonelli & Foucault (HEC Paris) find a
  **0.106 decline in stock-picking ability at four quarters, ~⅓ of a standard deviation** —
  a standardized skill measure, rendered in the source document as "0.11% alpha per year."
  Also omitted: specialist funds see declines **10–20× larger**.
- **Penn State dark pool thesis** — real, but tested a **$25,000** portfolio against SPY.
  The "1,163% over 60 days" figure did not surface.
- **Satellite parking lots** — real literature (Zhu 2019; Katona et al. 2018), but the
  quantified result is **4–5% over three days around earnings**, not "1.6% monthly alpha."
- **SDI numbers loose** — real finding is **~3.5%** top-vs-bottom quintile in the following
  year, not "4% annually with 24-month persistence."
- **Teo "Flagship Funds at Hedge Fund Families" doesn't exist under that title.** The real
  work is **"Hedge Fund Franchises"** — and it points the other way: first funds
  *outperform* follow-ons; multi-product firms *underperform* while harvesting more fees.
- **Zero-commissions paper: wrong journal and one-sided.** *Journal of Empirical Finance*
  79 (2024), Jain/Mishra/O'Donoghue/Zhao. Omits that **effective spreads declined** and
  **price impact fell**.
- **Chinese retail order imbalance: >15%, not ~20%** — and the horizon is intraday
  (positive 5–30 min, reversing negative 60–120 min), not weekly.
- **Citadel's retail share contradicts itself** — 35–40% in one section, **47%** in another.
  Citadel Securities' own figure is **~35%**.
- **Unconfirmed:** AMD Alveo at $119,873.99 (no pricing anywhere); Virtu's "$27.7M into
  microwave JVs in 2024"; the Monash ASX colocation paper; kernel bypass "120μs → 15μs,
  **2.3% annualized return improvement**" — the technology is real, that causal return
  figure is not sourceable and would be nearly impossible to isolate.

---

## 5. Unverified after two passes

| Item | Verdict |
|---|---|
| *Psychological Backgrounds… Forex/Crypto* (JAMV 2026) | **Fabricated or predatory.** The journal name itself leaves no trace |
| AI-FINALYST (Ravjot Kaur) | **Not found** even with the author name — which broke four other cases open. Real neighbours: FinDebate, AlphaAgents |
| Wall Street of AI Agents (GitHub) | **Not found.** Closest real match, AI Hedge Fund, has 14 personas not 4 |
| *The Ivy-Portfolio: An Empirical Analysis* (Hannover 2014) | **Misattributed.** The real source is Faber & Richardson's *The Ivy Portfolio* |
| Gothenburg pairs trading, Sharpe >2 | **Not found**, and the number is out of line — the literature reports 1.0–1.2 net |
| Italian electricity derivatives (Grigolin, Padova) | **Unindexed thesis, most likely** |
| Reddit WSB + FinBERT (Contino, LUISS) | **Unindexed thesis, most likely** |
| "Crypto Trading Village, 22 agents" | **Misdescribed.** Closest real event: LI.FI's "Agents of Wall Street" — **5 agents, 1,000 USDC, tokenised stocks on Solana**, most finishing negative |

**Disputed claims on real papers:** FinRL-DeepSeek's "Cambridge risk-first architecture";
the 120× Bitcoin NAV; the Hybrid DRL commodity futures composition; TradingAgents' star
count.

---

## 6. What the evidence actually says

### 6.1 Every credible number converges on Sharpe 1–2

| Source | Net Sharpe |
|---|---|
| HFT industry, corrected (Charles-Cadogan) | **1.07–1.87** |
| Aggressive HFT, corrected | **1.15** |
| Attention Factors stat arb (best in corpus) | **2.3** |
| Realistic S&P 500 pairs trading | 1.0–1.2 |
| Momentum Transformer, equities | 1.12 |
| Options wheel with Bayesian nets | 1.08 |
| Experienced City of London traders | 1.02 |
| Medallion | The one genuine outlier — capped at $10bn and closed |

Jane Street's 68% operating margin is not a Sharpe-8 strategy. It is a Sharpe ~1.5
strategy run with enormous capital, leverage, and 35% of US retail order flow.
**The gap was never risk-adjusted skill. It is capital, leverage and flow.**

Menkveld's 9.35 proves the point: it derives from €2.052M of maximum capital committed
per stock. A capital-efficiency ratio on a tiny base, not a scalable return engine.

### 6.2 Every optimistic result is a backtest; every live test disappoints

Five live tests in the corpus, five deflationary results:

1. Binance maker experiment — all imbalance strategies negative
2. HKU live uncontaminated benchmark — most LLM agents traded badly
3. Ackert et al. lab experiment — AI wins by taking human wealth, no efficiency gain
4. CNMV ten-month regulator study — human factor required
5. Izydorczyk, ex-Coatue — no bot shows a lasting edge

This asymmetry is the single most reliable finding across 117 searches.

### 6.3 Validation discipline is the binding constraint, never architecture

Factor-zoo regularization, split-sample persistence, sealed holdouts, temporal integrity,
counterfactual robustness, pre-training contamination. Across six years and three research
communities, the constraint is always whether you can tell if the edge is real.

### 6.4 Two unresolved contradictions worth knowing you're inheriting

- **Complexity.** The factor-zoo paper says regularize hard against many signals. Kelly
  says complexity is a *virtue* and depth helps to ~100 layers. Both are rigorous,
  top-venue, and give opposite instructions.
- **Decay mechanism.** Alpha decay is real in the data (NBER w35273). But the *explanation*
  everyone reaches for — convergence from shared training data — collides with that same
  paper's finding that AI funds show **lower** comovement than non-AI peers. Treat the
  18-month half-life as a model output, not a measurement.

### 6.5 The structural advantages a small operation actually has

1. **Smallness is documented edge.** Small hedge funds beat large by **3.65%/year**
   (Teo). Medallion caps at $10bn *deliberately*. Strategies too small to absorb $100M are
   invisible to everyone in this dossier.
2. **Distinctiveness pays ~3.5%/year** (SDI, *RFS* 2012).
3. **No mandate, no redemptions, no benchmark.** You can hold cash for six months. No fund
   in this corpus can.

### 6.6 What kills it, ranked

1. **Costs.** >4 → 2.3 for stat arb. ~2 → 1.0–1.2 for pairs. Positive → **negative** for
   market making.
2. **Overfitting.** The most repeated finding in the corpus.
3. **Decay.** ~18-month half-life on anything published.
4. **Execution.** 45% of aggressive HFT revenue comes from adversely selecting other HFTs.
   Without colocation you are not doing that — you are the one it is done to.

### 6.7 The verdict

**Decent margins: plausible. High margins: no.**

- P(a real net edge after costs, after a year of honest testing): **30–40%**
- If it works: **Sharpe 0.8–1.3**, mid-single to mid-teens percent annually on deployed
  capital
- P(Jane Street-style margins): **≈ 0**
- Most likely failure mode: **you believe a backtest**

---

## 7. What I want to build

This is the part that goes beyond summarizing. Given everything above, here is what I
think is worth building, in order, and what I would refuse to build.

### Phase 0 — The adversarial validator, before any strategy

**Rationale:** *Profit Mirage* and *The Alpha Illusion* both conclude that the thing that
kills these systems is believing a backtest, and that the specific mechanism for
LLM-touched components is pre-training contamination. The repo already has the right
instinct — a sealed holdout and a count of your looks. I want to finish it.

**Build:**

- Extend `crucible` with the six structural validity tests *The Alpha Illusion* specifies:
  temporal integrity, real-world frictions, counterfactual robustness, predictive
  calibration, numerical execution, multi-agent disaggregation.
- Add FactFin-style **counterfactual perturbation** for any LLM-touched component — perturb
  the inputs and confirm the output changes for causal reasons rather than recalled ones.
- Make the look-counter a **hard gate**, not a log line. N looks at the holdout, then it is
  burned and a new one must be cut.
- Make the cost model **mandatory and pessimistic by default**. No strategy reports a gross
  number anywhere in the system.

**Why first:** every phase below is worthless without it, and it is the only component
whose value does not decay.

### Phase 1 — The hedge-ratio experiment

**Rationale:** the highest evidence-to-effort ratio in the entire corpus. One variable,
a published result, a cheap test.

**Build:** PCA + OPTICS pair formation on S&P 500 constituents with cointegration
screening. Implement all six estimators — OLS, TLS, Johansen, Box-Tiao, Minimum Half-Life,
Minimum ADF. Hold entry/exit thresholds and the cost model **fixed**. Re-fit walk-forward.
Compare net Sharpe **and turnover**.

**The specific trap to check:** Box-Tiao maximizes predictability by reducing
autocorrelation, which mechanically favours slower spreads and therefore lower turnover.
Its apparent win may be a cost artifact rather than a signal improvement. Minimum Half-Life
is noisy on short windows and can latch onto an in-sample artifact. If neither beats OLS
walk-forward net of costs, the Aalto result was a full-sample fit and OLS's stability was
the real edge — which is a genuine finding and worth knowing.

### Phase 2 — The boring robust one

**Build:** Faber-style 10-month SMA trend overlay across diversified asset classes — US
equity, global equity, bonds, REITs, commodities.

**Rationale:** decades of out-of-sample robustness, almost no infrastructure, and it
doubles as the end-to-end plumbing test. It is also the strategy in this dossier most
likely to still work in five years, precisely because it is not clever.

### Phase 3 — Risk layer, not alpha

**Build:** a skew-conditioned exposure throttle wired into `risk_manager` — using the
**volatility × skewness interaction** from Bianchi et al., not skewness alone.

**Test:** does it improve drawdown on Phases 1–2 without materially cutting return? It is
a multiplier on exposure, never a source of signal.

### Phase 4 — The village as research engine, not as trader

**Rationale:** this is the biggest change from how these systems are usually built, and
the evidence for it is overwhelming. AI-Trader: general intelligence does not translate to
trading capability. Homo Silicus: persona prompts lose to quantitative optimization, so a
"village of characters" produces no real behavioural diversity. Dou/Goldstein/Ji: a
population of similarly-trained agents learns to collude without agreeing to. AQuA: what
*does* work is a self-improving research loop with a sealed sandbox preventing adaptive
overfitting.

**Build:**

- The village generates and screens **hypotheses**. The deterministic layer trades. An LLM
  never produces an order.
- Architecture pattern that OQL, the Options Wheel paper and AgenticAITA independently
  converged on: **LLM proposes structure → deterministic engine validates and executes →
  non-overridable gate between them.** `heart/` and `kill_switch.py` are that gate.
- Typed contracts between agents (AgenticAITA), and **anomaly-gated inference** so
  expensive LLM calls fire only on statistically unusual conditions.
- Behavioural diversity comes from **different objective functions, constraints and
  information sets** — never from different character prompts.
- Instrument for the collusion failure mode: watch for correlated positions across
  supposedly independent agents, and for profits that rise as agent diversity falls.

### What I explicitly do not want to build

- **Anything latency-sensitive.** 45% of aggressive HFT revenue comes from eating other
  HFTs. Without colocation you are the meal.
- **Market making.** The one live experiment in the corpus returned uniformly negative.
- **A persona-driven agent village as an alpha source.** Homo Silicus settles this.
- **Anything that combines all 70 signals.** Those results were each measured in isolation
  in a specific market, period and cost regime. They do not compose; their failure modes do.
- **Crypto momentum validated on a nine-month bull run.**

### Kill criteria

Matching the repo's existing philosophy — cheap to reach either answer, impossible to fudge:

- Phase 1 fails to beat OLS walk-forward net of costs → drop it, record the finding.
- Holdout looks exhausted with nothing clearing costs → **stop.** The answer is no, and
  that is a valid result worth the money it cost to get.
- Any strategy whose edge depends on a number this dossier flags as unverified → does not
  get built.

### What success looks like

Sharpe 0.8–1.3 on small capital, mid-single to mid-teens percent annually, positive most
years, drawdown under 15%. Not 60% margins. That target is what the professionals actually
achieve on a risk-adjusted basis, and the one fund in history that beat it caps itself at
$10bn and will not take outside money.

### Open questions I would want answered before Phase 4

1. Is the Options Wheel paper's scenario retrieval strictly walk-forward? It decides
   whether that architecture is trustworthy.
2. Does the ORCA clustering result survive a full bull/bear cycle, or is the edge
   regime-specific?
3. Which side of the Kelly-vs-factor-zoo complexity disagreement does our own data favour?
   That is answerable empirically on our own holdout, and it determines how many signals
   the system is allowed to carry.

---

*Compiled from four research documents and 117 live source checks. Every identifier here
was resolved individually; where a citation was wrong — or where my own first-pass verdict
was wrong — the correction is stated inline rather than silently fixed. Nothing in section
7 should be started before section 6.7 is understood.*
