#!/usr/bin/env bash
set -emb

export LC_ALL=C

# Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
  echo "This script requires root privileges. Switching to root..."
  exec sudo bash "$0" "$@"
fi

# Validate arguments (now expecting 6 total)
if [ "$#" -ne 6 ]; then
  echo "Usage: $0 <wlan_interface> <tx_power> <channel> <region> <bandwidth> <mode>"
  exit 1
fi

WLAN_INTERFACE="$1"
TX_POWER="$2"
CHANNEL="$3"
REGION="$4"
BANDWIDTH="$5"
MODE="$6"

_cleanup() {
  plist=$(jobs -p)
  if [ -n "$plist" ]; then
    kill -TERM $plist || true
  fi

  # Reset interface to managed mode
  ip link set dev "$WLAN_INTERFACE" down
  iw "$WLAN_INTERFACE" set type managed
  ip link set dev "$WLAN_INTERFACE" up

  exit 1
}

trap _cleanup EXIT

# --- General Setup ---

iw reg set "$REGION"

if command -v nmcli >/dev/null && ! nmcli device show "$WLAN_INTERFACE" | grep -q '(unmanaged)'; then
  nmcli device set "$WLAN_INTERFACE" managed no
  sleep 1
fi

ip link set "$WLAN_INTERFACE" down
iw dev "$WLAN_INTERFACE" set monitor otherbss
ip link set "$WLAN_INTERFACE" up

echo "General interface init done"

# For demonstration, keep all old "rx" logic under `MODE=rx`:
if [ "$MODE" = "rx" ]; then
  echo "Configuring '$WLAN_INTERFACE' for RX mode..."
  iw dev "$WLAN_INTERFACE" set channel "$CHANNEL" "$BANDWIDTH"


  # Start wfb_rx
  wfb_rx -f -c 127.0.0.1 -u 10000 -p 0  -i 7669206 -R 2097152 "$WLAN_INTERFACE" &
  sleep 2
  echo "WFB-ng init done (RX mode)"
  wait -n

elif [ "$MODE" = "tx" ]; then
  # Placeholder for future TX logic
  echo "Configuring '$WLAN_INTERFACE' for TX mode..."
  iw dev "$WLAN_INTERFACE" set channel "$CHANNEL" "$BANDWIDTH"
    wfb_rx -f -c 127.0.0.1 -u 10001 -p 32 -i 7669206 -R 2097152 "$WLAN_INTERFACE" &
  wfb_tx -I 11001 -R 2097152  "$WLAN_INTERFACE" &
  iw dev "$WLAN_INTERFACE" set txpower fixed "$TX_POWER"
  sleep 2
  echo "Done (TX mode)."
  wait -n

elif [ "$MODE" = "rx-tx" ]; then
  # Placeholder for combined RX-TX logic
  echo "Configuring '$WLAN_INTERFACE' for RX + TX mode..."
  iw dev "$WLAN_INTERFACE" set channel "$CHANNEL" "$BANDWIDTH"
  wfb_rx -f -c 127.0.0.1 -u 10000 -p 0  -i 7669206 -R 2097152 "$WLAN_INTERFACE" &
  wfb_rx -f -c 127.0.0.1 -u 10001 -p 32 -i 7669206 -R 2097152 "$WLAN_INTERFACE" &
  wfb_tx -I 11001 -R 2097152  "$WLAN_INTERFACE" &
  iw dev "$WLAN_INTERFACE" set txpower fixed "$TX_POWER"
  sleep 2
  echo "Done (RX-TX mode)."
  wait -n

fi
