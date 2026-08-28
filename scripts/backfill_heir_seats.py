"""Give heirs filed before the fix the seats they should have inherited.

The first six heirs were created by `bankruptcy.file_successor` before it
carried analyst seats, so their genomes have no `analysts` key and
`build_firm` falls through to a single `technical` seat. Their predecessors'
seats are still in config/firm_config.yaml; this copies them across, adding
the `signals` seat every heir is meant to have.

Idempotent: an heir that already has seats is left alone.
"""
import sys
from src.db.connection import Database
from src.trading.ecosystem import Ecosystem
from src.trading.firms.bankruptcy import INHERITED_SEAT

def main(url):
    eco = Ecosystem(Database.from_url(url))
    changed = []
    for rec in eco.store.firms():
        genome = dict(rec.genome or {})
        if genome.get("analysts"):
            continue
        parent_key = genome.get("inherited_from")
        if not parent_key:
            continue                      # not an heir
        spec = eco.specs().get(parent_key)
        seats = [str(s) for s in (getattr(spec, "analysts", []) or [])]
        if not seats:
            print(f"  {rec.firm_key}: no spec for parent {parent_key}; skipped")
            continue
        if INHERITED_SEAT not in seats:
            seats.append(INHERITED_SEAT)
        genome["analysts"] = seats
        rec.genome = genome
        eco.store.upsert_firm(rec)
        changed.append((rec.firm_key, parent_key, seats))
    for key, parent, seats in changed:
        print(f"  {key:<22} <- {parent:<20} {seats}")
    print(f"{len(changed)} heir(s) updated")
    eco.db.close()

if __name__ == "__main__":
    main(sys.argv[1])
