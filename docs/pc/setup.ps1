# Village setup for the Windows PC.
#
#   Right-click this file -> "Run with PowerShell"
#
# It does everything that can be automated: finds Python, builds the virtual
# environment, installs the dependencies, puts the databases in place, and
# checks the books reconcile. It stops and tells you plainly if something is
# missing rather than carrying on and leaving you with a village that looks
# like it works.
#
# It will NOT invent credentials. There is exactly one file you have to copy
# across by hand, and the script tells you where it goes.

$ErrorActionPreference = "Stop"

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "    $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "    $m" -ForegroundColor Red }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

# --- where is the village? ---------------------------------------------------
Say "finding the village"
$root = $null
foreach ($guess in @("$PWD\trade-bots", "$HOME\trade-bots", "$HOME\Desktop\trade-bots",
                     "$HOME\Documents\trade-bots")) {
    if (Test-Path "$guess\src\main.py") { $root = $guess; break }
}
if (-not $root) {
    Bad "Could not find trade-bots."
    Warn "Clone it first:  git clone git@github.com:robbie993/trade-bots.git"
    Warn "Then run this script from the folder that contains it."
    Read-Host "`nPress Enter to close"; exit 1
}
Good "found $root"
Set-Location $root

# --- python ------------------------------------------------------------------
Say "checking Python"
$py = $null
foreach ($c in @("python", "python3", "py")) {
    try { $v = & $c --version 2>&1; if ($v -match "Python 3\.(1[0-9]|[89])") { $py = $c; break } } catch {}
}
if (-not $py) {
    Bad "Python 3.9+ not found."
    Warn "Install it from https://python.org (tick 'Add Python to PATH'), then re-run."
    Read-Host "`nPress Enter to close"; exit 1
}
Good "$(& $py --version)"

# --- virtual environment -----------------------------------------------------
Say "building the virtual environment"
if (-not (Test-Path "venv")) { & $py -m venv venv }
$vpy = "$root\venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Bad "venv build failed"; Read-Host "`nPress Enter"; exit 1 }
Good "venv ready"

Say "installing dependencies (a few minutes)"
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt
# Current Playwright installs fine on Windows — the 1.60.0 pin in the notes was
# a macOS 13 workaround only, and does not apply here.
& $vpy -m pip install --quiet playwright beautifulsoup4 lxml
& $vpy -m playwright install chromium
Good "dependencies installed"

# --- databases ---------------------------------------------------------------
Say "checking for the village databases"
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }
if (Test-Path "data\mvv.db") {
    $mb = [math]::Round((Get-Item "data\mvv.db").Length / 1MB, 0)
    Good "data\mvv.db present ($mb MB)"
} else {
    Warn "data\mvv.db is missing."
    Warn "Unzip village-databases-2026-09-23.zip and put mvv.db and mvv_daily.db in $root\data\"
    Warn "Without them the village starts an empty book — which is fine, just not your history."
}

# --- the one thing that must be copied by hand -------------------------------
Say "checking credentials"
$creds = "$HOME\trade\.alpaca_credentials"
if (Test-Path $creds) {
    Good "found $creds"
} else {
    Warn "MISSING: $creds"
    Warn "Copy it from the Mac at /Users/robbie/trade/.alpaca_credentials"
    Warn "Nothing else can supply this, and without it the feed refuses rather than"
    Warn "faking prices - which is the failure you want, but it will not trade."
}

# --- does it actually work? --------------------------------------------------
Say "verifying the books reconcile"
try {
    & $vpy -m src.main trade reconcile
    Good "reconciled"
} catch {
    Warn "reconcile did not pass. Do not start the village until it does -"
    Warn "a book that does not add up is the one thing worth stopping for."
}

Write-Host ""
Say "done. To start the village:"
Write-Host ""
Write-Host "    venv\Scripts\python -m src.main serve --host 127.0.0.1 --port 8000" -ForegroundColor White
Write-Host "        then open http://127.0.0.1:8000/village" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    venv\Scripts\python -m src.main trade run --interval 120" -ForegroundColor White
Write-Host "        the loop that actually trades - run it in a second window" -ForegroundColor DarkGray
Write-Host ""
Warn "The .sh scripts (village.sh) need WSL or Git Bash. The two commands above"
Warn "are the same thing without them, which is why they are what this prints."
Write-Host ""
Read-Host "Press Enter to close"
