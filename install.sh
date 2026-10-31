#!/usr/bin/env bash
# install.sh —— 把本目录安装成 Hermes 技能（斜杠命令 /arena-agent）
#
#   ./install.sh                 # 安装到 ~/.hermes/skills/arena-agent
#   ./install.sh --force         # 已存在时覆盖
#   HERMES_HOME=~/work/.hermes ./install.sh   # 指定 profile
#   ./install.sh --link          # 用软链接（改代码即时生效，适合开发）
#
# 安装完成后在 Hermes 里直接：/arena-agent 帮我调研三家竞品的定价策略
set -euo pipefail

SKILL_NAME="arena-agent"
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
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

if [ -e "$DEST" ]; then
  if [ "$FORCE" -ne 1 ] && [ "$MODE" = "copy" ]; then
    echo "⚠️  已存在：$DEST（要覆盖请加 --force）" >&2
    exit 1
  fi
  rm -rf "$DEST"
fi

mkdir -p "$HERMES_ROOT/skills"

if [ "$MODE" = "link" ]; then
  ln -s "$SRC" "$DEST"
  echo "🔗 已软链：$DEST -> $SRC"
else
  mkdir -p "$DEST"
  cp "$SRC/SKILL.md" "$DEST/"
  cp -R "$SRC/scripts" "$DEST/"
  [ -d "$SRC/references" ] && cp -R "$SRC/references" "$DEST/"
  [ -d "$SRC/snippets" ]   && cp -R "$SRC/snippets"   "$DEST/"
  [ -f "$SRC/config.example.json" ] && cp "$SRC/config.example.json" "$DEST/"
  echo "✅ 已安装：$DEST"
fi

# 依赖检查
if ! python3 -c "import playwright" 2>/dev/null; then
  echo "⚠️  还没装 playwright：pip install -r \"$SRC/requirements.txt\" && python3 -m playwright install chromium" >&2
fi

# 规范自检
python3 "$SRC/scripts/verify_skill.py" "$SRC" || true

if ! command -v arena >/dev/null 2>&1; then
  echo "提示：想直接用 \`arena\` 命令（而不只是斜杠命令），装一次：pip install -e \"$SRC\"（或 pipx install \"$SRC\"）" >&2
fi

cat <<EOF

用法（在 Hermes 里）：
  /arena-agent 帮我调研三家竞品的定价策略
  /arena-agent --cookies ~/uploads/arena.ai_cookies.txt 你的问题

装了 CLI 的话（等价，且功能更全）：
  arena ask "帮我调研三家竞品的定价策略"
  arena cookies import ~/uploads/arena.ai_cookies.txt && arena check
  arena chat list / arena profile use work / arena doctor

首次没有 cookies 文件时（二选一）：
  1) 在浏览器登录 arena.ai → 导出 cookies（Netscape cookies.txt / EditThisCookie JSON）
  2) python3 "$SRC/scripts/arena_agent.py" login --email 你的邮箱 --headed --wait 300

验证：
  python3 "$SRC/scripts/arena_agent.py" check
EOF
