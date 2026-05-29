#!/usr/bin/env bash
# Run the headless end-to-end demo (no web server). Walks three returns through
# the full multi-agent pipeline and prints each decision.
#
#   ./run_cli.sh            # all three scenarios
#   ./run_cli.sh frank      # just the fraud / human-approval scenario
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$HERE/../continuum/.venv/bin/python"
cd "$HERE"
exec "$VENV_PY" run_demo.py ${1:+--scenario "$1"}
