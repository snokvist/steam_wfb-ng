#!/bin/bash

###############################################################################
# fpv.sh - A script to run different GStreamer pipelines with proper
#          signal handling and automatic restart if gst-launch crashes.
#
# By default, if no argument is passed, it runs the VIDEO pipeline.
#
# Usage: ./fpv.sh [video|video+record|video+audio|video+audio+record] [port]
###############################################################################

echo "start: $(date)" >> /tmp/fpv.log

# Function to handle cleanup on exit
onExit() {
    echo "stop: $(date)" >> /tmp/fpv.log
    # Kill all child processes in this script's process group
    pkill -TERM -P $$ 2>/dev/null
    sleep 1  # Allow processes to terminate gracefully
    pkill -9 -P $$ 2>/dev/null  # Force kill lingering child processes
    echo "fpv.sh cleanup completed." >> /tmp/fpv.log
    exit 0
}

# Trap all relevant signals for a clean exit
trap onExit EXIT HUP INT QUIT TERM

# Default to "video" if no argument is passed
MODE="${1:-video}"

# Default port is 5600 if no second argument is provided
PORT="${2:-5600}"

# Function to run the GStreamer pipeline
run_pipeline() {
    case "$MODE" in

    ###########################################################################
    # VIDEO pipeline (simple video display, no recording, no audio)
    ###########################################################################
    video)
        echo "Running VIDEO pipeline on port $PORT"
        gst-launch-1.0 \
            udpsrc port=$PORT ! \
            queue max-size-time=1 ! \
            application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
            rtpjitterbuffer latency=1 ! \
            rtph265depay ! \
            vaapih265dec ! \
            fpsdisplaysink video-sink=xvimagesink sync=true
        ;;

    ###########################################################################
    # VIDEO+RECORD pipeline (display and record, no audio)
    ###########################################################################
    video+record)
        echo "Running VIDEO+RECORD pipeline on port $PORT"
        gst-launch-1.0 -e \
            udpsrc port=$PORT ! \
            tee name=videoTee \
            videoTee. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                mpegtsmux name=ts ! \
                filesink location="/home/deck/Videos/record-$(date +%y%m%d_%H%M%S).ts" sync=false \
            videoTee. ! queue ! \
                application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                rtpjitterbuffer latency=20 ! \
                rtph265depay ! \
                vaapih265dec ! \
                xvimagesink sync=false async=false
        ;;

    ###########################################################################
    # VIDEO+AUDIO pipeline (display both video and audio)
    ###########################################################################
    video+audio)
        echo "Running VIDEO+AUDIO pipeline on port $PORT"
        gst-launch-1.0 \
            udpsrc port=$PORT ! \
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

    ###########################################################################
    # VIDEO+AUDIO+RECORD pipeline (display and record both video and audio)
    ###########################################################################
    video+audio+record)
        echo "Running VIDEO+AUDIO+RECORD pipeline on port $PORT"
        gst-launch-1.0 -e \
            udpsrc port=$PORT ! \
            tee name=t \
                t. ! queue ! \
                    application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                    rtpjitterbuffer latency=1 ! \
                    rtph265depay ! \
                    vaapih265dec ! \
                    xvimagesink sync=false async=false \
                t. ! queue ! \
                    application/x-rtp,payload=98,clock-rate=48000,encoding-name=OPUS ! \
                    rtpjitterbuffer latency=1 ! \
                    rtpopusdepay ! \
                    opusdec ! \
                    audioconvert ! \
                    audioresample ! \
                    alsasink sync=false async=false \
                t. ! queue ! \
                    application/x-rtp,payload=97,clock-rate=90000,encoding-name=H265 ! \
                    rtpjitterbuffer latency=20 ! \
                    rtph265depay ! \
                    h265parse config-interval=1 ! \
                    video/x-h265,alignment=au,stream-format=byte-stream ! \
                    mpegtsmux name=mux ! \
                    filesink location="/home/deck/Videos/record-$(date +%y%m%d_%H%M%S).ts" sync=false \
                t. ! queue ! \
                    application/x-rtp,payload=98,clock-rate=48000,encoding-name=OPUS ! \
                    rtpjitterbuffer latency=20 ! \
                    rtpopusdepay ! \
                    opusparse ! \
                    mux.
        ;;

    ###########################################################################
    # Invalid mode
    ###########################################################################
    *)
        echo "Invalid mode: '$MODE'"
        echo "Usage: $0 [video|video+record|video+audio|video+audio+record] [port]"
        exit 1
        ;;
    esac
}

# Main loop to restart the pipeline if it crashes or exits with non-zero status
while true; do
    run_pipeline &
    GST_PID=$!
    wait $GST_PID
    EXIT_CODE=$?
    if [[ $EXIT_CODE -ne 0 ]]; then
        echo "Pipeline crashed with exit code: $EXIT_CODE" >> /tmp/fpv.log
    fi
    sleep 1
done
