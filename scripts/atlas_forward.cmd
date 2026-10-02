@echo off
rem Run by Windows Task Scheduler on the operator's PC every weekday at 17:30 local.
rem Advances the Atlas R3 forward paper books (atlas_research/PREREG_R3.md).
rem Shadow ledger only: it reads prices, EDGAR and option quotes and sends no orders.
rem Missed days are filled in on the next run. Needs SEC_USER_AGENT in .env.
cd /d "%~dp0.."
set ATLAS_FORWARD_DIR=atlas_forward
echo ==== %date% %time% >> logs\atlas_forward.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m atlas_research.r3.forward run >> logs\atlas_forward.log 2>&1
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m atlas_research.r3.forward report >> logs\atlas_forward.log 2>&1
