#!/bin/bash
# ProxMenux Monitor - Shutdown Notification Script
# This script is called by systemd ExecStop before the service terminates.
# It sends a shutdown/reboot notification via the running Flask server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="/var/lib/proxmenux"
CONFIG_FILE="$CONFIG_DIR/config.json"
PORT="${PORT:-5000}"

# Determine if this is a reboot or shutdown
# Check for systemd targets or runlevel
is_reboot=false
if systemctl is-active --quiet reboot.target 2>/dev/null; then
    is_reboot=true
elif [ -f /run/systemd/shutdown/scheduled ]; then
    if grep -q "reboot" /run/systemd/shutdown/scheduled 2>/dev/null; then
        is_reboot=true
    fi
fi

# Build the event type and message
if [ "$is_reboot" = true ]; then
    event_type="system_reboot"
    reason="The system is rebooting."
else
    event_type="system_shutdown"
    reason="The system is shutting down."
fi

hostname=$(hostname)

# Try to send notification via internal API endpoint
# The Flask server may still be running at this point
curl -s -X POST "http://127.0.0.1:$PORT/api/internal/shutdown-event" \
    -H "Content-Type: application/json" \
    -d "{\"event_type\": \"$event_type\", \"hostname\": \"$hostname\", \"reason\": \"$reason\"}" \
    --max-time 5 2>/dev/null || true

# Give the notification a moment to be sent
sleep 2

# Now terminate the Flask process
# Find the main process and send SIGTERM
pkill -TERM -f "proxmenux-monitor" 2>/dev/null || true

exit 0
