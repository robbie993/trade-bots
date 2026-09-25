@echo off
rem Run by Windows Task Scheduler on the operator's PC every 30 minutes.
rem Reads new uploads from config\video_channels.yaml and carries explicit calls
rem into the village's Railway ledger. Needs `railway login` for this user.
cd /d "%~dp0.."
echo ==== %date% %time% >> logs\video_watch.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\video_watch.py --to-railway >> logs\video_watch.log 2>&1
