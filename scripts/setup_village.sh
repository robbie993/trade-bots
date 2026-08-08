#!/usr/bin/env bash
# The AI Village installer — every step that can be automated, in one command.
#
#   ./scripts/setup_village.sh              # phases 2,3,6 (the court + db)
#   ./scripts/setup_village.sh --all        # also ruflo and a dashboard
#   ./scripts/setup_village.sh --dashboard lattice
#   ./scripts/setup_village.sh --help
#
# What it CANNOT do, in any mode: Claude Code `/plugin` and skill commands are
# typed into an interactive session, so they are printed at the end for you to
# paste. Everything else here is real and idempotent — rerun it safely.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
CT_PATH="${MVV_CODETRIBUNAL_PATH:-$REPO/CodeTribunal}"
DO_RUFLO=0
DO_DASHBOARD=""
FAILED=()

while [ $# -gt 0 ]; do
    case "$1" in
        --all)        DO_RUFLO=1; DO_DASHBOARD="${DO_DASHBOARD:-lattice}" ;;
        --ruflo)      DO_RUFLO=1 ;;
        --dashboard)  shift; DO_DASHBOARD="${1:-lattice}" ;;
        --help|-h)
            # The header comment block, minus the shebang: stop at the first
            # line that is not a comment so this cannot drift out of range.
            awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
            exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '    ! %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

# ---------------------------------------------------------------- prereqs
say "Checking prerequisites"
for tool in python3 git; do
    if command -v "$tool" >/dev/null 2>&1; then
        note "$tool $($tool --version 2>&1 | head -1)"
    else
        warn "$tool is required and not installed — stopping."
        exit 1
    fi
done
for tool in node npm; do
    command -v "$tool" >/dev/null 2>&1 \
        && note "$tool $($tool --version 2>&1 | head -1)" \
        || warn "$tool missing — Ruflo, Yama and the dashboards need it."
done

# ------------------------------------------------- phase 3: the database
say "Phase 3 — database"
python3 -m src.main init-db || { warn "migrations failed"; FAILED+=("migrations"); }

say "Creating court directories"
mkdir -p "${MVV_UPLOADS_DIR:-uploads}" "${MVV_PROCESSED_DIR:-processed}" logs
note "uploads/ processed/ logs/"

# ----------------------------------------------- phase 2: the File Court
say "Phase 2 — CodeTribunal (the one reviewer that runs headless)"
if [ -d "$CT_PATH/.git" ] || [ -d "$CT_PATH/src" ]; then
    note "already present at $CT_PATH"
else
    # It is a Hugging Face *Space*. Note /spaces/ in the path.
    if git clone --depth 1 \
        https://huggingface.co/spaces/amine-yagoub/CodeTribunal "$CT_PATH"; then
        note "cloned to $CT_PATH"
    else
        warn "clone failed."
        warn "If this is a sandbox, huggingface.co is likely blocked by egress"
        warn "policy — run this script on your own machine instead."
        FAILED+=("codetribunal-clone")
    fi
fi

if [ -f "$CT_PATH/requirements.txt" ]; then
    say "Installing CodeTribunal dependencies"
    python3 -m pip install -r "$CT_PATH/requirements.txt" \
        || { warn "pip install failed"; FAILED+=("codetribunal-deps"); }
fi

for envfile in "$CT_PATH/.env.example" "$CT_PATH/.env.template"; do
    if [ -f "$envfile" ] && [ ! -f "$CT_PATH/.env" ]; then
        cp "$envfile" "$CT_PATH/.env"
        warn "wrote $CT_PATH/.env — set ZAI_API_KEY and ZAI_API_BASE in it"
        break
    fi
done

# The tribunal skill is a plain file, so this part needs no interactive session.
say "Installing the tribunal skill (~/.claude/skills/tribunal)"
if [ -f "$HOME/.claude/skills/tribunal/SKILL.md" ]; then
    note "already installed"
else
    mkdir -p "$HOME/.claude/skills/tribunal"
    if curl -fsSL -o "$HOME/.claude/skills/tribunal/SKILL.md" \
        https://raw.githubusercontent.com/hekman316/claude-skill-tribunal/main/SKILL.md
    then
        note "installed — restart Claude Code, skills load at session start"
    else
        warn "download failed"
        rmdir "$HOME/.claude/skills/tribunal" 2>/dev/null
        FAILED+=("tribunal-skill")
    fi
fi

if [ "${MVV_COURT_YAMA:-false}" = "true" ]; then
    say "Installing Yama"
    npm install -g @juspay/yama || { warn "yama install failed"; FAILED+=("yama"); }
fi

# ------------------------------------------------------- phase 1: Ruflo
if [ "$DO_RUFLO" = "1" ]; then
    say "Phase 1 — Ruflo"
    warn "note: 'ruflo init' adds a block to your global ~/.claude/CLAUDE.md."
    warn "      --no-global is passed below to leave it alone; drop that flag"
    warn "      if you do want the global pointer."
    npx -y ruflo@latest init --no-global || { warn "ruflo init failed"; FAILED+=("ruflo-init"); }
    # --fix only PRINTS suggested commands; it does not apply them.
    npx -y ruflo@latest doctor --fix || true
fi

# --------------------------------------------------- phase 5: dashboard
if [ -n "$DO_DASHBOARD" ]; then
    say "Phase 5 — dashboard ($DO_DASHBOARD)"
    case "$DO_DASHBOARD" in
        lattice)
            note "start it with:  npx lattice-agents $REPO"
            note "then open http://localhost:3000" ;;
        swarm|claude-ville|rufloui|openclaw)
            declare -A REPOS=(
                [swarm]=https://github.com/miopea/swarm.git
                [claude-ville]=https://github.com/deadronos/claude-ville.git
                [rufloui]=https://github.com/Mario-PB/rufloui.git
                [openclaw]=https://github.com/bokiko/openClaw-dashboard.git
            )
            target="$REPO/$DO_DASHBOARD"
            [ -d "$target" ] || git clone --depth 1 "${REPOS[$DO_DASHBOARD]}" "$target" \
                || { warn "clone failed"; FAILED+=("dashboard"); }
            if [ -f "$target/package.json" ]; then
                (cd "$target" && npm install) \
                    || { warn "npm install failed"; FAILED+=("dashboard-deps"); }
                note "start it with:  cd $target && npm run dev"
            fi ;;
        *) warn "unknown dashboard '$DO_DASHBOARD'"
           note "choose: lattice | swarm | claude-ville | rufloui | openclaw" ;;
    esac
fi

# ------------------------------------------------------------- verdict
say "Phase 7 — what can actually review a file"
python3 -m src.main court doctor

say "Summary"
if [ ${#FAILED[@]} -eq 0 ]; then
    note "every automated step succeeded"
else
    warn "these steps failed: ${FAILED[*]}"
    warn "the court still runs; cases will read UNREVIEWED until a reviewer works"
fi

cat <<'EOF'

  Still to do by hand — these are interactive Claude Code commands and
  cannot be scripted. Paste them into a Claude Code session:

      /plugin marketplace add ruvnet/ruflo
      /plugin install ruflo-core@ruflo
      /plugin install ruflo-swarm@ruflo
      /plugin install ruflo-autopilot@ruflo
      /plugin install ruflo-federation@ruflo

      /plugin marketplace add wan-huiyan/agent-review-panel
      /plugin install roundtable@agent-review-panel

  Then restart Claude Code — plugins and skills load at session start.

  Verify the court:
      python3 -m src.main court review <some-file>.py
EOF
