#!/usr/bin/env bash
# Single reliable way to start the HealthCross server - always run this
# script itself (double-click it, or `./start_healthcross.sh` from any
# terminal) rather than typing `cd`/`uvicorn` by hand. It finds its own
# folder, activates the venv that lives next to it, frees up port 8000 if
# something is already using it, and auto-restarts the server if it ever
# crashes instead of leaving you stuck on a dead connection.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR" || { echo "Could not cd into $SCRIPT_DIR"; exit 1; }
echo "Working directory: $(pwd)"

if [ ! -d ".venv" ]; then
  echo "ERROR: no .venv folder found here. This script must live inside the"
  echo "same HealthCross folder as .venv (the correct project clone), not"
  echo "a stray copy elsewhere (e.g. Downloads/Claude Documents/HealthCross)."
  exit 1
fi

# shellcheck disable=SC1091
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate

# Best-effort: free up port 8000 if a previous/stale server is still
# holding it, so we always connect to a freshly-started process rather
# than an old one from a different folder or a crashed leftover.
if command -v netstat >/dev/null 2>&1; then
  OLD_PID="$(netstat -ano 2>/dev/null | grep ':8000' | grep LISTENING | awk '{print $NF}' | head -1)"
  if [ -n "${OLD_PID:-}" ]; then
    echo "Port 8000 is already in use by PID $OLD_PID - stopping it first."
    taskkill //F //PID "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
fi

echo "Starting HealthCross server - press Ctrl+C twice to stop for good."
while true; do
  uvicorn app.main:app --reload
  echo ""
  echo "Server stopped unexpectedly - restarting in 3 seconds (Ctrl+C now to quit instead)..."
  sleep 3
done
