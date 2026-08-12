"""The switches on the wall — operator intent, shared between processes.

Once the village runs on its own it is two processes: a tick loop and a web
console. They share a database and nothing else. So a control in Mission
Control cannot be an environment variable, because setting one in the web
process changes nothing in the process that is actually ticking.

These live in ``village_settings`` instead, and the loop reads them on each
pass rather than at startup. That is the whole design: a switch you flip is a
switch that has already taken effect, without restarting anything.

**Environment is still the default.** ``TRADE_LIVING`` and friends decide what
the village does when nobody has said otherwise; a row here is a human
overriding that at runtime, and deleting the row hands the decision back to the
environment. So the precedence is: an explicit toggle, else the environment,
else off.

Every setter takes ``by``. A switch that changed itself with no record of who
did it is the kind of thing the rest of this repository refuses to have.
"""

from __future__ import annotations

from typing import Optional

# The switches that exist. Anything not in here is refused rather than stored,
# so a typo in a form post cannot quietly create a setting nothing reads.
KNOWN = {
    "paused": "the tick loop runs but does nothing",
    "arena": "the arena holds seasons",
    "bazaar": "the bazaar lists and sells",
    "tavern": "alliances form and rivals scheme",
}

TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")


class UnknownSetting(KeyError):
    """A setting nothing reads. Refused rather than stored."""


class Settings:
    """Read and write the wall switches. Cheap enough to call per tick."""

    def __init__(self, db):
        self.db = db

    # -- reading ----------------------------------------------------------
    def raw(self, key: str) -> Optional[str]:
        """The stored value, or None when nobody has decided."""
        try:
            row = self.db.query_one(
                "SELECT value FROM village_settings WHERE key = ?", (key,)
            )
        except Exception:  # noqa: BLE001 - an un-migrated database has no switches
            return None
        if row is None:
            return None
        value = row.get("value")
        return None if value is None else str(value)

    def get(self, key: str, default: bool = False) -> bool:
        """Whether a switch is on, falling back to ``default``.

        ``default`` is what the environment said, so an unset switch means
        "nobody has overridden the configuration" rather than "off".
        """
        value = self.raw(key)
        if value is None:
            return default
        return value.strip().lower() in TRUE

    def all(self) -> dict:
        """Every switch that has been explicitly set, for showing on a page."""
        try:
            rows = self.db.query("SELECT * FROM village_settings") or []
        except Exception:  # noqa: BLE001
            return {}
        return {
            str(row["key"]): {
                "on": str(row["value"]).strip().lower() in TRUE,
                "by": row.get("updated_by"),
                "at": row.get("updated_at"),
            }
            for row in rows
        }

    # -- writing ----------------------------------------------------------
    def set(self, key: str, on: bool, by: str = "web") -> bool:
        if key not in KNOWN:
            raise UnknownSetting(key)
        value = "on" if on else "off"
        existing = self.raw(key)
        if existing is None:
            self.db.insert(
                "village_settings",
                {"key": key, "value": value, "updated_by": by},
            )
        else:
            self.db.execute(
                "UPDATE village_settings SET value = ?, updated_by = ? WHERE key = ?",
                (value, by, key),
            )
        return on

    def toggle(self, key: str, default: bool = False, by: str = "web") -> bool:
        """Flip a switch and return where it landed."""
        return self.set(key, not self.get(key, default), by=by)

    def clear(self, key: str) -> None:
        """Forget an override and hand the decision back to the environment."""
        if key not in KNOWN:
            raise UnknownSetting(key)
        try:
            self.db.execute("DELETE FROM village_settings WHERE key = ?", (key,))
        except Exception:  # noqa: BLE001
            pass


__all__ = ["Settings", "UnknownSetting", "KNOWN"]
