#!/bin/bash

###############################################################################
# fpv.sh - A script to run different GStreamer pipelines for:
#           1) video
#           2) video+record
#           3) video+audio
#           4) video+audio+record
#
# By default, if no argument is passed, it runs the VIDEO pipeline.
#
# Usage: ./fpv.sh [video|video+record|video+audio|video+audio+record]
###############################################################################

echo "start: $(date)" >> /tmp/fpv.log

onExit() {
    echo "stop: $(date)" >> /tmp/fpv.log

    # Example of reloading a kernel module if needed:
    # sudo modprobe -r rtw88_8822ce
    # sudo modprobe rtw88_8822ce
}

trap "onExit" EXIT
trap "exit 2" HUP INT QUIT TERM

# Default to "video" if no argument is passed
MODE="${1:-video}"

# Choose pipeline based on MODE
function run_pipeline() {
    case "$MODE" in

    video)
        echo "Running VIDEO pipeline"
        gst-launch-1.0 \
            udpsrc port=5600 ! \
            queue max-size-time=1 ! \
            application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
            rtpjitterbuffer latency=1 ! \
            rtph265depay ! \
            vaapih265dec ! \
            fpsdisplaysink video-sink=xvimagesink sync=true
        ;;

    video+record)
        echo "Running VIDEO+RECORD pipeline"
        gst-launch-1.0 \
            udpsrc port=5600 ! \
            tee name=videoTee \
                videoTee. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                mpegtsmux name=ts ! \
                filesink location=/home/deck/Videos/record-$(date +%y%m%d_%H%M%S).tsn sync=false \
            videoTee. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                vaapih265dec ! \
                xvimagesink sync=false async=false
        ;;

    video+audio)
        echo "Running VIDEO+AUDIO pipeline"
        gst-launch-1.0 \
            udpsrc port=5600 ! \
            tee name=t \
                t. ! queue max-size-time=1 ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=1 ! \
                rtph265depay ! \
                vaapih265dec ! \
                xvimagesink sync=false async=false \
                t. ! queue leaky=1 ! \
                application/x-rtp,payload=98,encoding-name=OPUS ! \
                rtpjitterbuffer latency=1 ! \
                rtpopusdepay ! \
                opusdec ! \
                audioconvert ! \
                audioresample ! \
                alsasink sync=false async=false
        ;;

    video+audio+record)
        echo "Running VIDEO+AUDIO+RECORD pipeline"
        gst-launch-1.0 \
            udpsrc port=5600 ! \
            tee name=t \
                \
                # Branch 1: Video decode & display
                t. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                vaapih265dec ! \
                xvimagesink sync=false async=false \
                \
                # Branch 2: Audio decode & play
                t. ! queue leaky=1 ! \
                application/x-rtp,payload=98,encoding-name=OPUS ! \
                rtpjitterbuffer latency=20 ! \
                rtpopusdepay ! \
                opusdec ! \
                audioconvert ! \
                audioresample ! \
                alsasink sync=false async=false \
                \
                # Branch 3: Video to record
                t. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                h265parse ! \
                mpegtsmux name=mux \
                ! filesink location=/home/deck/Videos/record-$(date +%y%m%d_%H%M%S).tsn sync=false \
                \
                # Branch 4: Audio to record
                t. ! queue ! \
                application/x-rtp,payload=98,encoding-name=OPUS ! \
                rtpjitterbuffer latency=20 ! \
                rtpopusdepay ! \
                opusparse ! \
                mux.
        ;;

    *)
        echo "Invalid mode: '$MODE'"
        echo "Usage: $0 [video|video+record|video+audio|video+audio+record]"
        exit 1
        ;;
    esac
}

# Main loop - Restart gst-launch if it crashes
while true; do
    run_pipeline
    EXIT_CODE=$?
    echo "GST crashed with exit code: $EXIT_CODE" >> /tmp/fpv.log
    sleep 1
done
