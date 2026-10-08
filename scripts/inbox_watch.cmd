@echo off
rem Run by Windows Task Scheduler on the operator's PC every 5 minutes.
rem Watches and reads what was sent to the village at /village/send.
rem Quiet when nothing is waiting, so the log only grows when something was sent.
cd /d "%~dp0.."
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
if not exist logs mkdir logs
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\inbox_watch.py --to-railway >> logs\inbox_watch.log 2>&1
