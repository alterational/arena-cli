#!/usr/bin/env bash
# Run all self-tests: engine + pure JS route + Hermes skill entry point
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== 1/8 SKILL.md spec self-check ==="; python3 scripts/verify_skill.py
echo "=== 2/8 Engine and CLI (mock) ==="; python3 tests/run_tests.py
echo "=== 3/8 Pure JS route (mock) ==="; python3 tests/run_js_tests.py
echo "=== 4/8 Hermes skill entry point + install ==="; python3 tests/run_entry_tests.py
echo "=== 5/8 arena CLI (command system) ==="; python3 tests/run_cli_tests.py
echo "=== 6/8 Offline safety and tab tests ==="; python3 -m unittest discover -s tests -p test_recovery.py
echo "=== 7/8 Browser follow-up and origin tests ==="; python3 tests/run_browser_regressions.py
echo "=== 8/8 Interactive terminal tests ==="; python3 tests/run_tui_tests.py
