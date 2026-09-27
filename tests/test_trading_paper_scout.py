"""The paper scout parses what arXiv and OpenAlex return and keeps it as intel.
The two payloads are trimmed from real responses of 2026-09-27."""

from src.trading import intel
from src.trading.paper_scout import PaperScout, parse_arxiv, parse_openalex

ARXIV = """<feed><entry><id>http://arxiv.org/abs/2609.01234v1</id>
<published>2026-09-25T10:00:00Z</published>
<title>EvolveTrade: Experience-Driven Policy Refinement for Self-Evolving LLM Trading Agents</title>
<summary>We propose a trading agent that refines its policy from experience.</summary>
<author><name>A. Author</name></author></entry></feed>"""

OPENALEX = {"results": [{"id": "https://openalex.org/W1", "title": "HedgeAgents: A Balanced-aware Multi-agent Financial Trading System",
                         "doi": "https://doi.org/10.1/x", "publication_date": "2026-05-01",
                         "abstract_inverted_index": {"Multi-agent": [0], "trading": [1]},
                         "authorships": [{"author": {"display_name": "B. Author"}}],
                         "primary_location": {"source": {"display_name": "A Journal"}}}]}


def test_arxiv_and_openalex_are_parsed():
    (a,) = parse_arxiv(ARXIV)
    assert a["key"] == "arxiv:2609.01234v1" and "EvolveTrade" in a["title"]
    (o,) = parse_openalex(OPENALEX)
    assert o["abstract"] == "Multi-agent trading" and o["venue"] == "A Journal"


def test_papers_are_kept_with_topics(db, monkeypatch):
    import json

    def get(url):
        return ARXIV if "arxiv" in url else json.dumps(OPENALEX)

    import src.trading.paper_scout as ps
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    PaperScout(db, get_text=get, clock=lambda: 10**6).run()
    rows = intel.recent(db, "papers", limit=50)
    assert any("EvolveTrade" in r["title"] for r in rows)
    assert any("ai_agent" in (r["detail"].get("topics") or []) for r in rows)
