"""The village reads the literature — with a browser, and without believing it.

    python scripts/research_scout.py --list            # approved sources
    python scripts/research_scout.py --source arxiv_qfin
    python scripts/research_scout.py                   # every approved source

Finds candidate ideas and writes them to `data/research/candidates.jsonl`. It
does not test anything, does not write a strategy file, and cannot reach the
ledger, the gate or a firm.

**Why a browser and not an API.** Half the places worth reading have no usable
API, a paid one, or one that omits what you actually want. A real Chromium sees
the page anybody else sees. `politician_bot/scraper.py` in the sibling repo has
worked this way against Capitol Trades for months, and this borrows its shape —
warm up on the homepage so a session exists, then fetch, then parse with
BeautifulSoup — rather than inventing a second approach.

**Discovery is autonomous; adding a source is not.** `src/agents/scout.py` set
this rule for the product side and it is the right one here: "Discovery is the
one part of the loop that costs nothing and risks nothing, so it is fully
autonomous... Adding a *new* platform is a human-approved action." So SOURCES
below is an allowlist. A source not in it cannot be scouted by passing a URL,
because that is how a scraper ends up somewhere nobody agreed to.

**It will not write a strategy file, and that is the point.** The tempting next
step is to have a model read a paper and emit something the court can judge.
That model would be *generating* a strategy, not finding one, and the court
would then be ruling on an interpretation while the row said "from arXiv". This
records the claim and its provenance. Turning a claim into a testable rule is a
reviewed step with somebody's name on it.

**Every candidate counts, including the ones that go nowhere.** Reading N ideas
and keeping the ones that backtest well is how a search manufactures a winner
out of noise — this project has measured that three times on its own data
(3,016 combos whose best sat inside its own shuffled-search noise at p=0.167; a
four-way stack that tied a random-name control; 2,384 combos over 20.8 years
where the real search produced a *worse* winner than shuffled ones). So the
count is written down here, at discovery, before anybody knows which looked
good. `data/research/candidates.jsonl` is the denominator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "candidates.jsonl"

#: The allowlist. Each entry: a landing page, a CSS selector for result rows,
#: and a short note on why it is here. Adding one is a human action.
SOURCES = {
    "arxiv_qfin": {
        "url": "https://arxiv.org/list/q-fin.TR/recent",
        "parser": "arxiv",
        "why": "q-fin.TR is trading and market microstructure. Papers state a "
               "rule and its test, which is the rarest property in this corpus.",
    },
    "arxiv_stat": {
        "url": "https://arxiv.org/list/q-fin.ST/recent",
        "parser": "arxiv",
        "why": "q-fin.ST — statistical finance. Same reason.",
    },
    "arxiv_portfolio": {
        "url": "https://arxiv.org/list/q-fin.PM/recent",
        "parser": "arxiv",
        "why": "q-fin.PM — portfolio management. Where sizing and risk rules live, "
               "which is the half of a strategy that usually goes unspecified.",
    },
    "github_strategies": {
        "url": "https://github.com/search?q=trading+strategy+backtest+language%3APython"
               "&type=repositories&s=updated&o=desc",
        "parser": "github",
        "why": "Public repositories, ranked by recent activity. Code is the one "
               "medium where a claim cannot hide behind prose — either the rule is "
               "in the file or it is not.",
    },
    "hn_trading": {
        "url": "https://hn.algolia.com/api/v1/search_by_date?query=trading%20strategy"
               "&tags=story&hitsPerPage=25",
        "parser": "hn",
        "why": "Hacker News, newest first. Keyless and public. Comment threads here "
               "are unusually good at killing a bad claim quickly, which is worth "
               "more than the submissions.",
    },
}

#: **Two sources were tried and removed: Reddit and SSRN both block automated
#: access.** Not rate-limit — an outright "Content Blocked" page. Getting past
#: that means defeating bot detection, which is not something this will do, so
#: they are gone rather than quietly broken. If you want r/algotrading in here,
#: the honest route is Reddit's own API with an account, which is a decision to
#: take deliberately and not a scraper to sneak in.
#:
#: What is left needs no login and no key. arXiv and GitHub render server-side,
#: so one page load is the whole result; Hacker News is a JS app, so this reads
#: its public Algolia endpoint instead — still just fetching what any reader can
#: see. One page per source per run is politer than a human browsing.

TIMEOUT_MS = 25000


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _seen() -> set:
    if not OUT.exists():
        return set()
    out = set()
    for line in OUT.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.add(json.loads(line).get("id"))
        except ValueError:
            continue
    return out


def _parse_arxiv(soup, limit):
    """arXiv listing pages pair <dt> (ids) with <dd> (title, subjects)."""
    out = []
    for dt in soup.select("dl > dt")[:limit]:
        dd = dt.find_next_sibling("dd")
        link = dt.select_one('a[href*="/abs/"]')
        if dd is None or link is None:
            continue
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"https://arxiv.org{href}"
        title_el = dd.select_one(".list-title")
        title = (title_el.get_text(strip=True).replace("Title:", "").strip()
                 if title_el else "")
        subj_el = dd.select_one(".list-subjects")
        extra = (subj_el.get_text(strip=True).replace("Subjects:", "").strip()
                 if subj_el else "")
        if title:
            out.append((url, title, extra))
    return out


def _parse_github(soup, limit):
    """Repository search results. The heading link is owner/name."""
    out = []
    seen = set()
    for a in soup.select('a[href]'):
        href = a.get("href") or ""
        parts = [p for p in href.split("?")[0].strip("/").split("/") if p]
        # owner/repo and nothing deeper — skip /issues, /tree, /stargazers.
        if len(parts) != 2 or href.startswith("http"):
            continue
        if parts[0] in {"search", "topics", "collections", "sponsors", "features",
                        "orgs", "settings", "login", "signup", "about", "pricing"}:
            continue
        slug = f"{parts[0]}/{parts[1]}"
        if slug in seen:
            continue
        text = a.get_text(strip=True)
        if not text or "/" not in text:
            continue
        seen.add(slug)
        out.append((f"https://github.com/{slug}", slug, "repository"))
        if len(out) >= limit:
            break
    return out


def _parse_hn(soup, limit):
    """Hacker News via its public Algolia endpoint — the body is JSON, not HTML."""
    try:
        payload = json.loads(soup.get_text())
    except ValueError:
        return []
    out = []
    for hit in (payload.get("hits") or [])[:limit]:
        url = hit.get("url") or (
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            if hit.get("objectID") else "")
        title = hit.get("title") or hit.get("story_title") or ""
        if url and title:
            out.append((url, title, f"{hit.get('points') or 0} points, "
                                    f"{hit.get('num_comments') or 0} comments"))
    return out


PARSERS = {"arxiv": _parse_arxiv, "github": _parse_github, "hn": _parse_hn}


def scout(name: str, spec: dict, limit: int = 25) -> list:
    """Open the page in a real browser and read what is listed. Never evaluates."""
    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright

    parse = PARSERS[spec["parser"]]
    found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(spec["url"], timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            time.sleep(2.5)
            soup = BeautifulSoup(page.content(), "lxml")
            for url, title, extra in parse(soup, limit):
                found.append({
                    "id": _digest(url),
                    "source": name,
                    "url": url,
                    "title": title[:300],
                    "subjects": extra[:200],
                    "found_at": datetime.now(timezone.utc).isoformat(),
                    # Deliberately absent: any judgement. No score, no
                    # "promising", no rank. Those are the reviewed step.
                    "status": "unreviewed",
                })
        finally:
            browser.close()
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", action="append", choices=sorted(SOURCES))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    if args.list:
        for name, spec in sorted(SOURCES.items()):
            print(f"  {name:<16} {spec['url']}")
            print(f"  {'':<16} {spec['why']}")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen = _seen()
    total_new = 0

    for name in (args.source or sorted(SOURCES)):
        try:
            rows = scout(name, SOURCES[name], args.limit)
        except Exception as exc:  # noqa: BLE001 - one dead source is not a dead run
            print(f"  {name:<16} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        new = [r for r in rows if r["id"] not in seen]
        with OUT.open("a") as fh:
            for r in new:
                fh.write(json.dumps(r) + "\n")
                seen.add(r["id"])
        total_new += len(new)
        print(f"  {name:<16} {len(rows):>3} listed, {len(new):>3} new")

    print(f"\n{total_new} new candidate(s) -> {OUT.relative_to(REPO)}")
    print(f"denominator so far: {len(seen)} candidate(s) ever seen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
