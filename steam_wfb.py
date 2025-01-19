#!/usr/bin/env python3

import curses
import configparser
import subprocess
import threading
import queue
import time
import signal
import sys

# An event for coordinating shutdown across threads
STOP_EVENT = threading.Event()

# Track all child Popen processes (so we can terminate them if needed)
CHILD_PROCESSES = []

# Track if user triggered Ctrl+C
CTRL_C_TRIGGERED = False

def handle_sigint(signum, frame):
    """Signal handler for Ctrl+C (SIGINT)."""
    global CTRL_C_TRIGGERED
    CTRL_C_TRIGGERED = True
    STOP_EVENT.set()

# Install the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode, event_queue):
    """
    Runs script.sh <iface> <tx_power> <channel> <region> <bandwidth> <mode>.
    Captures stdout/stderr, sends them to the event_queue under 'status'.
    """
    try:
        command = [
            "bash",
            "-c",
            f"./script.sh {interface} {tx_power} {channel} {region} {bandwidth} {mode}"
        ]
        event_queue.put(("status", f"[STARTING] WLAN: {interface} (mode={mode})"))

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Keep track so we can terminate if needed
        CHILD_PROCESSES.append(process)

        # Read stdout
        for line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("status", f"[STDOUT-{interface}] {line}"))

        # Read stderr
        for line in process.stderr:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("status", f"[STDERR-{interface}] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("status", f"[COMPLETED] WLAN: {interface} (mode={mode})"))
        else:
            event_queue.put(("status", f"[FAILED/TERMINATED] WLAN: {interface} (mode={mode}), code {return_code}"))

    except Exception as e:
        event_queue.put(("status", f"[ERROR] WLAN: {interface} (mode={mode}) - {str(e)}"))

def wfb_rx_worker(ip, port, event_queue):
    """
    Worker for wfb_rx with given IP/port.
    Captures stdout/stderr, sends them to the event_queue under 'wfb'.
    """
    try:
        command = [
            "/usr/bin/wfb_rx",
            "-a", "10000",
            "-p", "0",
            "-c", ip,
            "-u", port,
            "-K", "/etc/gs.key",
            "-R", "2097152",
            "-l", "1000",
            "-i", "7669206"
        ]
        event_queue.put(("wfb", "[STARTING] wfb_rx command"))

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        CHILD_PROCESSES.append(process)

        # Read stdout
        for line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("wfb", line))

        # Read stderr
        for line in process.stderr:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("wfb", f"[STDERR] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("wfb", "[COMPLETED] wfb_rx command"))
        else:
            event_queue.put(("wfb", f"[FAILED/TERMINATED] wfb_rx command, code {return_code}"))

    except Exception as e:
        event_queue.put(("wfb", f"[ERROR] wfb_rx: {str(e)}"))

def ncurses_main(stdscr):
    # 1. Load config
    config = configparser.ConfigParser()
    config.read("config.cfg")

    ip = config.get("common", "ip_address", fallback="192.168.1.49")
    port = config.get("common", "port", fallback="5600")
    region = config.get("common", "region", fallback="00")

    rx_wlans_str = config.get("wlans", "rx_wlans", fallback="").strip()
    tx_wlan_str  = config.get("wlans", "tx_wlan",  fallback="").strip()
    tx_power     = config.get("wlans", "tx_power", fallback="100")
    channel      = config.get("wlans", "channel",  fallback="161")
    bandwidth    = config.get("wlans", "bandwidth",fallback="HT20")

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    tx_wlans = tx_wlan_str.split()  if tx_wlan_str  else []
    all_ifaces = set(rx_wlans + tx_wlans)

    event_queue = queue.Queue()
    threads = []

    # Debug info
    event_queue.put(("status", f"[DEBUG] rx_wlans = {rx_wlans}"))
    event_queue.put(("status", f"[DEBUG] tx_wlans = {tx_wlans}"))
    event_queue.put(("status", f"[DEBUG] all_ifaces = {list(all_ifaces)}"))

    # 2. Start worker threads for each interface
    for iface in all_ifaces:
        if iface in rx_wlans and iface in tx_wlans:
            mode = "rx-tx"
        elif iface in rx_wlans:
            mode = "rx"
        else:
            mode = "tx"

        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue),
            daemon=True
        )
        t.start()
        threads.append(t)

    # 3. Start wfb_rx thread
    wfb_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(ip, port, event_queue),
        daemon=True
    )
    wfb_thread.start()
    threads.append(wfb_thread)

    # 4. Setup ncurses windows
    stdscr.clear()
    height, width = stdscr.getmaxyx()

    half_height = height // 2

    status_win = curses.newwin(half_height, width, 0, 0)
    status_win.nodelay(True)
    status_win.scrollok(True)

    wfb_win = curses.newwin(height - half_height, width, half_height, 0)
    wfb_win.nodelay(True)
    wfb_win.scrollok(True)

    status_logs = []
    wfb_logs = []

    MAX_STATUS_LINES = half_height - 2
    MAX_WFB_LINES = (height - half_height) - 2

    curses.curs_set(0)
    stdscr.nodelay(True)

    # 5. Main UI loop
    try:
        while True:
            # If STOP_EVENT is set, we need to terminate
            if STOP_EVENT.is_set():
                # Terminate all processes
                for proc in CHILD_PROCESSES:
                    if proc.poll() is None:
                        proc.terminate()
                break

            alive_threads = any(t.is_alive() for t in threads)

            # Drain event_queue
            while True:
                try:
                    kind, text = event_queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    status_logs.append(text)
                    if len(status_logs) > 1000:
                        status_logs.pop(0)
                elif kind == "wfb":
                    wfb_logs.append(text)
                    if len(wfb_logs) > 1000:
                        wfb_logs.pop(0)

            # Draw windows
            status_win.erase()
            status_win.border()
            slice_status = status_logs[-MAX_STATUS_LINES:]
            for idx, line in enumerate(slice_status):
                status_win.addstr(idx + 1, 1, line)
            status_win.refresh()

            wfb_win.erase()
            wfb_win.border()
            slice_wfb = wfb_logs[-MAX_WFB_LINES:]
            for idx, line in enumerate(slice_wfb):
                wfb_win.addstr(idx + 1, 1, line)
            wfb_win.refresh()

            # If no threads left and queue empty -> done
            if not alive_threads and event_queue.empty():
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        # If we get a direct KeyboardInterrupt (rare, but can happen),
        # we do the same as if user pressed Ctrl+C
        global CTRL_C_TRIGGERED
        CTRL_C_TRIGGERED = True
        STOP_EVENT.set()

        for proc in CHILD_PROCESSES:
            if proc.poll() is None:
                proc.terminate()

    # 6. After the loop, do final cleanup
    #    For instance, restart NetworkManager
    event_queue.put(("status", "[INFO] Restarting NetworkManager..."))
    try:
        subprocess.run(["systemctl", "restart", "NetworkManager"], check=False)
        event_queue.put(("status", "[INFO] NetworkManager restarted."))
    except Exception as e:
        event_queue.put(("status", f"[ERROR] Could not restart NetworkManager: {e}"))

    # If user pressed Ctrl+C, exit immediately
    if CTRL_C_TRIGGERED:
        return

    # Otherwise, let user see final logs
    stdscr.nodelay(False)
    stdscr.clear()
    stdscr.addstr(0, 0, "All threads have exited. Press any key to quit.")
    stdscr.refresh()
    stdscr.getch()

def main():
    curses.wrapper(ncurses_main)

if __name__ == "__main__":
    main()

