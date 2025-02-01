#!/bin/sh

echo "Put custom commands to be executed in this script."

# Capture the output of the command.
output=$(wfb-cli -g .common.passphrase)

# Check if the output is non-empty.
if [ -n "$output" ]; then
  echo "Passphrase retrieved: $output"
  # Run the additional command here.
  keypair $output
else
  echo "No passphrase retrieved, please check the command."
  # Optionally run another command here.
  cp etc/gs.key /etc/drone.key
fi

exit 0
