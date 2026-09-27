"""A second mind for the firms: DuckDuckGo's free AI chat, no account.

    python scripts/answer_duck.py --dry-run
    python scripts/answer_duck.py --to-railway      # the scheduled run

Claude (answer_questions.py) is the first mind. This asks duck.ai the same
questions through the village browser: free, no login, no key, chats not
retained by DuckDuckGo. Each answer is recorded with the model duck.ai reports
(its free model identifies itself on the page), so a firm hearing two opinions
knows whose each is, and a proposal from this mind goes through the same test
and council as any other.

One question per fresh chat, a handful per run: the free plan has limits and a
chat that carries one firm's context into another's question would mix them.
If duck.ai asks for anything more than its one-time terms, the run stops.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from scripts.insta_watch import browser  # noqa: E402
from src.trading import ask  # noqa: E402

ANSWERED_BY = "duck.ai (DuckDuckGo free AI chat)"
PER_RUN = 6
WAIT_S = 120


def _answer_text(body: str, question: str) -> tuple:
    """(answer, model) from the page text after one question."""
    tail = body[body.rfind(question[-60:]):] if question[-60:] in body else body
    m = re.search(r"Duck\.ai said\s*\n\s*([^\n]+)\n(.*?)(\n\s*2nd opinion|\n\s*Duck\.ai works best|$)",
                  tail, re.S)
    if not m:
        return "", ""
    return m.group(2).strip(), m.group(1).strip()


def ask_duck(page, prompt: str) -> tuple:
    page.goto("https://duck.ai/", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    box = page.locator("textarea").first
    box.click()
    box.fill(prompt)
    box.press("Enter")
    page.wait_for_timeout(2500)
    cont = page.get_by_role("button", name="Continue")
    if cont.count():
        cont.first.click()                  # the one-time terms, nothing else
    last, text, model = "", "", ""
    for _ in range(WAIT_S // 2):
        page.wait_for_timeout(2000)
        body = page.inner_text("body")
        text, model = _answer_text(body, prompt)
        if text and body == last:
            break                           # stopped streaming
        last = body
    return text, model


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-railway", action="store_true")
    ap.add_argument("--database-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    from scripts.fleet_sync import railway_database_url
    from src.db.connection import Database

    db = Database.from_url(args.database_url or railway_database_url())
    db.init_schema()
    waiting = ask.open_questions(db, answered_by=ANSWERED_BY, limit=PER_RUN)
    print(f"{len(waiting)} question(s) duck.ai has not answered yet")
    if args.dry_run or not waiting:
        return 0
    failures = 0
    with sync_playwright() as p:
        page = browser(p).contexts[0].new_page()
        try:
            for q in waiting:
                print(f"\n#{q['id']} {q['firm_key']} [{q['topic']}]: {q['question'][:90]}")
                try:
                    text, model = ask_duck(page, ask.prompt_for(q))
                except Exception as exc:  # noqa: BLE001 - one question is not the run
                    failures += 1
                    print(f"  FAILED: {str(exc)[:160]}", file=sys.stderr)
                    continue
                if not text:
                    failures += 1
                    print("  no answer read from the page", file=sys.stderr)
                    continue
                ask.answer(db, q["id"], ANSWERED_BY, text, model=model)
                print(f"  answered by {model}: {text[:180]}")
        finally:
            page.close()
    db.close()
    return 1 if failures and failures == len(waiting) else 0


if __name__ == "__main__":
    raise SystemExit(main())
