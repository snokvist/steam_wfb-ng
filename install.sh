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
apts=(python3 build-essential libncurses-dev wireless-tools net-tools git awk make pgrep xdotool xprop xrandr xxd xwininfo yad)
if apt update && apt install -y "${apts[@]}"; then
  echo "Applications installed successfully."
else
  echo "Failed to install necessary applications. Exiting."
  exit 1
fi

# Clone application files
cd /home/deck || { echo "Failed to change to /home/deck. Exiting."; exit 1; }
if ! git clone https://github.com/snokvist/steam_wfb-ng.git; then
  echo "Failed to clone repository. Exiting."
  exit 1
fi
cd /home/deck/steam_wfb-ng || { echo "Failed to enter application directory. Exiting."; exit 1; }

# Interactive configuration
echo "Configuring application..."
iw dev
interfaces=( "none" $(iw dev | grep Interface | awk '{print $2}') )
echo "Available WiFi interfaces (select 'none' to leave empty):"
select wlan in "${interfaces[@]}"; do
  if [[ "$wlan" == "none" ]]; then
    wlan=""
    echo "No WiFi interface selected."
    break
  elif [[ -n "$wlan" ]]; then
    echo "Selected interface: $wlan"
    break
  else
    echo "Invalid selection. Try again."
  fi
done

read -p "Choose WiFi region (US/BO/00 or enter a valid custom region): " region
read -p "Choose bandwidth (20/40 MHz): " bandwidth

# Available channels
echo "Available channels based on region $region (ensure this is valid):"
echo -e "5745 MHz [149]\n5765 MHz [153]\n5785 MHz [157]\n5805 MHz [161]\n5825 MHz [165]"
read -p "Enter your desired channel (or custom, ensure it is valid): " channel

# Update config.cfg
echo "Updating config.cfg..."
sed -i "s/^ip_address.*/ip_address=127.0.0.1/" config.cfg
sed -i "s/^region.*/region=$region/" config.cfg
sed -i "s/^bandwidth.*/bandwidth=$bandwidth/" config.cfg
sed -i "s/^channel.*/channel=$channel/" config.cfg
sed -i "s/^rx_wlans.*/rx_wlans=$wlan/" config.cfg

# Generate key pair
echo "Generating key pair..."
if ./wfb_keygen; then
  echo "Key pair generated."
else
  echo "Failed to generate key pair. Exiting."
  exit 1
fi

# Clone SteamTinkerLaunch
echo "Cloning SteamTinkerLaunch..."
if ! git clone https://github.com/sonic2kk/steamtinkerlaunch.git; then
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
steamtinkerlaunch compat add

# Set language to English automatically
echo "Setting language to English..."
./steamtinkerlaunch lang=lang/english.txt
mkdir -p ~/.config/steamtinkerlaunch/lang
cp lang/english.txt ~/.config/steamtinkerlaunch/lang/

# Add shortcuts using SteamTinkerLaunch
cd /home/deck/steam_wfb-ng || { echo "Failed to return to /home/deck/steam_wfb-ng. Exiting."; exit 1; }

# Ensure Steam is closed before adding shortcuts
echo "Ensuring Steam is closed..."
killall -q steam && echo "Steam closed."

echo "Adding Steam shortcuts for fpv.sh..."
steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video"
steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Record" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+record"
steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Audio" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+audio"
steamtinkerlaunch addnonsteamgame --appname="OpenIPC + WFB_NG Video+Audio+Record" --exepath="/home/deck/steam_wfb-ng/fpv.sh" --launchoptions="video+audio+record"

# Add shortcut for steam_wfb.py in terminal
steamtinkerlaunch addnonsteamgame --appname="Steam WFB_NG Terminal" --exepath="/bin/konsole" --startdir="/home/deck/steam_wfb-ng/" --launchoptions="-p 'TerminalColumns=100' -p 'TerminalRows=42' -e ./steam_wfb.py"

# Restart Steam
echo "Restarting Steam..."
steam &

# Final instructions
echo -e "\nSetup complete! Please restart your Steam Deck to apply sudoers changes."
echo -e "To configure SteamTinkerLaunch, refer to its documentation.\n"

