from src.trading.topics import tag


def test_topics_are_tagged_and_a_plain_post_is_not():
    assert tag("Jarvis, an AI agent that trades for you") == ["ai_agent"]
    assert "strategy" in tag("my mean reversion strategy, backtested on SPY")
    assert "village" in tag("a swarm of multi-agent traders")
    assert tag("Burger CEO taste-test season is officially open") == []
