Edit config.cfg to match your setup. chown +x on scripts. Start steam_wfb.py first and check for initialization and data. Start fpv.sh.
fpv.sh have one pipeline with video only, and one with audio. I will add argument to chose betweeen them later, different steam shortcuts can be created.
Prepared for bidirectional tunnel, but not tested. Requires drivers and tx compatible wlan.

Using aggregation and distributor options, compatible with openwrt and remote nodes, as well as local interfaces (add to config.cfg). final_cleanup.sh need to remove and readd the built in driver to work, if you are using Steamdeck OLED, you need to add your driver name to have network restored after FPV.
Requires NOPASSWD sudo for tunnel to work and for wlan adapters to be initialized correctly. (Only do it if you understand it):
echo "%wheel ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/wheel >/dev/null


TODO:
 - fix steam_wfb-ng console size, use xterm instead of konsole? "konsole -p 'TerminalColumns=100' -p 'TerminalRows=42' -geometry +0-30" vs "xterm -geometry 100x44+0-30."OK
  - Finish up tunnel TX side
  - Only ONE TX allowed to avoid messy RSSI choice implementation.
  - Able to select either local or remote injector from config, if IP-address == remote, if empty == no tunnel, if wlanX == wlan_init.sh tx
  - One-click installer  OK for now
 - Driver installs must be done by user on a need basis, but simplify? Ncurses menu selection?
 - Create steamdeck links: https://github.com/sonic2kk/steamtinkerlaunch OK
 - Create fpv.sh links for different pipelines (with script arguments) video/video-record/video-audio/video-audio-record. User can remove them if needed. OK
 - Add some nice pictures and names for the "Game" entries
 -Divide top part and make some ASCII graphs (RSSI?) with link health colors. OK
- Ground side msposd rendering? (when tx works)
 - Not sure how steamdeck gamescope will handle cairo overlay on top of gstreamer
- Write a better readme
add fec_rec and lost in graphs

add wlan reconnect in case the adapter is pulled out or losing connection. reconnect every 3s? OK

add a config script to ask for wlan choice or continue with current. maybe additional choice for some more? mini configurator from game mode

tiny up antenna graphs, add graphs for mbit in/out?
