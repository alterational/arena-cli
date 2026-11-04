#!/usr/bin/env bash
# End-to-end self-test: local mock site; no real arena account required
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tests/run_tests.py
