#!/bin/bash

sudo rmmod rtw88_8822ce
sudo modprobe rtw88_8822ce
sleep 2

sudo systemctl restart NetworkManager

exit 0
