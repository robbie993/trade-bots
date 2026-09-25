@echo off
rem Run by Windows Task Scheduler on the operator's PC every 30 minutes.
rem Carries what the live fleet decided into the village's Railway ledger.
rem Needs `railway login` for this Windows user; see scripts/fleet_sync.py.
cd /d "%~dp0.."
echo ==== %date% %time% >> logs\fleet_sync.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\fleet_sync.py --to-railway >> logs\fleet_sync.log 2>&1
