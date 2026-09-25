"""The repo scout: what is new or moving on GitHub and Hugging Face.

Once an hour it asks both sites a handful of fixed questions — new
algorithmic-trading repositories, active trading frameworks, trending finance
models and datasets — and writes what it finds to `intel`. That is all.

**It never downloads, clones, installs or runs anything it finds.** Trending
"trading bot" repositories are a known malware lure: a fresh account, a
Codepen-style name, a few dozen bought stars, and an install step that empties a
wallet. One turned up on the first probe. So a find is a title, a link, a star
count and a description, and the panel says "never run" beside it. Turning a
find into something the village can test is a reviewed step with a person's
name on it, the same rule `scripts/research_scout.py` keeps for papers — and
the strategy court is where that step already goes.

No keys. GitHub's unauthenticated search allows ten queries a minute; this
makes three an hour.

Off unless `TRADE_REPO_SCOUT_ENABLED` is set.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import intel

GITHUB_SEARCH = "https://api.github.com/search/repositories"
HF_MODELS = "https://huggingface.co/api/models"
HF_DATASETS = "https://huggingface.co/api/datasets"
HEADERS = {"User-Agent": "village-repo-scout", "Accept": "application/json"}

#: Seconds between runs. The things it looks at change by the day, not the bar.
EVERY_S = 3600


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def github_queries() -> list:
    return [
        ("new: algorithmic trading", f"topic:algorithmic-trading created:>{_since(14)}"),
        ("new: trading bots", f"topic:trading-bot created:>{_since(14)}"),
        ("active: quant frameworks",
         f"quantitative trading pushed:>{_since(7)} stars:>200"),
    ]


HF_QUERIES = [
    (HF_MODELS, "huggingface_models", "finance"),
    (HF_MODELS, "huggingface_models", "trading"),
    (HF_MODELS, "huggingface_models", "time series forecasting"),
    (HF_DATASETS, "huggingface_datasets", "stock"),
    (HF_DATASETS, "huggingface_datasets", "crypto"),
]


class RepoScout:
    name = "repo_scout"

    def __init__(self, db, get_json: Optional[Callable] = None, clock=time.time):
        self.db = db
        self._clock = clock
        self._last = 0.0
        if get_json is None:
            from .data.feeds import _get_json

            def get_json(url, params, headers=None):
                return _get_json(url, params, headers=headers, timeout=8)
        self._get = get_json

    def run(self) -> list:
        now = self._clock()
        if now - self._last < EVERY_S:
            return []
        self._last = now
        found, failed = 0, []

        for label, q in github_queries():
            try:
                data = self._get(GITHUB_SEARCH, {"q": q, "sort": "stars", "order": "desc",
                                                 "per_page": 15}, headers=HEADERS)
            except Exception as exc:  # noqa: BLE001 - one site is not the scout
                failed.append(f"github ({label}): {str(exc)[:80]}")
                continue
            for repo in (data or {}).get("items", [])[:15]:
                intel.upsert(
                    self.db, "github", repo.get("full_name") or str(repo.get("id")),
                    title=f"{repo.get('full_name')} — {(repo.get('description') or '')[:120]}",
                    url=repo.get("html_url") or "",
                    score=repo.get("stargazers_count"),
                    detail={"query": label, "language": repo.get("language"),
                            "created": repo.get("created_at"), "pushed": repo.get("pushed_at"),
                            "topics": (repo.get("topics") or [])[:8]},
                )
                found += 1

        for url, source, term in HF_QUERIES:
            try:
                items = self._get(url, {"search": term, "sort": "trendingScore",
                                        "limit": 10}, headers=HEADERS)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{source} ({term}): {str(exc)[:80]}")
                continue
            kind = "datasets/" if "datasets" in url else ""
            for item in (items or [])[:10]:
                ident = item.get("id") or item.get("modelId")
                if not ident:
                    continue
                intel.upsert(
                    self.db, source, ident,
                    title=f"{ident} — {item.get('pipeline_tag') or term}",
                    url=f"https://huggingface.co/{kind}{ident}",
                    score=item.get("likes"),
                    detail={"query": term, "downloads": item.get("downloads"),
                            "tags": (item.get("tags") or [])[:8]},
                )
                found += 1

        notes = [f"repo scout: {found} find(s) on GitHub and Hugging Face"] if found else []
        notes += [f"repo scout source FAILING — {f}" for f in failed[:3]]
        return notes


__all__ = ["EVERY_S", "RepoScout", "github_queries"]
