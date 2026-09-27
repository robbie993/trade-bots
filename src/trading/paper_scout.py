"""The paper scout: new research on trading, markets and AI agents, from arXiv
and OpenAlex, every few hours.

What the operator asked for, in their words: "a bot looking for research papers
like these, or just anything on trading, news, AI". Two free, keyless sources:

* **arXiv** — preprints, where most AI-agent and quant work appears first:
  trading and microstructure (q-fin.TR), portfolio management (q-fin.PM),
  statistical finance (q-fin.ST), and AI trading agents across cs.AI / cs.LG.
* **OpenAlex** — an open index of ~250M works including journals and SSRN-type
  working papers, searched within economics and finance for hedge-fund
  strategies, reverse-engineering strategies, microstructure and crypto markets.

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
OPENALEX = "https://api.openalex.org/works?filter={f}&per-page={n}&sort=publication_date:desc"
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
)

#: Field 20 is Economics, Econometrics and Finance in OpenAlex's topic tree.
OPENALEX_SEARCHES = (
    ("hedge fund strategies", "hedge fund strategy"),
    ("reverse-engineering strategies", "reverse engineering trading strategy"),
    ("momentum and reversal", "momentum reversal returns"),
    ("crypto markets", "cryptocurrency trading"),
    ("market microstructure", "market microstructure liquidity"),
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
        })
    return out


class PaperScout:
    name = "paper_scout"

    def __init__(self, db, get_text: Optional[Callable] = None, clock=time.time):
        self.db = db
        self._clock = clock
        self._last = 0.0
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
        for label, q in ARXIV_QUERIES:
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
                    self._get(OPENALEX.format(f=quote(f, safe=':,.'), n=6))))))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"OpenAlex ({label}): {str(exc)[:80]}")
        for label, papers in batches:
            for p in papers:
                if not p["title"]:
                    continue
                intel.upsert(self.db, "papers", p["key"][:255],
                             title=f"{p['title']} ({p['venue']}, {p['published'][:10]})",
                             url=p["url"],
                             detail={"query": label, "authors": p["authors"],
                                     "venue": p["venue"], "published": p["published"],
                                     "abstract": p["abstract"][:1200],
                                     "topics": tag(f"{p['title']} {p['abstract']}")})
                found += 1
        notes = [f"paper scout: {found} paper(s) from arXiv and OpenAlex"] if found else []
        notes += [f"paper scout source FAILING — {f}" for f in failed[:3]]
        return notes


__all__ = ["EVERY_S", "PaperScout", "parse_arxiv", "parse_openalex"]
