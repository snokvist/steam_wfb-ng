#!/bin/bash

CONFIG_FILE="config.cfg"

# Function to read values from config file
get_config_value() {
    local section=$1
    local key=$2
    sed -n -e "/\[$section\]/,/^\[/p" "$CONFIG_FILE" | sed -n -e "/^$key[[:space:]]*=/s/^$key[[:space:]]*=[[:space:]]*//p"
}

# Graceful exit handling
cleanup() {
    echo "Cleaning up processes..."
    sudo pkill -f ./steam_wfb.py
    exit 0
}

trap cleanup SIGINT SIGTERM

# Launch Konsole with menu_selector.py
konsole -p 'TerminalColumns=170' -p 'TerminalRows=51' -e ./menu_selector.py
#./menu_selector.py

# Launch Konsole with steam_wfb.py
sudo konsole -p 'TerminalColumns=170' -p 'TerminalRows=51' -e ./steam_wfb.py &
#sudo ./steam_wfb.py &

# Read daemon and gst_pipeline from the config file
daemon=$(get_config_value common daemon)
gst_pipeline=$(get_config_value common gst_pipeline)

# Check if daemon is true
if [[ "$daemon" == "true" ]]; then
    # Execute fpv.sh with gst_pipeline as argument
    ./fpv.sh "$gst_pipeline"
else
    # Wait for steam_wfb.py to exit
    steam_pid=$(pgrep -f ./steam_wfb.py)
    if [[ -n "$steam_pid" ]]; then
        wait "$steam_pid"
    fi
fi

exit 0

