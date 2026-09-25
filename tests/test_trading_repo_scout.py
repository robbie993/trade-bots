"""The repo scout records what it finds and never fetches the code itself."""

from __future__ import annotations

from src.trading import intel
from src.trading.repo_scout import EVERY_S, RepoScout


def _fake(fail=()):
    calls = []

    def get(url, params, headers=None):
        calls.append((url, dict(params)))
        if any(f in url for f in fail):
            raise RuntimeError("HTTP 403 rate limited")
        if "github" in url:
            return {"items": [{"full_name": "someone/quant", "id": 1, "description": "a bot",
                               "html_url": "https://github.com/someone/quant",
                               "stargazers_count": 120, "language": "Python"}]}
        return [{"id": "org/finbert", "likes": 30, "pipeline_tag": "text-classification"}]

    get.calls = calls
    return get


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_finds_are_recorded_with_a_link_and_a_count(db):
    RepoScout(db, get_json=_fake(), clock=Clock()).run()
    gh = intel.recent(db, "github")
    assert gh[0]["url"] == "https://github.com/someone/quant" and float(gh[0]["score"]) == 120
    assert intel.recent(db, "huggingface_models")[0]["url"] == "https://huggingface.co/org/finbert"
    assert intel.recent(db, "huggingface_datasets")[0]["url"].startswith(
        "https://huggingface.co/datasets/")


def test_it_only_ever_asks_the_two_apis(db):
    """Never a clone, a raw file or an archive: the finds are malware-lure territory."""
    get = _fake()
    RepoScout(db, get_json=get, clock=Clock()).run()
    hosts = {url.split("/")[2] for url, _ in get.calls}
    assert hosts == {"api.github.com", "huggingface.co"}
    assert all("/api/" in url or "/search/" in url for url, _ in get.calls)


def test_hourly_not_every_tick(db):
    clock, get = Clock(), _fake()
    scout = RepoScout(db, get_json=get, clock=clock)
    scout.run()
    n = len(get.calls)
    clock.t += 60
    assert scout.run() == [] and len(get.calls) == n
    clock.t += EVERY_S
    scout.run()
    assert len(get.calls) == 2 * n


def test_a_rate_limited_site_is_named_and_the_other_still_runs(db):
    notes = RepoScout(db, get_json=_fake(fail=("github",)), clock=Clock()).run()
    assert any("FAILING" in n and "github" in n for n in notes)
    assert intel.recent(db, "huggingface_models")


def test_the_scout_is_off_unless_switched_on(ecosystem):
    assert ecosystem.repo_scout is None
