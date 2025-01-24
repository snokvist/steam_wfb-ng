#!/bin/bash

# Parse the INI file
config_file="config.cfg"

# Function to retrieve a value from the INI file
get_config_value() {
    local section="$1"
    local key="$2"
    awk -F "=" "/^\[$section\]/ {found=1} found && \$1 ~ \"^$key\" {gsub(/[ \t]+/, \"\", \$2); print \$2; exit}" "$config_file"
}

# Extract necessary values from the config
daemon=$(get_config_value "common" "daemon")
gst_pipeline=$(get_config_value "common" "gst_pipeline")

# Launch the required terminals
sudo konsole -p 'TerminalColumns=170' -p 'TerminalRows=51' -e ./menu_selector.py
sudo konsole -p 'TerminalColumns=170' -p 'TerminalRows=51' -e ./steam_wfb.py &

# If [common] and daemon = 1, execute ./fpv.sh with gst_pipeline as the argument
if [[ "$daemon" == "true" ]]; then
    ./fpv.sh "$gst_pipeline"
else
    # If daemon = 0, wait for ./steam_wfb.py to exit
    wait
fi

exit 0

