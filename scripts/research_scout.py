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
        "row": "dl > dt",
        "why": "q-fin.TR is trading and market microstructure. Papers state a "
               "rule and its test, which is the rarest property in this corpus.",
    },
    "arxiv_stat_ml": {
        "url": "https://arxiv.org/list/q-fin.ST/recent",
        "row": "dl > dt",
        "why": "q-fin.ST — statistical finance. Same reason.",
    },
}

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


def scout(name: str, spec: dict, limit: int = 25) -> list:
    """Open the page in a real browser and read what is listed. Never evaluates."""
    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright

    found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(spec["url"], timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            time.sleep(2)
            soup = BeautifulSoup(page.content(), "lxml")

            # arXiv listing pages pair <dt> (the id/links) with <dd> (title,
            # authors). Walk the pairs rather than the rows, so a title is never
            # attached to the wrong identifier.
            for dt in soup.select(spec["row"])[:limit]:
                dd = dt.find_next_sibling("dd")
                if dd is None:
                    continue
                link = dt.select_one('a[href*="/abs/"]')
                if link is None:
                    continue
                href = link.get("href") or ""
                url = href if href.startswith("http") else f"https://arxiv.org{href}"
                title_el = dd.select_one(".list-title")
                title = (title_el.get_text(strip=True).replace("Title:", "").strip()
                         if title_el else "")
                subj_el = dd.select_one(".list-subjects")
                subjects = (subj_el.get_text(strip=True).replace("Subjects:", "").strip()
                            if subj_el else "")
                if not title:
                    continue
                found.append({
                    "id": _digest(url),
                    "source": name,
                    "url": url,
                    "title": title,
                    "subjects": subjects,
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
