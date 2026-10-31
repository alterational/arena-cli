#!/usr/bin/env bash
# 一键跑全部自测：引擎 + 纯 JS 路线 + Hermes 技能入口
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== 1/5 SKILL.md 规范自检 ==="; python3 scripts/verify_skill.py
echo "=== 2/5 引擎与 CLI（mock） ==="; python3 tests/run_tests.py
echo "=== 3/5 纯 JS 路线（mock） ==="; python3 tests/run_js_tests.py
echo "=== 4/5 Hermes 技能入口 + 安装 ==="; python3 tests/run_entry_tests.py
echo "=== 5/5 arena CLI（命令体系） ==="; python3 tests/run_cli_tests.py
