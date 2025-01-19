Edit config.cfg to match your setup. chown +x on scripts. Start steam_wfb.py first and check for initialization and data. Start fpv.sh.
fpv.sh have one pipeline with video only, and one with audio. I will add argument to chose betweeen them later, different steam shortcuts can be created.

Using aggregation and distributor options, compatible with openwrt and remote nodes, as well as local interfaces (add to config.cfg). final_cleanup.sh need to remove and readd the built in driver to work, if you are using Steamdeck OLED, you need to add your driver name to have network restored after FPV.
Requires NOPASSWD sudo (Only do it if you understand it):
echo "%wheel ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/wheel >/dev/null

One-click installer will come later.

Steam shortcuts:
/bin/konsole
/home/deck/steam_wfb-ng/
--profile steam_wfb -e ./steam_wfb.py

"/home/deck/steam_wfb-ng/fpv.sh"
/home/deck/steam_wfb-ng/
