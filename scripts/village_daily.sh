#!/usr/bin/env bash
# The daily village. Same machinery as `village.sh`, different clock.
#
#     ./scripts/village_daily.sh start | status | logs | stop
#
# This is `village.sh` with six environment variables in front of it and no
# logic of its own, deliberately: two copies of a start/stop script drift, and
# the one that drifts is always the one you are not looking at.
#
# **What makes it a separate village and not a second process on the same one.**
# Every piece of state is its own:
#
#     database     data/mvv_daily.db        (MVV_LOCAL_DB)
#     firms        config/firm_config_daily.yaml
#     pid files    run-daily/               (MVV_RUN_DIR)
#     logs         logs-daily/              (MVV_LOG_DIR)
#     console      127.0.0.1:8001           (MVV_GATE_PORT)
#
# Nothing here is shared with the 15m village, so starting, stopping or losing
# this one cannot touch that one. That is the rule for any new service in this
# project and it is not negotiable: a new thing never perturbs a running thing.
#
# **Why it exists.** VERITAS is a daily strategy — SMA200 means two hundred
# daily closes. The 15m village fetches 3,680 fifteen-minute bars, keeps 720 and
# hands a bot 250, and an honest SMA200 there would need 5,200. No setting
# reaches that, because the bars do not exist. Rather than compute a 50-hour
# average and call it a 200-day one, the clock changes.
#
# `TRADE_BAR=1d` is the village's resolution. `VERITAS_BARS_PER_DAY=1` is the
# strategy's own declaration of what a day is worth in bars, which it will not
# infer — see the comment on that constant. Both must be set; setting one is how
# a window silently changes meaning.
set -euo pipefail

cd "$(dirname "$0")/.."

export MVV_LOCAL_DB="${MVV_LOCAL_DB:-sqlite:///data/mvv_daily.db}"
export TRADE_FIRMS_CONFIG="${TRADE_FIRMS_CONFIG:-config/firm_config_daily.yaml}"
export TRADE_BAR="${TRADE_BAR:-1d}"
export VERITAS_BARS_PER_DAY="${VERITAS_BARS_PER_DAY:-1}"
export MVV_RUN_DIR="${MVV_RUN_DIR:-run-daily}"
export MVV_LOG_DIR="${MVV_LOG_DIR:-logs-daily}"
export MVV_GATE_PORT="${MVV_GATE_PORT:-8001}"

# A daily bar turns over once a day. Ticking every two minutes would ask the
# feed seven hundred times to be told the same thing, so this is slow on
# purpose: fifteen minutes is frequent enough to catch the close promptly and
# to notice the village has stopped, and rare enough to be nearly free.
export MVV_TICK_INTERVAL="${MVV_TICK_INTERVAL:-900}"

exec ./scripts/village.sh "$@"
