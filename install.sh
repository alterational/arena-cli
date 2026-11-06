#!/usr/bin/env bash
# install.sh —— Installs this directory as a Hermes skill (slash command /arena-cli)
#
#   ./install.sh                 # Install to ~/.hermes/skills/arena-cli
#   ./install.sh --force         # Overwrite if it already exists
#   HERMES_HOME=~/work/.hermes ./install.sh   # Specify a profile
#   ./install.sh --link          # Use a symlink (code changes take effect immediately; good for development)
#
# After installation, run this directly in Hermes: /arena-cli Research the pricing strategies of three competitors
set -euo pipefail

SKILL_NAME="arena-cli"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_ROOT="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_ROOT/skills/$SKILL_NAME"
MODE="copy"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --link)  MODE="link" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Older versions installed under the name arena-agent: leaving it in place would create two slash commands, so warn the user
if [ -e "$HERMES_ROOT/skills/arena-agent" ] && [ "$MODE" != "link" ]; then
  echo "⚠️  Found an old skill directory: $HERMES_ROOT/skills/arena-agent (renamed to arena-cli in 2.4.0). Consider removing it: rm -rf \"$HERMES_ROOT/skills/arena-agent\"" >&2
fi

if [ -e "$DEST" ]; then
  if [ "$FORCE" -ne 1 ] && [ "$MODE" = "copy" ]; then
    echo "⚠️  Already exists: $DEST (add --force to overwrite)" >&2
    exit 1
  fi
  rm -rf "$DEST"
fi

mkdir -p "$HERMES_ROOT/skills"

if [ "$MODE" = "link" ]; then
  ln -s "$SRC" "$DEST"
  echo "🔗 Symlinked: $DEST -> $SRC"
else
  mkdir -p "$DEST"
  cp "$SRC/SKILL.md" "$DEST/"
  cp -R "$SRC/scripts" "$DEST/"
  cp -R "$SRC/arena" "$DEST/"
  find "$DEST/arena" -type d -name __pycache__ -prune -exec rm -rf {} +
  [ -d "$SRC/references" ] && cp -R "$SRC/references" "$DEST/"
  [ -d "$SRC/snippets" ]   && cp -R "$SRC/snippets"   "$DEST/"
  [ -f "$SRC/config.example.json" ] && cp "$SRC/config.example.json" "$DEST/"
  echo "✅ Installed: $DEST"
fi

# Dependency check
if ! python3 -c "import playwright" 2>/dev/null; then
  echo "⚠️  Playwright is not installed: pip install -r \"$SRC/requirements.txt\" && python3 -m playwright install chromium" >&2
fi

# Spec self-check
python3 "$SRC/scripts/verify_skill.py" "$SRC" || true

if ! command -v arena >/dev/null 2>&1; then
  echo "Tip: to use the \`arena\` command directly (not just the slash command), install it once: pip install -e \"$SRC\" (or pipx install \"$SRC\")" >&2
fi

cat <<EOF

Usage (in Hermes):
  /arena-cli Research the pricing strategies of three competitors
  /arena-cli --cookies ~/uploads/arena.ai_cookies.txt your question

If you have the CLI installed (equivalent, with more features):
  arena ask "Research the pricing strategies of three competitors"
  arena cookies import ~/uploads/arena.ai_cookies.txt && arena check
  arena chat list / arena profile use work / arena doctor

If you have no cookies file yet (choose one):
  1) Log in to arena.ai in your browser → export cookies (Netscape cookies.txt / EditThisCookie JSON)
  2) python3 "$SRC/scripts/arena_agent.py" login --email your@email.com --headed --wait 300

Verify:
  python3 "$SRC/scripts/arena_agent.py" check
EOF
