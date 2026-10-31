#!/usr/bin/env bash
# 端到端自测：本地 mock 站点，不需要真实 arena 账号
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tests/run_tests.py
