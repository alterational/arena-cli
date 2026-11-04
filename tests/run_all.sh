#!/usr/bin/env bash
# Run all self-tests: engine + pure JS route + Hermes skill entry point
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== 1/5 SKILL.md spec self-check ==="; python3 scripts/verify_skill.py
echo "=== 2/5 Engine and CLI (mock) ==="; python3 tests/run_tests.py
echo "=== 3/5 Pure JS route (mock) ==="; python3 tests/run_js_tests.py
echo "=== 4/5 Hermes skill entry point + install ==="; python3 tests/run_entry_tests.py
echo "=== 5/5 arena CLI (command system) ==="; python3 tests/run_cli_tests.py
