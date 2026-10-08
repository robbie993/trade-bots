@echo off
rem One tick of the daily village on Windows (the PC's scheduled task runs this).
rem Same settings as scripts/village_daily.sh; paper venue only.
cd /d "%~dp0.."
set TRADE_FIRMS_CONFIG=config/firm_config_daily.yaml
set TRADE_BAR=1d
set VERITAS_BARS_PER_DAY=1
set TRADE_HISTORY_DAYS=450
set TRADE_DATA_SOURCE=alpaca,yahoo
if not exist logs-daily mkdir logs-daily
echo ==== %date% %time% >> logs-daily\tick.log
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m src.cli trade tick --database-url sqlite:///data/mvv_daily.db >> logs-daily\tick.log 2>&1
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" scripts\notify_fills.py --db data/mvv_daily.db >> logs-daily\tick.log 2>&1
