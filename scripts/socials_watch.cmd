@echo off
rem Run by Windows Task Scheduler on the operator's PC every hour.
rem Instagram, X and TikTok share the village browser, so they run one after another.
cd /d "%~dp0.."
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
echo ==== %date% %time% >> logs\socials_watch.log
%PY% scripts\insta_watch.py --to-railway >> logs\socials_watch.log 2>&1
%PY% scripts\x_watch.py --to-railway >> logs\socials_watch.log 2>&1
%PY% scripts\tiktok_watch.py --to-railway >> logs\socials_watch.log 2>&1
