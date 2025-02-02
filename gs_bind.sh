#!/bin/bash

echo "Calling wlan_init.sh"
./wlan_init.sh "$1" 100 165 US HT20 bind &
sleep 3
#do stuff $2 =  bind_data_folder
echo "Starting SEND: "
./connect.py --bind $2
#./connect.py --info
./final_cleanup.sh
exit 0
