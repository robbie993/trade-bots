"""The village as a solar system, for anything that wants to draw one.

`solar-system-agents` is a static cyberpunk dashboard that draws agents as
planets orbiting a sun. It ships with twelve invented agents and it polls
``/health`` and ``/api/status`` on a configured gateway for real ones. This
module is the second half of that: the village's actual state, in the shape
that dashboard expects.

The distinction is the whole point. Cloned and opened, that dashboard is a
beautiful picture of twelve agents that do not exist. Pointed at a village, it
is a picture of *your* firms — their real allocations, their real status, their
real last heartbeat — and a paused firm stops moving on screen because it has
actually stopped.

**What becomes a planet.** Every firm, plus the standing institutions that are
agents in every sense that matters here: the brokerage that scores and
allocates, the court that tries strategies, the council that rules, the heart
that refuses trades, the brain that remembers. They are not firms and they do
not hold capital, so they orbit closest to the sun and never change size.

**How the numbers are derived, and why they are not decorative.** Anyone can
assign a random radius. These are readable:

    size   allocation — a bigger desk is a bigger planet
    orbit  rank by score — the better a firm is doing, the closer it sits
    speed  recent fills — an idle firm visibly stops moving
    color  status — active, paused, killed, or cannot-be-valued

So the picture is not an illustration of the village, it *is* the village, and
"that one has stopped and gone grey" means something specific.

Nothing here can change anything: it reads the store and returns a dict. It is
mounted on the read-only JSON router, which has no POST in it.
"""

from __future__ import annotations

from typing import Optional

from ..money import D, ZERO

# Sun first, then the institutions, then the firms. The list is ordered by how
# close a thing sits to the middle of the village rather than alphabetically.
SUN = {
    "id": "village",
    "name": "The Village",
    "role": "sun",
    "planet": "sun",
    "color": "#fbbf24",
}

# The standing agents. Each one is a real subsystem with a real job, and the
# `role` is what it actually refuses to do wrong — which is the useful thing to
# read on a dashboard at three in the morning.
INSTITUTIONS = (
    ("brokerage", "The Brokerage", "reconciles, scores, allocates", "command",
     "mercury", "#38bdf8", 1),
    ("heart", "The Heart", "blocks trades on conscience", "command",
     "venus", "#f472b6", 2),
    ("court", "The Court", "tries strategies before they trade", "operations",
     "earth", "#4ade80", 3),
    ("council", "The Council", "rules on what the ledger settles", "command",
     # Deliberately not #f87171: that is STATUS_COLOR["blind"], and a council
     # planet the same colour as "this firm cannot be valued" is two meanings
     # sharing one pixel on a display built to be read at a glance.
     "mars", "#fb923c", 4),
    ("brain", "The Brain", "remembers what happened last time", "support",
     "jupiter", "#a78bfa", 5),
)

STATUS_COLOR = {
    "active": "#4ade80",
    "paused": "#fbbf24",
    "killed": "#6b7280",
    "blind": "#f87171",      # holding something that cannot be valued
}


def _heartbeat(card, firm) -> str:
    """When this firm was last heard from, as a string a dashboard can show."""
    if card is not None and card.as_of:
        return str(card.as_of)
    return str(getattr(firm, "updated_at", "") or "")


def _speed(card) -> float:
    """How fast it orbits: trades, not time.

    An idle firm visibly stops. That is the property worth having — a picture
    where everything spins at the same rate whatever is happening is an
    animation, not a display.
    """
    if card is None:
        return 0.2
    trades = int(getattr(card, "trades", 0) or 0)
    return round(min(2.0, 0.2 + trades * 0.05), 3)


def _size(allocation, largest) -> float:
    """Radius from allocation, on a floor so a small desk is still visible."""
    if largest <= 0:
        return 1.0
    share = float(D(allocation) / D(largest)) if D(largest) else 0.0
    return round(0.6 + share * 1.6, 3)


def _colour(firm, card) -> str:
    if card is not None and not card.can_be_valued:
        return STATUS_COLOR["blind"]
    return STATUS_COLOR.get(str(firm.status), "#94a3b8")


def agents(eco, market=None, cards: Optional[list] = None) -> list:
    """Every agent in the village, as the dashboard's schema.

    `cards` is accepted so the caller that already evaluated them — `/api/status`
    does — does not pay for a second pass over the whole ledger.
    """
    market = market if market is not None else eco.market()
    records = eco.store.firms()
    if cards is None:
        cards = eco.brokerage.evaluator.evaluate_all(records, market)
    by_id = {c.firm_id: c for c in cards}

    out = []
    for index, (key, name, role, tier, planet, colour, orbit) in enumerate(INSTITUTIONS):
        out.append({
            "id": key,
            "name": name,
            "model": "deterministic",
            "role": role,
            "tier": tier,
            # Not a language model, and saying so on the dashboard is the same
            # honesty the analysts practise: the court's jury is arithmetic.
            "premium": False,
            "skills": 0,
            "channels": ["village"],
            "status": "active",
            "heartbeat": str(market.as_of() or ""),
            "planet": planet,
            "color": colour,
            "size": 0.8,
            "orbit": orbit,
            "speed": round(0.6 - index * 0.05, 3),
        })

    largest = max((D(f.allocation) for f in records), default=ZERO)
    ranked = sorted(
        records,
        key=lambda f: float(D(by_id[f.id].score)) if f.id in by_id else 0.0,
        reverse=True,
    )
    for rank, firm in enumerate(ranked):
        card = by_id.get(firm.id)
        specs = eco.specs().get(firm.firm_key)
        out.append({
            "id": firm.firm_key,
            "name": firm.name or firm.firm_key,
            "model": firm.strategy or "pod",
            "role": firm.asset_class or "trading firm",
            "tier": "operations",
            "premium": False,
            "skills": len(getattr(specs, "analysts", ()) or ()),
            "channels": list(firm.universe or [])[:6],
            "status": str(firm.status),
            "heartbeat": _heartbeat(card, firm),
            "planet": "",                      # the dashboard picks its own art
            "color": _colour(firm, card),
            "size": _size(firm.allocation, largest),
            "orbit": len(INSTITUTIONS) + 1 + rank,
            "speed": _speed(card),
            "rings": bool(card is not None and not card.can_be_valued),
            # Beyond the dashboard's schema, and harmless to it — anything that
            # ignores unknown keys keeps working, and anything that wants the
            # real numbers now has them without a second endpoint.
            "allocation": str(firm.allocation),
            "equity": str(card.equity) if card else None,
            "score": str(card.score) if card else None,
            "measured": bool(card.can_be_valued) if card else True,
        })
    return out


def solar_system(eco, market=None, cards: Optional[list] = None) -> dict:
    return {"sun": dict(SUN), "agents": agents(eco, market, cards)}


__all__ = ["INSTITUTIONS", "STATUS_COLOR", "SUN", "agents", "solar_system"]
