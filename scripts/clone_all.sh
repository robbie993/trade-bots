#!/usr/bin/env bash
# Clone the external frameworks from the build document into vendor/.
#
# Nothing in this repository needs any of them. The ecosystem — firms,
# brokerage, brain, heart, kill switches, ledger — is implemented in src/trading
# and is complete without a single clone. These are here so you can read what
# the frameworks do, borrow from them, or wire one in behind an adapter.
#
# Deliberately not automatic: pulling third-party code that can place orders is
# a decision an operator makes on purpose, with their eyes open.
#
#   ./scripts/clone_all.sh              # clone everything reachable
#   ./scripts/clone_all.sh Magents      # clone one
#
# Already-cloned repositories are left alone; re-run with --update to pull.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="${TRADE_VENDOR_DIR:-$ROOT/vendor}"
UPDATE=0

# name|url  — checked with `git ls-remote` on 2026-08-08.
# lex-conscience/lex-conscience is NOT in this list: the repository is not
# reachable. The heart is implemented natively in src/trading/heart instead.
REPOS=(
  "Magents|https://github.com/LLMQuant/Magents.git"
  "TradingAgents|https://github.com/TauricResearch/TradingAgents.git"
  "OctoBot-AI|https://github.com/Drakkar-Software/OctoBot-AI.git"
  "clawock|https://github.com/KCNyu/clawock.git"
  "ai-broker|https://github.com/OpenSourceAGI/ai-broker-investing-agent.git"
  "ai-fund|https://github.com/cubexch/ai-fund.git"
  "OmniRoute|https://github.com/diegosouzapw/OmniRoute.git"
)

WANTED=()
for arg in "$@"; do
  case "$arg" in
    --update) UPDATE=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) WANTED+=("$arg") ;;
  esac
done

mkdir -p "$VENDOR"
echo "vendor directory: $VENDOR"
failed=0

for entry in "${REPOS[@]}"; do
  name="${entry%%|*}"
  url="${entry##*|}"

  if [ ${#WANTED[@]} -gt 0 ]; then
    match=0
    for want in "${WANTED[@]}"; do [ "$want" = "$name" ] && match=1; done
    [ $match -eq 1 ] || continue
  fi

  target="$VENDOR/$name"
  if [ -d "$target/.git" ]; then
    if [ $UPDATE -eq 1 ]; then
      echo "==> updating $name"
      git -C "$target" pull --ff-only || { echo "    pull failed"; failed=$((failed+1)); }
    else
      echo "==> $name already present (use --update to pull)"
    fi
    continue
  fi

  echo "==> cloning $name from $url"
  # Shallow: these are references, not history you need.
  if ! git clone --depth 1 "$url" "$target"; then
    echo "    clone failed — skipping $name"
    failed=$((failed+1))
  fi
done

echo
echo "Done. See what landed with:"
echo "  python -m src.main trade frameworks"
[ $failed -gt 0 ] && echo "($failed repository/repositories could not be fetched)"
exit 0
