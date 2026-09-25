# Run this on the PC, in the trade-bots folder. Paste the whole output back.
#
# It prints one compact report and changes nothing. No secret values are shown —
# credentials appear only as character counts, so the output is safe to paste
# into a chat.

$ErrorActionPreference = "SilentlyContinue"
function L($k, $v) { "{0,-22} {1}" -f $k, $v }

"===== VILLAGE STATUS ====="
L "when"        (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
L "folder"      (Get-Location).Path
L "windows"     [System.Environment]::OSVersion.VersionString

"`n--- repo ---"
$branch = (git branch --show-current 2>&1)
L "branch"      $branch
L "head"        (git log --oneline -1 2>&1)
L "clean"       ((git status --porcelain 2>&1 | Measure-Object -Line).Lines.ToString() + " uncommitted")
if ($branch -ne "claude/ai-village-trading-build-m4bg19") {
  "  *** WRONG BRANCH — expected claude/ai-village-trading-build-m4bg19"
  "  *** fix: git checkout claude/ai-village-trading-build-m4bg19"
}

"`n--- files that must exist ---"
foreach ($f in @("src\main.py","requirements.txt","docs\research-dossier.md",
                 "scripts\research_scout.py","src\trading\council_of_ais.py",
                 "config\firm_config.yaml","config\firm_config_daily.yaml",
                 "bots\fleet_scanner.py",".alpaca_credentials")) {
  L $f (if (Test-Path $f) { "yes" } else { "MISSING" })
}

"`n--- python / venv ---"
L "python"      (python --version 2>&1)
$vpy = "venv\Scripts\python.exe"
L "venv"        (if (Test-Path $vpy) { "built" } else { "NOT BUILT" })
if (Test-Path $vpy) {
  L "venv python" (& $vpy --version 2>&1)
  L "fastapi"    (& $vpy -c "import fastapi;print(fastapi.__version__)" 2>&1)
  L "playwright" (& $vpy -c "import importlib.metadata as m;print(m.version('playwright'))" 2>&1)
}

"`n--- databases ---"
foreach ($d in @("data\mvv.db","data\mvv_daily.db")) {
  if (Test-Path $d) { L $d ("{0:N0} MB" -f ((Get-Item $d).Length/1MB)) }
  else { L $d "absent (village will start a fresh book)" }
}

"`n--- credentials: names and LENGTHS only, never values ---"
foreach ($n in @("ALPACA_API_KEY_ID","ALPACA_API_SECRET_KEY","TRADE_DATA_SOURCE",
                 "TRADE_ALPACA_FEED","TRADE_BAR")) {
  $v = [Environment]::GetEnvironmentVariable($n)
  if ($n -like "*KEY*") { L $n (if ($v) { "$($v.Length) chars" } else { "NOT SET" }) }
  else                  { L $n (if ($v) { $v } else { "NOT SET" }) }
}
if (Test-Path ".alpaca_credentials") {
  "  .alpaca_credentials contains these NAMES:"
  (Get-Content .alpaca_credentials) | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_]+)\s*=') { "      " + $matches[1] }
  }
}

"`n--- can it actually reach the feed? (the only check that matters) ---"
if ((Test-Path $vpy) -and $env:ALPACA_API_KEY_ID) {
  & $vpy -c @"
import os
os.environ.setdefault('TRADE_DATA_SOURCE','alpaca')
try:
    from src.trading.config import TradingConfig
    from src.trading.data.feeds import build_feed
    c = TradingConfig(); f = build_feed(c.data)
    print(f'  feed class        {type(f).__name__}')
    bars = f.series('SPY')
    print(f'  SPY bars          {len(bars)}')
    print(f'  newest bar        {bars[-1].as_of if bars else "none"}')
    print('  VERDICT           LIVE DATA' if len(bars) > 100 else '  VERDICT           SUSPECT')
except Exception as e:
    print(f'  FEED FAILED       {type(e).__name__}: {str(e)[:120]}')
"@ 2>&1
} else {
  "  skipped - venv or ALPACA_API_KEY_ID missing (see above)"
}

"`n--- reconcile ---"
if (Test-Path $vpy) { & $vpy -m src.main trade reconcile 2>&1 | Select-Object -Last 3 }

"`n===== END ====="
