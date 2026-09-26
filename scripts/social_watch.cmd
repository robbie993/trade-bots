@echo off
rem Run by Windows Task Scheduler on the operator's PC every 30 minutes.
rem Reads the subreddits in config\social_sources.yaml into the village's ledger.
cd /d "%~dp0.."
echo ==== %date% %time% >> logs\social_watch.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\social_watch.py --to-railway >> logs\social_watch.log 2>&1
