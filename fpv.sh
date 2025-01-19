#!/bin/bash

echo "start: $(date)" >> /tmp/fpv.log

onExit() {
    echo "stop: $(date)" >> /tmp/fpv.log

    #sudo modprobe -r rtw88_8822ce

    #sudo modprobe rtw88_8822ce
}

trap "onExit" EXIT
trap "exit 2" HUP INT QUIT TERM


#Restart gstreamer if it crashes.
while true
do
#Only video
gst-launch-1.0 udpsrc port=5600 ! queue max-size-time=1 ! application/x-rtp,payload=97, clock-rate=90000, encoding-name=H265 ! rtpjitterbuffer latency=1 ! rtph265depay ! vaapih265dec ! fpsdisplaysink video-sink=xvimagesink sync=true

#Vido + Audio
#gst-launch-1.0 udpsrc port=5600 ! tee name=t t. ! queue max-size-time=1 ! application/x-rtp,payload=97, clock-rate=90000, encoding-name=H265 ! rtpjitterbuffer latency=1 ! rtph265depay ! vaapih265dec ! xvimagesink sync=false async=false t. ! queue leaky=1 ! application/x-rtp, payload=98, encoding-name=OPUS ! rtpjitterbuffer latency=1 ! rtpopusdepay ! opusdec ! audioconvert ! audioresample ! alsasink sync=false async=false

echo "GST crashed with exit code:$?" >> /tmp/fpv.log
sleep 1
done
