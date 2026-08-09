"""Operator-declared asset restrictions, by category.

The sanctity foundation already refuses individual symbols. This adds the
question underneath it: *what is this instrument*? An operator who will not
hold weapons manufacturers, or anything sanctioned, is stating a rule about a
category, and restating it as a list of tickers they have to keep current is
how the rule quietly stops being enforced.

Two rules govern this file, both of which matter more than the feature:

* **The operator supplies the mapping.** There is no bundled classification of
  which company does what. Guessing a symbol's industry from its name, or
  shipping a stale list scraped from somewhere, would produce refusals nobody
  could justify and — worse — silent *approvals* for things that are on the
  list under another ticker.
* **Absent means absent, not clean.** With no restrictions file, no category
  is restricted and sanctity behaves exactly as before. It never reports a
  symbol as "checked and clear" when there was nothing to check against.

    # config/restricted_assets.yaml
    categories:
      weapons: [LMT, RTX]
      sanctioned: [XYZ]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AssetRestrictions:
    """symbol -> the operator's categories for it."""

    by_symbol: dict = field(default_factory=dict)
    source: Optional[Path] = None

    @property
    def is_empty(self) -> bool:
        return not self.by_symbol

    @property
    def categories(self) -> set:
        return {c for cats in self.by_symbol.values() for c in cats}

    def categories_for(self, symbol: str) -> list:
        return sorted(self.by_symbol.get(symbol.upper(), []))

    def violations(self, symbol: str, restricted: tuple) -> list:
        """Which of the operator's restricted categories this symbol is in."""
        wanted = {c.strip().lower() for c in restricted if str(c).strip()}
        return [c for c in self.categories_for(symbol) if c in wanted]

    @classmethod
    def load(cls, path: Optional[Path]) -> "AssetRestrictions":
        """Read the mapping, or return an empty one when there is no file."""
        if path is None:
            return cls()
        target = Path(path)
        if not target.exists():
            return cls()

        from ..firms.spec import parse_config

        raw = parse_config(target)
        categories = raw.get("categories", raw) or {}
        if not isinstance(categories, dict):
            raise ValueError(f"{target}: `categories` must be a mapping of category -> symbols")

        by_symbol: dict = {}
        for category, symbols in categories.items():
            name = str(category).strip().lower()
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(",") if s.strip()]
            for symbol in symbols or []:
                by_symbol.setdefault(str(symbol).strip().upper(), []).append(name)
        return cls(by_symbol=by_symbol, source=target)


__all__ = ["AssetRestrictions"]
