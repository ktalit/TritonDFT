#!/usr/bin/env bash
set -euo pipefail

PORT="${TRITONDFT_DASHBOARD_PORT:-8008}"
PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"

if [[ -z "$PIDS" ]]; then
    echo "No TritonDFT dashboard is running on port ${PORT}."
    exit 0
fi

stopped=0
while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    owner="$(ps -p "$pid" -o user= 2>/dev/null | xargs || true)"
    current_user="$(id -un)"

    if [[ "$owner" != "$current_user" ]]; then
        echo "Refusing to stop PID ${pid}: it is owned by ${owner:-another user}." >&2
        continue
    fi
    if [[ "$command" != *"browser_dashboard.py"* ]]; then
        echo "Refusing to stop PID ${pid}: port ${PORT} is not owned by TritonDFT browser_dashboard.py." >&2
        continue
    fi

    kill "$pid"
    echo "Stopped TritonDFT dashboard PID ${pid} on port ${PORT}."
    stopped=1
done <<< "$PIDS"

if [[ "$stopped" -eq 0 ]]; then
    exit 1
fi

echo "Submitted Slurm jobs were not cancelled. You may now close the browser tab."
