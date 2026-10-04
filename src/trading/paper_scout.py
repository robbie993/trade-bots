"""The paper scout: research on trading, markets and AI agents, from arXiv,
OpenAlex and Semantic Scholar, every few hours.

What the operator asked for, in their words: "a bot looking for research papers
like these, or just anything on trading, news, AI". Two free, keyless sources:

* **arXiv** — preprints, where most AI-agent and quant work appears first:
  trading and microstructure (q-fin.TR), portfolio management (q-fin.PM),
  statistical finance (q-fin.ST), and AI trading agents across cs.AI / cs.LG.
* **OpenAlex** — an open index of ~250M works including journals and SSRN-type
  working papers, searched within economics and finance for hedge-fund
  strategies, reverse-engineering strategies, microstructure and crypto markets.
* **The landmarks** — newest is not best. OpenAlex (every field, so computer
  science counts too) and Semantic Scholar are also asked for the most-cited
  work of the last five years on AI and machine-learning trading, so the papers
  everyone builds on are found, not only this week's preprints. Their citation
  count is kept as the find's score.

Each paper goes to `intel` (source `papers`) with its date, venue, authors,
abstract head and topic tags. Nothing is downloaded or run. The daily research
review (ask.py) hands them to the outside minds and asks what an operator's
chat app did unprompted for an SSRN report: is it peer-reviewed or a preprint,
does it show real out-of-sample numbers, would costs erase it, is it dated.

Off unless `TRADE_PAPER_SCOUT_ENABLED` is set.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

from . import intel
from .topics import tag

ARXIV = "http://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&sortOrder=descending&max_results={n}"
OPENALEX = "https://api.openalex.org/works?filter={f}&per-page={n}&sort={sort}"
S2 = ("https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit={n}"
      "&year={years}&fields=title,url,venue,year,publicationDate,abstract,authors,"
      "citationCount,externalIds")
HEADERS = {"User-Agent": "village-paper-scout", "Accept": "application/json"}

#: Hours between runs: papers arrive by the day, not the bar.
EVERY_S = 6 * 3600

ARXIV_QUERIES = (
    ("trading & microstructure", "cat:q-fin.TR"),
    ("portfolio management", "cat:q-fin.PM"),
    ("statistical finance", "cat:q-fin.ST"),
    ("AI trading agents", 'all:"trading agent" AND (cat:cs.AI OR cat:cs.LG OR cat:q-fin.TR)'),
    ("LLMs on markets", 'abs:"large language model" AND (abs:stock OR abs:crypto OR abs:trading)'),
    ("multi-agent markets", 'abs:"multi-agent" AND abs:trading'),
    ("computational finance", "cat:q-fin.CP"),
    ("general finance", "cat:q-fin.GN"),
    ("reinforcement learning trading", 'abs:"reinforcement learning" AND (abs:trading OR abs:portfolio)'),
    ("LLM financial agents", 'abs:"large language model" AND abs:agent AND abs:financial'),
    ("alpha and factor mining", '(abs:"alpha factor" OR abs:"factor mining" OR abs:"formulaic alpha")'),
    ("return prediction", 'abs:"return prediction" AND (abs:"deep learning" OR abs:"machine learning")'),
    ("market making", 'abs:"market making" AND (cat:q-fin.TR OR cat:cs.LG)'),
    ("crypto trading", '(abs:bitcoin OR abs:cryptocurrency) AND abs:trading'),
    ("quant trading strategies", '(abs:"quantitative trading" OR abs:"systematic trading" OR abs:"algorithmic trading")'),
    ("statistical arbitrage", '(abs:"statistical arbitrage" OR abs:"pairs trading" OR abs:"mean reversion")'),
    ("volatility and risk", '(cat:q-fin.RM OR abs:"volatility forecasting")'),
    ("options and pricing", "cat:q-fin.PR"),
    ("mathematical finance", "cat:q-fin.MF"),
    ("factor investing", '(abs:"factor investing" OR abs:"factor model") AND (cat:q-fin.PM OR cat:q-fin.ST)'),
    ("high-frequency and execution", '(abs:"high-frequency trading" OR abs:"optimal execution" OR abs:"order flow")'),
)

#: Field 20 is Economics, Econometrics and Finance in OpenAlex's topic tree.
OPENALEX_SEARCHES = (
    ("hedge fund strategies", "hedge fund strategy"),
    ("reverse-engineering strategies", "reverse engineering trading strategy"),
    ("momentum and reversal", "momentum reversal returns"),
    ("crypto markets", "cryptocurrency trading"),
    ("market microstructure", "market microstructure liquidity"),
)

#: The most-cited work of the last five years, every field (AI papers sit in
#: computer science, not finance). Asked of OpenAlex and Semantic Scholar.
LANDMARK_SEARCHES = (
    ("landmark: RL trading", "reinforcement learning trading"),
    ("landmark: LLM trading agents", "large language model trading agent"),
    ("landmark: deep learning returns", "deep learning stock return prediction"),
    ("landmark: multi-agent trading", "multi-agent trading system"),
    ("landmark: ML factor investing", "machine learning factor investing"),
    ("landmark: crypto strategies", "cryptocurrency trading strategy"),
    ("landmark: alpha mining", "alpha factor mining"),
    ("landmark: quant strategies", "quantitative trading strategy backtest"),
    ("landmark: statistical arbitrage", "statistical arbitrage pairs trading"),
    ("landmark: anomalies", "cross-section stock return anomalies"),
    ("landmark: trend following", "time series momentum trend following"),
    ("landmark: volatility", "volatility forecasting trading"),
)


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def parse_arxiv(xml: str) -> list:
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        def field(tag_):
            m = re.search(rf"<{tag_}[^>]*>(.*?)</{tag_}>", e, re.S)
            return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        ident = field("id")
        out.append({
            "key": "arxiv:" + ident.rsplit("/abs/", 1)[-1],
            "title": field("title"),
            "url": ident,
            "published": field("published"),
            "abstract": field("summary"),
            "authors": re.findall(r"<name>(.*?)</name>", e)[:6],
            "venue": "arXiv preprint",
        })
    return out


def parse_openalex(doc: dict) -> list:
    out = []
    for w in (doc or {}).get("results", []):
        inv = w.get("abstract_inverted_index") or {}
        words = sorted(((p, word) for word, ps in inv.items() for p in ps))
        src = ((w.get("primary_location") or {}).get("source") or {})
        out.append({
            "key": "openalex:" + str(w.get("id", "")).rsplit("/", 1)[-1],
            "title": w.get("title") or "",
            "url": w.get("doi") or w.get("id") or "",
            "published": w.get("publication_date") or "",
            "abstract": " ".join(word for _, word in words),
            "authors": [a.get("author", {}).get("display_name")
                        for a in (w.get("authorships") or [])][:6],
            "venue": src.get("display_name") or "unknown venue",
            "citations": w.get("cited_by_count"),
        })
    return out


def parse_s2(doc: dict) -> list:
    out = []
    for p in (doc or {}).get("data") or []:
        ext = p.get("externalIds") or {}
        key = ("arxiv:" + ext["ArXiv"]) if ext.get("ArXiv") else "s2:" + str(p.get("paperId", ""))
        out.append({
            "key": key,
            "title": p.get("title") or "",
            "url": p.get("url") or "",
            "published": p.get("publicationDate") or str(p.get("year") or ""),
            "abstract": p.get("abstract") or "",
            "authors": [a.get("name") for a in (p.get("authors") or [])][:6],
            "venue": p.get("venue") or "unknown venue",
            "citations": p.get("citationCount"),
        })
    return out


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class PaperScout:
    name = "paper_scout"

    def __init__(self, db, get_text: Optional[Callable] = None, clock=time.time):
        self.db = db
        self._clock = clock
        self._last = 0.0
        self._turn = 0             # which share of the queries this run asks
        if get_text is None:
            import urllib.request

            from .data.feeds import _ssl_context

            def get_text(url):
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=20,  # noqa: S310 - fixed hosts
                                            context=_ssl_context()) as r:
                    return r.read().decode("utf-8", "replace")
        self._get = get_text

    def run(self) -> list:
        now = self._clock()
        if now - self._last < EVERY_S:
            return []
        self._last = now
        found, failed = 0, []
        batches = []
        # The scout runs inside the tick, and arXiv wants 3 s between calls, so
        # each run asks half the arXiv questions and one landmark question, in
        # turn: every arXiv query every 12 hours, every landmark every 72.
        turn, self._turn = self._turn, self._turn + 1
        for label, q in ARXIV_QUERIES[turn % 2::2]:
            try:
                batches.append((label, parse_arxiv(self._get(ARXIV.format(q=quote(q), n=8)))))
            except Exception as exc:  # noqa: BLE001 - one query is not the scout
                failed.append(f"arXiv ({label}): {str(exc)[:80]}")
            time.sleep(3)          # arXiv asks for a pause between API calls
        for label, words in OPENALEX_SEARCHES:
            f = (f"title_and_abstract.search:{words},from_publication_date:{_since(120)},"
                 "primary_topic.field.id:20")
            try:
                batches.append((label, parse_openalex(json.loads(
                    self._get(OPENALEX.format(f=quote(f, safe=':,.'), n=6,
                                              sort="publication_date:desc"))))))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"OpenAlex ({label}): {str(exc)[:80]}")
        five_years = _since(5 * 365)
        for label, words in (LANDMARK_SEARCHES[turn % len(LANDMARK_SEARCHES)],):
            f = f"title_and_abstract.search:{words},from_publication_date:{five_years}"
            try:
                batches.append((label, parse_openalex(json.loads(
                    self._get(OPENALEX.format(f=quote(f, safe=':,.'), n=8,
                                              sort="cited_by_count:desc"))))))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"OpenAlex ({label}): {str(exc)[:80]}")
            try:
                batches.append((label, parse_s2(json.loads(self._get(S2.format(
                    q=quote(words), n=20, years=f"{five_years[:4]}-"))))))
            except Exception as exc:  # noqa: BLE001 - keyless Semantic Scholar rate-limits
                failed.append(f"Semantic Scholar ({label}): {str(exc)[:80]}")
        seen = set()
        for label, papers in batches:
            if label.startswith("landmark"):
                # Semantic Scholar's search is by relevance, not citations
                papers = sorted(papers, key=lambda p: -(p.get("citations") or 0))[:8]
            for p in papers:
                if not p["title"] or _norm(p["title"]) in seen:
                    continue
                seen.add(_norm(p["title"]))
                intel.upsert(self.db, "papers", p["key"][:255],
                             title=f"{p['title']} ({p['venue']}, {p['published'][:10]})",
                             url=p["url"], score=p.get("citations"),
                             detail={"query": label, "authors": p["authors"],
                                     "venue": p["venue"], "published": p["published"],
                                     "abstract": p["abstract"][:1200],
                                     "topics": tag(f"{p['title']} {p['abstract']}")})
                found += 1
        notes = ([f"paper scout: {found} paper(s) from arXiv, OpenAlex and Semantic Scholar"]
                 if found else [])
        notes += [f"paper scout source FAILING — {f}" for f in failed[:3]]
        return notes


__all__ = ["EVERY_S", "LANDMARK_SEARCHES", "PaperScout", "parse_arxiv", "parse_openalex",
           "parse_s2"]
