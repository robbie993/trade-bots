"""Why is that firm dead? Reads only; changes nothing.

    python scripts/why_dead.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.db import Database

db = Database.from_url(Config().database_url)

print("=== firms")
for r in db.query("SELECT firm_key, status, venue, allocation, cash, high_water_mark FROM firms ORDER BY firm_key"):
    flag = "  <-- DEAD" if r["status"] == "killed" else ""
    print(f"  {r['firm_key']:20} {r['status']:8} alloc {r['allocation']:>12} "
          f"cash {r['cash']:>12} hwm {r['high_water_mark']:>12}{flag}")

print("\n=== every kill-related event, newest first")
for r in db.query(
    "SELECT created_at, event_type, detail FROM brokerage_events "
    "WHERE event_type LIKE '%kill%' OR event_type LIKE '%halt%' "
    "ORDER BY id DESC LIMIT 12"):
    print(f"  {str(r['created_at'])[:19]}  {r['event_type']:18} {str(r['detail'])[:150]}")

print("\n=== approvals, any status")
rows = db.query("SELECT id, action, status, approved_by, reason FROM human_approvals ORDER BY id DESC LIMIT 8")
for r in rows:
    print(f"  #{r['id']} {r['action']:16} {r['status']:9} by={r['approved_by'] or '-':12} {str(r['reason'])[:80]}")
if not rows:
    print("  (none — so nothing was ever approved)")

print("\n=== performance rows: how are the bars spaced?")
for r in db.query(
    "SELECT f.firm_key, p.as_of, p.drawdown_pct, p.sharpe, p.equity "
    "FROM firm_performance p JOIN firms f ON f.id = p.firm_id "
    "ORDER BY p.id DESC LIMIT 14"):
    print(f"  {r['firm_key']:20} {str(r['as_of'])[:19]}  dd {str(r['drawdown_pct']):>7}  "
          f"sharpe {str(r['sharpe'] or '-'):>9}  equity {r['equity']}")
db.close()
