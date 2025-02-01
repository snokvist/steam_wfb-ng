#!/bin/bash

sudo killall -q wfb_tun
sudo killall -q wfb_tx
sudo killall -q wfb_rx
sudo rmmod rtw88_8822ce
sudo rmmod 88XXau_wfb
sudo modprobe 88XXau_wfb
sudo modprobe rtw88_8822ce
sleep 1
sudo systemctl restart NetworkManager

exit 0
