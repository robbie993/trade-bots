"""Where the forward books get their data. Runs on a machine that can reach
Alpaca, SEC EDGAR and GitHub (Robbie's PC or Railway, not the cloud session).

Needs the village's Alpaca keys (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY,
read from the environment or the repo's .env; nothing here places an order) and SEC_USER_AGENT, which the
SEC asks for: "<name> <email>". Key values are never written anywhere.
"""
from __future__ import annotations

import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

DATA = "https://data.alpaca.markets"
TRADING = "https://paper-api.alpaca.markets"
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
NY = ZoneInfo("America/New_York")


def _session():
    import requests
    s = requests.Session()
    env = os.environ.get
    # the village's own names (src/trading/data/feeds.py), then trade-bot-2's
    k = env("ALPACA_API_KEY_ID") or env("APCA_API_KEY_ID") or env("ALPACA_API_KEY")
    sec = env("ALPACA_API_SECRET_KEY") or env("APCA_API_SECRET_KEY") or env("ALPACA_SECRET_KEY")
    if not k or not sec:
        raise RuntimeError("Alpaca keys are not set (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)")
    s.headers.update({"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec})
    return s


def _get(s, url, params=None, tries=5):
    for i in range(tries):
        r = s.get(url, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(2 + i * 2)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


class LiveFeed:
    def __init__(self, cache_dir: Path):
        self.s = _session()
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._snap = None

    # -- daily stock and ETF bars ------------------------------------------
    def bars(self, symbols, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Adjusted closes and dollar volume, dates x symbols, consolidated tape.

        The consolidated (SIP) feed, never IEX: IEX is ~2% of real volume and
        would break the dollar-volume filter (see trade-bot-2 fetch_prices.py).
        """
        closes, dvol = {}, {}
        syms = sorted(set(symbols))
        for i in range(0, len(syms), 100):
            chunk = syms[i:i + 100]
            token = None
            while True:
                p = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start.isoformat(),
                     "end": end.isoformat(), "limit": 10000, "adjustment": "all", "feed": "sip"}
                if token:
                    p["page_token"] = token
                j = _get(self.s, f"{DATA}/v2/stocks/bars", p)
                for sym, rows in (j.get("bars") or {}).items():
                    for b in rows:
                        d = b["t"][:10]
                        closes.setdefault(sym, {})[d] = b["c"]
                        dvol.setdefault(sym, {})[d] = b["c"] * b["v"]
                token = j.get("next_page_token")
                if not token:
                    break
        c = pd.DataFrame(closes).sort_index()
        c.index = pd.to_datetime(c.index)
        v = pd.DataFrame(dvol).reindex(c.index)
        return c, v

    # -- S&P 500 snapshot, saved once per quarter ---------------------------
    def has_universe(self, quarter: str) -> bool:
        return (self.cache / f"sp500_{quarter}.json").exists()

    def universe(self, quarter: str) -> tuple[list, bool]:
        """(tickers, late?) for 'YYYY-Qn'. A saved snapshot is never re-fetched."""
        f = self.cache / f"sp500_{quarter}.json"
        if f.exists():
            d = json.loads(f.read_text())
            return d["tickers"], d.get("late", False)
        import requests
        txt = requests.get(SP500_CSV, timeout=60).text
        tickers = sorted(pd.read_csv(io.StringIO(txt))["Symbol"].str.replace("-", ".").tolist())
        f.write_text(json.dumps({"quarter": quarter, "fetched": datetime.now(timezone.utc).isoformat(),
                                 "tickers": tickers}))
        return tickers, False

    # -- EDGAR earnings releases (8-K Item 2.02) ----------------------------
    def earnings_events(self, symbols, since: date) -> list:
        import requests
        ua = os.environ.get("SEC_USER_AGENT")
        if not ua:
            raise RuntimeError("SEC_USER_AGENT is not set (the SEC wants 'name email')")
        h = {"User-Agent": ua}
        cmap_f = self.cache / "sec_tickers.json"
        if not cmap_f.exists() or time.time() - cmap_f.stat().st_mtime > 7 * 86400:
            cmap_f.write_text(requests.get(SEC_TICKERS, headers=h, timeout=60).text)
        cik = {v["ticker"].upper().replace("-", "."): int(v["cik_str"])
               for v in json.loads(cmap_f.read_text()).values()}
        out = []
        for sym in symbols:
            if sym not in cik:
                continue
            time.sleep(0.12)          # SEC asks for at most 10 requests a second
            r = requests.get(SEC_SUBMISSIONS.format(cik=cik[sym]), headers=h, timeout=60)
            if r.status_code != 200:
                continue
            rec = r.json().get("filings", {}).get("recent", {})
            for form, fdate, acc, items in zip(rec.get("form", []), rec.get("filingDate", []),
                                               rec.get("acceptanceDateTime", []), rec.get("items", [])):
                if form != "8-K" or "2.02" not in (items or "") or fdate < since.isoformat():
                    continue
                accepted = datetime.fromisoformat(acc.replace("Z", "+00:00")).astimezone(NY)
                out.append({"symbol": sym, "filed": fdate,
                            "after_close": accepted.strftime("%H:%M") >= "16:00",
                            "accepted": accepted.isoformat()})
        return out

    # -- SPY options --------------------------------------------------------
    def _snapshots(self):
        if self._snap is None:
            today = date.today()
            snaps, token = {}, None
            while True:
                p = {"feed": "indicative", "type": "call", "limit": 1000,
                     "expiration_date_gte": (today + timedelta(days=25)).isoformat(),
                     "expiration_date_lte": (today + timedelta(days=130)).isoformat()}
                if token:
                    p["page_token"] = token
                j = _get(self.s, f"{DATA}/v1beta1/options/snapshots/SPY", p)
                snaps.update(j.get("snapshots") or {})
                token = j.get("next_page_token")
                if not token:
                    break
            self._snap = snaps
        return self._snap

    @staticmethod
    def _parse_occ(occ):
        # SPY261218C00650000 -> expiry 2026-12-18, strike 650.0
        body = occ[3:]
        return f"20{body[0:2]}-{body[2:4]}-{body[4:6]}", int(body[7:]) / 1000

    def option_chain(self, day: date) -> list:
        if day != date.today():
            return []        # a past day's chain cannot be reconstructed with greeks
        out = []
        for occ, s in self._snapshots().items():
            exp, k = self._parse_occ(occ)
            q, g = s.get("latestQuote") or {}, s.get("greeks") or {}
            out.append({"symbol": occ, "expiry": exp, "strike": k, "bid": q.get("bp"),
                        "ask": q.get("ap"), "delta": g.get("delta")})
        return out

    def option_quote(self, occ: str, day: date):
        if day == date.today() and occ in self._snapshots():
            s = self._snapshots()[occ]
            q, g, b = s.get("latestQuote") or {}, s.get("greeks") or {}, s.get("dailyBar") or {}
            return {"bid": q.get("bp"), "ask": q.get("ap"), "delta": g.get("delta"), "close": b.get("c")}
        j = _get(self.s, f"{DATA}/v1beta1/options/bars",
                 {"symbols": occ, "timeframe": "1Day", "start": day.isoformat(), "end": day.isoformat()})
        rows = (j.get("bars") or {}).get(occ) or []
        return {"close": rows[0]["c"]} if rows else None
