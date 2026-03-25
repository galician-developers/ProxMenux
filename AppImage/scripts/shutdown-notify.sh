#!/bin/bash
# ProxMenux Monitor - Shutdown Notification Script
# This script is called by systemd ExecStop before the service terminates.
# It sends a shutdown/reboot notification via the running Flask server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="/var/lib/proxmenux"
CONFIG_FILE="$CONFIG_DIR/config.json"
LOG_FILE="/var/log/proxmenux-shutdown.log"
PORT="${PORT:-5000}"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE" 2>/dev/null || true
}

log "=== Shutdown notification script started ==="

# Determine if this is a reboot or shutdown
# Check for systemd targets or runlevel
is_reboot=false
if systemctl is-active --quiet reboot.target 2>/dev/null; then
    is_reboot=true
    log "Detected: reboot.target is active"
elif [ -f /run/systemd/shutdown/scheduled ]; then
    if grep -q "reboot" /run/systemd/shutdown/scheduled 2>/dev/null; then
        is_reboot=true
        log "Detected: /run/systemd/shutdown/scheduled contains 'reboot'"
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
log "Event type: $event_type, Hostname: $hostname"

# Try to send notification via internal API endpoint
# The Flask server may still be running at this point
log "Sending notification to http://127.0.0.1:$PORT/api/internal/shutdown-event"

response=$(curl -s -w "\n%{http_code}" -X POST "http://127.0.0.1:$PORT/api/internal/shutdown-event" \
    -H "Content-Type: application/json" \
    -d "{\"event_type\": \"$event_type\", \"hostname\": \"$hostname\", \"reason\": \"$reason\"}" \
    --max-time 10 2>&1)

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | head -n -1)

log "Response HTTP code: $http_code"
log "Response body: $body"

if [ "$http_code" = "200" ]; then
    log "Notification sent successfully"
else
    log "WARNING: Notification may have failed (HTTP $http_code)"
fi

# Give the notification a moment to be sent
log "Waiting 3 seconds for notification delivery..."
sleep 3

# Now terminate the Flask process
# Find the main process and send SIGTERM
log "Terminating Flask process..."
pkill -TERM -f "flask_server" 2>/dev/null || true

log "=== Shutdown notification script completed ==="
exit 0
