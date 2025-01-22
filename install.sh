#!/bin/bash

# Ensure the script is run as root
if [[ $EUID -ne 0 ]]; then
  echo "This script must be run as root. Please use sudo or log in as root."
  exit 1
fi

# Interactive user feedback
read -p "This script will disable the Steam Deck's read-only filesystem and add your user to the sudoers NOPASSWD group. This increases system flexibility but may reduce security. Continue? (y/n): " proceed
if [[ "$proceed" != "y" ]]; then
  echo "Aborting installation."
  exit 0
fi

# Disable read-only filesystem
echo "Disabling read-only filesystem..."
if steamos-readonly disable; then
  echo "Filesystem set to mutable."
else
  echo "Failed to disable read-only filesystem. Exiting."
  exit 1
fi

# Add user to sudoers NOPASSWD if not already set
if ! grep -q "%wheel ALL=(ALL) NOPASSWD:ALL" /etc/sudoers.d/wheel 2>/dev/null; then
  echo "Adding %wheel to sudoers NOPASSWD..."
  echo "%wheel ALL=(ALL) NOPASSWD:ALL" | tee /etc/sudoers.d/wheel >/dev/null
else
  echo "NOPASSWD for %wheel is already configured."
fi

# Install necessary system applications
echo "Installing necessary applications..."
apts=(python net-tools git gawk make xdotool vim yad libsodium-dev libpcap-dev)
if pacman -S --noconfirm "${apts[@]}"; then
  echo "Applications installed successfully."
else
  echo "Failed to install necessary applications. Exiting."
  exit 1
fi

# Make necessary files executable
echo "Making application files executable..."
chmod +x terminal.sh final_cleanup.sh fpv.sh steam_wfb.py wfb_keygen wfb_rx wfb_tun wfb_tx wfb_tx_cmd wlan_init.sh
if [[ $? -eq 0 ]]; then
  echo "Files made executable successfully."
else
  echo "Failed to make some files executable. Exiting."
  exit 1
fi

# Ask user whether to install Steam shortcuts
read -p "Do you want to install Steam shortcuts for the application? (y/n): " install_shortcuts
if [[ "$install_shortcuts" == "y" ]]; then
  # Clone SteamTinkerLaunch
  echo "Cloning SteamTinkerLaunch..."
  if ! sudo -u deck git clone https://github.com/sonic2kk/steamtinkerlaunch.git; then
    echo "Failed to clone SteamTinkerLaunch repository. Exiting."
    exit 1
  fi

  # Install SteamTinkerLaunch
  cd steamtinkerlaunch || { echo "Failed to enter SteamTinkerLaunch directory. Exiting."; exit 1; }
  echo "Installing SteamTinkerLaunch system-wide..."
  if make install; then
    echo "SteamTinkerLaunch installed system-wide."
  else
    echo "Failed to install SteamTinkerLaunch system-wide. Exiting."
    exit 1
  fi
  sudo -u deck steamtinkerlaunch compat add >/dev/null

  # Set language to English automatically
  echo "Setting language to English..."
  sudo -u deck ./steamtinkerlaunch lang=lang/english.txt
  sudo -u deck mkdir -p ~/.config/steamtinkerlaunch/lang
  sudo -u deck cp lang/english.txt ~/.config/steamtinkerlaunch/lang/

  # Add shortcuts using SteamTinkerLaunch
  cd /home/deck/steam_wfb-ng || { echo "Failed to return to /home/deck/steam_wfb-ng. Exiting."; exit 1; }

  # Ensure Steam is closed before adding shortcuts
  echo "Ensuring Steam is closed..."
  killall -q steam && echo "Steam closed."

  sleep 2

  echo "Adding Steam shortcuts..."
  sudo -u deck steamtinkerlaunch addnonsteamgame --appname="Steam WFB_NG Terminal" --exepath="/home/deck/steam_wfb-ng/terminal.sh" --launchoptions="" >/dev/null
  sudo -u deck steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video only" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video" >/dev/null
  sudo -u deck steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Record" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+record" >/dev/null
  sudo -u deck steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Audio" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+audio" >/dev/null
  sudo -u deck steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Audio+Record" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+audio+record" >/dev/null

  sleep 2

  # Restart Steam
  echo "Restarting Steam..."
  sudo -u deck steam &
fi

# Final instructions
echo -e "\nSetup complete! Please restart your Steam Deck to apply sudoers changes."
echo -e "\nChange config.cfg to match your system requirements."
