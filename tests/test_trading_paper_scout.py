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


S2 = {"data": [
    {"paperId": "p1", "title": "FinRL: Deep Reinforcement Learning for Trading", "url": "https://s2/p1",
     "venue": "ICAIF", "year": 2021, "publicationDate": "2021-11-03", "abstract": "A library.",
     "authors": [{"name": "C. Author"}], "citationCount": 900, "externalIds": {"ArXiv": "2111.09395"}},
    {"paperId": "p2", "title": "A small follow-up", "url": "https://s2/p2", "venue": "", "year": 2024,
     "abstract": None, "authors": [], "citationCount": 3, "externalIds": {}},
]}


def test_semantic_scholar_is_parsed():
    from src.trading.paper_scout import parse_s2
    a, b = parse_s2(S2)
    assert a["key"] == "arxiv:2111.09395" and a["citations"] == 900
    assert b["key"] == "s2:p2" and b["abstract"] == "" and b["venue"] == "unknown venue"


def test_landmarks_are_kept_with_citations_and_queries_rotate(db, monkeypatch):
    import json

    import src.trading.paper_scout as ps
    asked = []

    def get(url):
        asked.append(url)
        if "arxiv" in url:
            return ARXIV
        if "semanticscholar" in url:
            return json.dumps(S2)
        return json.dumps(OPENALEX)

    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    t = [10**6]
    scout = PaperScout(db, get_text=get, clock=lambda: t[0])
    scout.run()
    first = [u for u in asked if "arxiv" in u]
    assert len(first) == len(ps.ARXIV_QUERIES[0::2])
    assert any("cited_by_count" in u for u in asked) and any("semanticscholar" in u for u in asked)
    rows = {r["item_key"]: r for r in intel.recent(db, "papers", limit=50)}
    assert float(rows["arxiv:2111.09395"]["score"]) == 900
    asked.clear()
    t[0] += ps.EVERY_S + 1
    scout.run()
    second = [u for u in asked if "arxiv" in u]
    assert len(second) == len(ps.ARXIV_QUERIES[1::2]) and not set(first) & set(second)


def test_most_cited_new_papers_come_first(db):
    intel.upsert(db, "papers", "arxiv:new", title="Newest preprint")
    intel.upsert(db, "papers", "s2:landmark", title="Landmark", score=900)
    intel.upsert(db, "papers", "s2:minor", title="Minor", score=3)
    top = intel.most_scored(db, "papers", "2000-01-01", limit=5)
    assert [r["item_key"] for r in top] == ["s2:landmark", "s2:minor"]
    assert intel.most_scored(db, "papers", "2999-01-01") == []
