#!/bin/bash
# advBBS Docker Entrypoint
# Runs ttyd for web config access and the BBS

export advBBS_CONFIG="/data/config.toml"
export TERM="${TERM:-xterm-256color}"

# First run - no config exists, create defaults
if [ ! -f "$advBBS_CONFIG" ]; then
    mkdir -p /data
    cat > "$advBBS_CONFIG" << 'EOF'
[bbs]
name = "advBBS"
callsign = "FQ51"
admin_password = "changeme"
motd = "Welcome to advBBS!"

[database]
path = "/data/advbbs.db"
backup_path = "/data/backups"

[meshtastic]
connection_type = "tcp"
tcp_host = "localhost"
tcp_port = 4403

[features]
mail_enabled = true
boards_enabled = true
sync_enabled = true
registration_enabled = true

[operating_mode]
mode = "full"

[logging]
level = "INFO"
EOF
    echo "Default config created. Configure via http://localhost:7681"
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
