#!/usr/bin/python3
import os
import signal
import subprocess
import sys
import time

# Global variables to store process objects
menu_process = None
steam_wfb_process = None
fpv_process = None
shutdown_triggered = False

# Path to the final cleanup script
FINAL_CLEANUP_SCRIPT = "./final_cleanup.sh"

# Cleanup function
def cleanup():
    global menu_process, steam_wfb_process, fpv_process, shutdown_triggered
    if shutdown_triggered:
        return  # Prevent recursive cleanup
    shutdown_triggered = True

    print("Cleaning up all processes...")

    # Run final_cleanup.sh (to ensure steam_wfb.py cleanup happens)
    if os.path.exists(FINAL_CLEANUP_SCRIPT):
        print("Running final_cleanup.sh...")
        try:
            subprocess.run([FINAL_CLEANUP_SCRIPT], check=False)
        except Exception as e:
            print(f"Error running {FINAL_CLEANUP_SCRIPT}: {e}")

    # Terminate fpv.sh
    if fpv_process and fpv_process.poll() is None:
        print("Sending SIGTERM to fpv.sh...")
        fpv_process.terminate()
        try:
            fpv_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Force killing fpv.sh...")
            fpv_process.kill()

    # Terminate steam_wfb.py
    if steam_wfb_process and steam_wfb_process.poll() is None:
        print("Sending SIGTERM to steam_wfb.py...")
        steam_wfb_process.terminate()
        try:
            steam_wfb_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Force killing steam_wfb.py...")
            steam_wfb_process.kill()

    print("Cleanup complete.")
    sys.exit(0)

# Signal handlers
def signal_handler(signum, frame):
    print(f"Received signal: {signum}")
    cleanup()

# Main function
def main():
    global menu_process, steam_wfb_process, fpv_process

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Launch menu_selector.py
    print("Launching menu_selector.py...")
    menu_process = subprocess.Popen(
        ["konsole", "--qwindowgeometry", "1280x800", "-e", "./menu_selector.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    menu_process.wait()  # Wait for menu_selector.py to complete

    # Read the updated config
    config_file = "config.cfg"
    gst_pipeline = "video"  # Default pipeline
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            for line in f:
                if line.startswith("gst_pipeline"):
                    gst_pipeline = line.split("=")[-1].strip()

    print(f"DEBUG: Updated gst_pipeline={gst_pipeline}")

    # Launch steam_wfb.py
    print("Launching steam_wfb.py...")

    steam_wfb_process = subprocess.Popen(
        ["konsole", "--qwindowgeometry", "1280x800", "-e", "sudo ./steam_wfb.py"],
        #["xterm". "-geometry", "100x44", "-e", "'sudo ./steam_wfb.py'"]
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Launch fpv.sh
    print("Launching fpv.sh...")
    fpv_process = subprocess.Popen(
        ["./fpv.sh", gst_pipeline],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Monitor processes
    try:
        while True:
            # Check if fpv.sh exited unexpectedly
            if fpv_process.poll() is not None:
                print("fpv.sh has exited unexpectedly. Triggering cleanup...")
                cleanup()

            # Check if steam_wfb.py exited unexpectedly
            if steam_wfb_process.poll() is not None:
                print("steam_wfb.py has exited unexpectedly. Triggering cleanup...")
                cleanup()

            time.sleep(1)  # Sleep briefly to avoid high CPU usage
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
