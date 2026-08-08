#!/usr/bin/env bash
# Set up the File Court's headless reviewer.
#
# This script does only the parts that can be automated. The interactive
# reviewers (/tribunal, /roundtable:agent-review-panel) and any dashboard are
# per-user installs on the machine you actually sit at — see VILLAGE.md.
set -euo pipefail

cd "$(dirname "$0")/.."
CT_PATH="${MVV_CODETRIBUNAL_PATH:-$PWD/CodeTribunal}"

echo "==> Applying migrations"
python -m src.main init-db

echo "==> Creating court directories"
mkdir -p "${MVV_UPLOADS_DIR:-uploads}" "${MVV_PROCESSED_DIR:-processed}" logs

if [ -d "$CT_PATH" ]; then
    echo "==> CodeTribunal already present at $CT_PATH"
else
    echo "==> Cloning CodeTribunal (Hugging Face, not GitHub)"
    if ! git clone https://huggingface.co/spaces/amine-yagoub/CodeTribunal "$CT_PATH"; then
        echo "    Clone failed. If this is a sandbox, huggingface.co may be"
        echo "    blocked by egress policy — run this on your own machine."
        echo "    The court still works; every case will read UNREVIEWED"
        echo "    until a reviewer is installed."
    fi
fi

if [ -f "$CT_PATH/requirements.txt" ]; then
    echo "==> Installing CodeTribunal dependencies"
    pip install -r "$CT_PATH/requirements.txt"
fi

if [ -f "$CT_PATH/.env.example" ] && [ ! -f "$CT_PATH/.env" ]; then
    cp "$CT_PATH/.env.example" "$CT_PATH/.env"
    echo "==> Wrote $CT_PATH/.env — set ZAI_API_KEY and ZAI_API_BASE in it"
fi

echo
echo "==> Reviewer status"
python -m src.main court doctor

cat <<'EOF'

Still to do by hand, in Claude Code on your own machine:

  mkdir -p ~/.claude/skills/tribunal
  curl -o ~/.claude/skills/tribunal/SKILL.md \
    https://raw.githubusercontent.com/hekman316/claude-skill-tribunal/main/SKILL.md

  /plugin marketplace add wan-huiyan/agent-review-panel
  /plugin install roundtable@agent-review-panel

Then restart Claude Code — skills load at session start.
EOF
