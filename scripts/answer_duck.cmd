@echo off
rem Run by Windows Task Scheduler on the operator's PC every hour.
rem The second mind: DuckDuckGo's free AI chat, through the village browser, no account.
cd /d "%~dp0.."
echo ==== %date% %time% >> logs\answer_duck.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\answer_duck.py --to-railway >> logs\answer_duck.log 2>&1
