#!/bin/bash
# restart_merlin.sh — Pi 5 process manager for Merlin
#
# Usage:
#   Restart:  ssh pi@100.87.156.70 'bash /home/pi/RBOS/merlin/restart_merlin.sh'
#   Status:   ssh pi@100.87.156.70 'bash /home/pi/RBOS/merlin/restart_merlin.sh status'
#
# Kills all existing Merlin python processes, then starts:
#   1. tracker_pi.py       — face tracking + idle behavior
#   2. merlin_pi_client.py — audio capture/playback, brain server comms
#
# All processes run from /home/pi/RBOS/merlin/ to fix the "wrong directory" bug.
# Logs: /tmp/merlin-tracker.log  /tmp/merlin-client.log

MERLIN_DIR="/home/pi/RBOS/merlin"
VENV_PYTHON="$MERLIN_DIR/venv/bin/python3"
SYSTEM_PYTHON="/usr/bin/python3"
LOG_DIR="/tmp"

TRACKER_SCRIPT="tracker_pi.py"
CLIENT_SCRIPT="merlin_pi_client.py"

# ── status subcommand ──────────────────────────────────────────────────────────

do_status() {
    echo ""
    echo "=== Merlin Process Status ==="
    local all_running=true

    for script in "$TRACKER_SCRIPT" "$CLIENT_SCRIPT"; do
        local pid
        pid=$(pgrep -f "$script" 2>/dev/null | head -1 || true)
        if [ -n "$pid" ]; then
            echo "  [RUNNING]  $script  (PID $pid)"
        else
            echo "  [STOPPED]  $script"
            all_running=false
        fi
    done

    echo ""
    echo "Logs:"
    echo "  tail -f /tmp/merlin-tracker.log"
    echo "  tail -f /tmp/merlin-client.log"
    echo ""

    if $all_running; then
        echo "All processes running."
        return 0
    else
        echo "One or more processes stopped."
        return 1
    fi
}

if [ "${1:-}" = "status" ]; then
    do_status
    exit $?
fi

# ── Python resolver ────────────────────────────────────────────────────────────

if [ -x "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
    echo "[merlin] Using venv python: $VENV_PYTHON"
else
    PYTHON="$SYSTEM_PYTHON"
    echo "[merlin] Venv not found, using system python: $SYSTEM_PYTHON"
fi

# ── Phase 1: Kill existing processes ──────────────────────────────────────────

echo ""
echo "=== Stopping existing Merlin processes ==="

for script in "$TRACKER_SCRIPT" "$CLIENT_SCRIPT"; do
    if pgrep -f "$script" > /dev/null 2>&1; then
        echo "  Killing $script ..."
        pkill -f "$script" 2>/dev/null || true
    else
        echo "  $script not running"
    fi
done

echo "  Waiting 2s for clean shutdown..."
sleep 2

# Force kill any stragglers
for script in "$TRACKER_SCRIPT" "$CLIENT_SCRIPT"; do
    if pgrep -f "$script" > /dev/null 2>&1; then
        echo "  Force killing $script ..."
        pkill -9 -f "$script" 2>/dev/null || true
    fi
done

# ── Phase 2: Start processes ───────────────────────────────────────────────────

echo ""
echo "=== Starting Merlin processes ==="
echo "  Working directory: $MERLIN_DIR"

start_process() {
    local script="$1"
    local logname="$2"
    local logfile="$LOG_DIR/merlin-${logname}.log"

    # Append restart marker to log
    echo "" >> "$logfile"
    echo "=== restart_merlin.sh — $(date) ===" >> "$logfile"

    # Launch from MERLIN_DIR so relative imports resolve correctly
    cd "$MERLIN_DIR"
    nohup "$PYTHON" -u "$MERLIN_DIR/$script" >> "$logfile" 2>&1 &
    local pid=$!

    sleep 1

    if kill -0 "$pid" 2>/dev/null; then
        echo "  [OK]   $script  (PID $pid, log: $logfile)"
    else
        echo "  [FAIL] $script  (died immediately — check $logfile)"
        tail -5 "$logfile" 2>/dev/null || true
    fi
}

start_process "$TRACKER_SCRIPT" "tracker"
start_process "$CLIENT_SCRIPT"  "client"

# ── Phase 3: Status summary ────────────────────────────────────────────────────

do_status
