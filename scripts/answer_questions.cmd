@echo off
rem Run by Windows Task Scheduler on the operator's PC every two hours.
rem Answers the firms' questions with Claude Code (the operator's own plan).
cd /d "%~dp0.."
echo ==== %date% %time% >> logs\answer_questions.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\answer_questions.py --to-railway >> logs\answer_questions.log 2>&1
