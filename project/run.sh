#!/usr/bin/env bash
# Launch the Returns Optimization Engine web app on the Continuum venv.
#
#   ./run.sh           # serve on http://127.0.0.1:8099
#   PORT=9000 ./run.sh # custom port
#
# Then open the URL and click a scenario chip.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$HERE/../continuum/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Continuum venv not found at: $VENV_PY" >&2
  echo "Expected the framework at ../continuum with a .venv. Adjust the path if needed." >&2
  exit 1
fi

cd "$HERE"
PORT="${PORT:-8099}"

# Ensure the Continuum framework (import root: ``orchestrator``) is importable.
# The venv's editable install can record a stale absolute path if the repo was
# moved/copied; prepending the real local source makes the import robust here.
CONT_SRC="$HERE/../continuum/src"
if [[ -d "$CONT_SRC" ]]; then
  export PYTHONPATH="$CONT_SRC${PYTHONPATH:+:$PYTHONPATH}"
fi

echo "Returns Optimization Engine → http://127.0.0.1:${PORT}"
echo "(first start seeds long-term memory for the demo customers — ~1 min)"
exec "$VENV_PY" -m uvicorn app.api:app --host 127.0.0.1 --port "$PORT"
