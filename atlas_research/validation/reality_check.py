"""Did the search find something, or did it just search a lot?

Four tests, all run over every genome the GA evaluated, not just the winner:

* **White's Reality Check** (2000): is the best genome's mean daily return
  over SPY larger than the best of k genomes would show by luck alone?
  Stationary bootstrap (Politis & Romano), mean block 10 days.
* **Hansen's SPA** (2005): the same question, studentized, and not dragged
  down by the many clearly bad genomes (the consistent "c" version).
* **Probability of Backtest Overfitting** (Bailey, Borwein, Lopez de Prado
  & Zhu 2015), by combinatorially symmetric cross-validation: split the
  train days into 16 blocks, pick the best genome on half of them, and see
  where it ranks on the other half. PBO is how often the in-sample winner
  lands in the bottom half out of sample.
* **Deflated Sharpe Ratio** (Bailey & Lopez de Prado 2014): the probability
  that the winner's Sharpe beats zero after accounting for how many trials
  were run and for fat tails.

Harvey, Liu & Zhu's point stands over all of this: with hundreds of
genomes tried, a t-stat of 2 means little. The trial counts go in the report.
"""
from __future__ import annotations

import itertools
import math
import random

import numpy as np
from scipy import stats as st


def _stationary_bootstrap_idx(n: int, block: float, rng: np.random.Generator) -> np.ndarray:
    idx = np.empty(n, dtype=np.int64)
    p = 1.0 / block
    idx[0] = rng.integers(n)
    jumps = rng.random(n) < p
    starts = rng.integers(n, size=n)
    for t in range(1, n):
        idx[t] = starts[t] if jumps[t] else (idx[t - 1] + 1) % n
    return idx


def reality_check_and_spa(models: np.ndarray, bench: np.ndarray, B: int = 1000,
                          block: float = 10.0, seed: int = 1) -> dict:
    """models: [k, n] daily returns; bench: [n]. Loss differential d = model - bench."""
    d = models.astype(float) - bench[None, :].astype(float)
    k, n = d.shape
    dbar = d.mean(axis=1)
    rng = np.random.default_rng(seed)
    # long-run variance of each d via the bootstrap itself
    boot_means = np.empty((B, k))
    for b in range(B):
        ix = _stationary_bootstrap_idx(n, block, rng)
        boot_means[b] = d[:, ix].mean(axis=1)
    omega = np.sqrt(n) * boot_means.std(axis=0, ddof=1)
    omega = np.where(omega > 1e-12, omega, 1e-12)

    v_rc = math.sqrt(n) * dbar.max()
    v_rc_star = (math.sqrt(n) * (boot_means - dbar[None, :])).max(axis=1)
    p_rc = float((v_rc_star >= v_rc).mean())

    t_spa = max(0.0, (math.sqrt(n) * dbar / omega).max())
    thresh = -np.sqrt(2 * math.log(math.log(n))) * omega / math.sqrt(n)
    mu_c = np.where(dbar >= thresh, dbar, 0.0)
    z = math.sqrt(n) * (boot_means - mu_c[None, :]) / omega[None, :]
    t_star = np.maximum(z.max(axis=1), 0.0)
    p_spa = float((t_star >= t_spa).mean())
    best = int(dbar.argmax())
    return {"models": k, "days": n, "best_index": best,
            "best_mean_daily_excess_bp": float(dbar[best] * 1e4),
            "white_rc_p": p_rc, "hansen_spa_p": p_spa, "bootstrap_draws": B,
            "mean_block_days": block}


def _sharpe(x: np.ndarray, axis=-1) -> np.ndarray:
    sd = x.std(axis=axis, ddof=1)
    return np.where(sd > 1e-12, x.mean(axis=axis) / np.where(sd > 1e-12, sd, 1), 0.0)


def pbo_cscv(excess: np.ndarray, S: int = 16, max_combos: int = 2000, seed: int = 3) -> dict:
    """excess: [k, n] daily returns over cash. Ranks genomes by Sharpe."""
    k, n = excess.shape
    blocks = np.array_split(np.arange(n), S)
    combos = list(itertools.combinations(range(S), S // 2))
    random.Random(seed).shuffle(combos)
    combos = combos[:max_combos]
    logits, degr = [], []
    for c in combos:
        is_idx = np.concatenate([blocks[i] for i in c])
        oos_idx = np.concatenate([blocks[i] for i in range(S) if i not in c])
        sr_is = _sharpe(excess[:, is_idx])
        sr_oos = _sharpe(excess[:, oos_idx])
        best = int(sr_is.argmax())
        rank = (st.rankdata(sr_oos)[best]) / (k + 1)
        logits.append(math.log(rank / (1 - rank)))
        degr.append((sr_is[best], sr_oos[best]))
    logits = np.array(logits)
    d = np.array(degr) * math.sqrt(252)
    return {"pbo": float((logits <= 0).mean()), "combinations": len(combos), "blocks": S,
            "median_is_sharpe_of_winner": float(np.median(d[:, 0])),
            "median_oos_sharpe_of_winner": float(np.median(d[:, 1]))}


def deflated_sharpe(selected_excess: np.ndarray, all_sharpes: np.ndarray) -> dict:
    """Probability the selected Sharpe is above zero after N trials (daily units)."""
    x = selected_excess
    n = len(x)
    sr = float(x.mean() / x.std(ddof=1))
    N = len(all_sharpes)
    var_sr = float(np.var(all_sharpes, ddof=1))
    emc = 0.5772156649
    sr0 = math.sqrt(var_sr) * ((1 - emc) * st.norm.ppf(1 - 1 / N) + emc * st.norm.ppf(1 - 1 / (N * math.e)))
    g3 = float(st.skew(x))
    g4 = float(st.kurtosis(x, fisher=False))
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    dsr = float(st.norm.cdf((sr - sr0) * math.sqrt(n - 1) / denom))
    return {"trials": N, "selected_sharpe_ann": sr * math.sqrt(252),
            "expected_max_sharpe_from_luck_ann": sr0 * math.sqrt(252),
            "deflated_sharpe_prob": dsr}
