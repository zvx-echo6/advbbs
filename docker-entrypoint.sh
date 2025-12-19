#!/bin/bash
# advBBS Docker Entrypoint
# Runs ttyd for web config access and the BBS
#
# Environment variables for initial config (only used on first run):
#   ADVBBS_NAME          - BBS name (default: advBBS)
#   ADVBBS_CALLSIGN      - BBS callsign (default: ADVBBS)
#   ADVBBS_ADMIN_PASS    - Admin password (default: changeme)
#   ADVBBS_MOTD          - Message of the day
#   TZ                   - Timezone (default: UTC)
#   ADVBBS_CONNECTION    - Meshtastic connection: serial, tcp, ble (default: serial)
#   ADVBBS_SERIAL_PORT   - Serial port (default: /dev/ttyUSB0)
#   ADVBBS_TCP_HOST      - TCP host for Meshtastic (default: localhost)
#   ADVBBS_TCP_PORT      - TCP port for Meshtastic (default: 4403)
#   ADVBBS_MODE          - Operating mode: full, mail_only, boards_only, repeater (default: full)

export advBBS_CONFIG="/data/config.toml"
export TERM="${TERM:-xterm-256color}"

# First run - no config exists, create defaults from env vars
if [ ! -f "$advBBS_CONFIG" ]; then
    mkdir -p /data
    cat > "$advBBS_CONFIG" << EOF
[bbs]
name = "${ADVBBS_NAME:-advBBS}"
callsign = "${ADVBBS_CALLSIGN:-ADVBBS}"
admin_password = "${ADVBBS_ADMIN_PASS:-changeme}"
motd = "${ADVBBS_MOTD:-Welcome to advBBS!}"
timezone = "${TZ:-UTC}"

[database]
path = "/data/advbbs.db"
backup_path = "/data/backups"

[meshtastic]
connection_type = "${ADVBBS_CONNECTION:-serial}"
serial_port = "${ADVBBS_SERIAL_PORT:-/dev/ttyUSB0}"
tcp_host = "${ADVBBS_TCP_HOST:-localhost}"
tcp_port = ${ADVBBS_TCP_PORT:-4403}

[features]
mail_enabled = true
boards_enabled = true
sync_enabled = true
registration_enabled = true

[operating_mode]
mode = "${ADVBBS_MODE:-full}"

[logging]
level = "${ADVBBS_LOG_LEVEL:-INFO}"
EOF
    echo "Default config created from environment variables."
    echo "Configure via http://localhost:7681"
fi

# Start ttyd for web-based config access
echo "Starting web config interface on port 7681..."
ttyd -W -p 7681 \
    -t titleFixed="advBBS Config" \
    -t 'theme={"background":"#0d1117","foreground":"#c9d1d9","cursor":"#58a6ff","selectionBackground":"#388bfd"}' \
    -t fontSize=14 \
    /bin/bash -c 'while true; do python3 -m advbbs --config "$advBBS_CONFIG" config; sleep 1; done' &

# Keep ttyd running even if BBS fails
trap "kill %1 2>/dev/null; kill %2 2>/dev/null" EXIT

# Restart watcher - monitors for restart signal from config tool
(
    while true; do
        if [ -f /tmp/advbbs_restart ]; then
            rm -f /tmp/advbbs_restart
            echo "Restart signal received, restarting BBS..."
            pkill -f "python.*advbbs.*config" --signal 0 2>/dev/null || true  # Don't kill config tool
            pkill -f "python -m advbbs --config" || true
            sleep 1
        fi
        sleep 2
    done
) &

# Start the BBS in a loop - retry on failure
echo "Starting advBBS..."
while true; do
    python -m advbbs --config "$advBBS_CONFIG" || true
    echo "BBS exited. Check config at http://localhost:7681. Retrying in 5s..."
    sleep 5
done
