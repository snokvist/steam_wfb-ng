#!/usr/bin/env python3

import curses
import configparser
import subprocess
import threading
import queue
import time
import signal
import sys

STOP_EVENT = threading.Event()       # Set when we want to stop all threads
CHILD_PROCESSES = []                # Keep track of all subprocess.Popen objects
CTRL_C_TRIGGERED = False            # True if user pressed Ctrl+C

def handle_sigint(signum, frame):
    """Signal handler for Ctrl+C (SIGINT)."""
    global CTRL_C_TRIGGERED
    CTRL_C_TRIGGERED = True
    STOP_EVENT.set()

# Install the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)

# --------------------------------------------------------------------------
# Worker Functions
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode, event_queue):
    """
    Runs ./script.sh <interface> <tx_power> <channel> <region> <bandwidth> <mode>.
    Captures stdout/stderr, sends lines to event_queue under 'status'.
    """
    try:
        command = [
            "bash",
            "-c",
            f"./script.sh {interface} {tx_power} {channel} {region} {bandwidth} {mode}"
        ]
        event_queue.put(("status", f"[STARTING] WLAN: {interface} (mode={mode})"))

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
    Worker for the "video stream" wfb_rx command (bottom-left logs).
    Captures stdout/stderr, sends lines to event_queue under 'wfb'.
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
        event_queue.put(("wfb", "[STARTING] wfb_rx command (video)"))

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        CHILD_PROCESSES.append(process)

        for line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("wfb", line))

        for line in process.stderr:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("wfb", f"[STDERR] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("wfb", "[COMPLETED] wfb_rx (video)"))
        else:
            event_queue.put(("wfb", f"[FAILED/TERMINATED] wfb_rx (video), code {return_code}"))

    except Exception as e:
        event_queue.put(("wfb", f"[ERROR] wfb_rx (video): {str(e)}"))

def tunnel_rx_worker(key_path, event_queue):
    """
    Worker for the "tunnel" wfb_rx command (bottom-right logs).
    Captures stdout/stderr, sends lines to event_queue under 'tunnel'.
    """
    try:
        command = [
            "/usr/bin/wfb_rx",
            "-a", "10001",
            "-p", "32",
            "-u", "54682",
            "-K", key_path,
            "-R", "2097152",
            "-l", "1000",
            "-i", "7669206"
        ]
        event_queue.put(("tunnel", "[STARTING] wfb_rx (tunnel)"))

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        CHILD_PROCESSES.append(process)

        for line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("tunnel", line))

        for line in process.stderr:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("tunnel", f"[STDERR] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("tunnel", "[COMPLETED] wfb_rx (tunnel)"))
        else:
            event_queue.put(("tunnel", f"[FAILED/TERMINATED] wfb_rx (tunnel), code {return_code}"))

    except Exception as e:
        event_queue.put(("tunnel", f"[ERROR] wfb_rx (tunnel): {str(e)}"))

def tunnel_tx_worker(key_path, bandwidth, stbc, ldpc, mcs, fec_k, fec_n, fec_timeout, event_queue):
    """
    Worker for the "tunnel" wfb_tx command (bottom-right logs).
    Captures stdout/stderr, sends lines to event_queue under 'tunnel'.
    """
    try:
        command = [
            "/usr/bin/wfb_tx",
            "-d",
            "-f", "data",
            "-p", "160",
            "-u", "0",
            "-K", key_path,
            "-B", str(bandwidth),
            "-G", "long",
            "-S", str(stbc),
            "-L", str(ldpc),
            "-M", str(mcs),
            "-k", str(fec_k),
            "-n", str(fec_n),
            "-T", str(fec_timeout),
            "-F", "0",
            "-i", "7669206",
            "-R", "2097152",
            "-l", "1000",
            "-C", "0",
            "127.0.0.1:11001"
        ]
        event_queue.put(("tunnel", "[STARTING] wfb_tx (tunnel)"))

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        CHILD_PROCESSES.append(process)

        for line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("tunnel", line))

        for line in process.stderr:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = line.strip()
            if line:
                event_queue.put(("tunnel", f"[STDERR] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("tunnel", "[COMPLETED] wfb_tx (tunnel)"))
        else:
            event_queue.put(("tunnel", f"[FAILED/TERMINATED] wfb_tx (tunnel), code {return_code}"))

    except Exception as e:
        event_queue.put(("tunnel", f"[ERROR] wfb_tx (tunnel): {str(e)}"))

# --------------------------------------------------------------------------
# ncurses Main
# --------------------------------------------------------------------------

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

    # Tunnel config
    tunnel_key      = config.get("tunnel", "key_path", fallback="/etc/gs.key")
    tunnel_bw       = config.get("tunnel", "bandwidth", fallback="20")
    tunnel_stbc     = config.get("tunnel", "stbc", fallback="1")
    tunnel_ldpc     = config.get("tunnel", "ldpc", fallback="0")
    tunnel_mcs      = config.get("tunnel", "mcs", fallback="1")
    tunnel_fec_k    = config.get("tunnel", "fec_k", fallback="1")
    tunnel_fec_n    = config.get("tunnel", "fec_n", fallback="2")
    tunnel_fec_time = config.get("tunnel", "fec_timeout", fallback="0")

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    tx_wlans = tx_wlan_str.split()  if tx_wlan_str  else []
    all_ifaces = set(rx_wlans + tx_wlans)

    event_queue = queue.Queue()
    threads = []

    # Debug info in status
    event_queue.put(("status", f"[DEBUG] rx_wlans = {rx_wlans}"))
    event_queue.put(("status", f"[DEBUG] tx_wlans = {tx_wlans}"))
    event_queue.put(("status", f"[DEBUG] all_ifaces = {list(all_ifaces)}"))

    # 2. Start worker threads for each WLAN
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

    # 3. Start wfb_rx for video
    wfb_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(ip, port, event_queue),
        daemon=True
    )
    wfb_thread.start()
    threads.append(wfb_thread)

    # 4. Start new tunnel threads (rx + tx)
    tunnel_rx_thread = threading.Thread(
        target=tunnel_rx_worker,
        args=(tunnel_key, event_queue),
        daemon=True
    )
    tunnel_rx_thread.start()
    threads.append(tunnel_rx_thread)

    tunnel_tx_thread = threading.Thread(
        target=tunnel_tx_worker,
        args=(tunnel_key, tunnel_bw, tunnel_stbc, tunnel_ldpc, tunnel_mcs,
              tunnel_fec_k, tunnel_fec_n, tunnel_fec_time, event_queue),
        daemon=True
    )
    tunnel_tx_thread.start()
    threads.append(tunnel_tx_thread)

    # 5. Set up curses windows
    stdscr.clear()
    height, width = stdscr.getmaxyx()

    # The top half for status logs
    half_height = height // 2
    status_win = curses.newwin(half_height, width, 0, 0)
    status_win.nodelay(True)
    status_win.scrollok(True)

    # The bottom half is split into two columns:
    bottom_height = height - half_height
    wfb_width = width // 2

    # Bottom-left: existing wfb logs
    wfb_win = curses.newwin(bottom_height, wfb_width, half_height, 0)
    wfb_win.nodelay(True)
    wfb_win.scrollok(True)

    # Bottom-right: tunnel logs
    tunnel_width = width - wfb_width
    tunnel_win = curses.newwin(bottom_height, tunnel_width, half_height, wfb_width)
    tunnel_win.nodelay(True)
    tunnel_win.scrollok(True)

    status_logs = []
    wfb_logs = []
    tunnel_logs = []

    MAX_STATUS_LINES = half_height - 2
    MAX_WFB_LINES    = bottom_height - 2
    MAX_TUNNEL_LINES = bottom_height - 2

    curses.curs_set(0)
    stdscr.nodelay(True)

    # 6. Main UI loop
    try:
        while True:
            if STOP_EVENT.is_set():
                # Terminate all processes
                for proc in CHILD_PROCESSES:
                    if proc.poll() is None:
                        proc.terminate()
                break

            alive_threads = any(t.is_alive() for t in threads)

            # Drain event queue
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
                elif kind == "tunnel":
                    tunnel_logs.append(text)
                    if len(tunnel_logs) > 1000:
                        tunnel_logs.pop(0)

            # Redraw top (status) window
            status_win.erase()
            status_win.border()
            slice_status = status_logs[-MAX_STATUS_LINES:]
            for idx, line in enumerate(slice_status):
                status_win.addstr(idx + 1, 1, line)
            status_win.refresh()

            # Redraw bottom-left (wfb) window
            wfb_win.erase()
            wfb_win.border()
            slice_wfb = wfb_logs[-MAX_WFB_LINES:]
            for idx, line in enumerate(slice_wfb):
                wfb_win.addstr(idx + 1, 1, line)
            wfb_win.refresh()

            # Redraw bottom-right (tunnel) window
            tunnel_win.erase()
            tunnel_win.border()
            slice_tunnel = tunnel_logs[-MAX_TUNNEL_LINES:]
            for idx, line in enumerate(slice_tunnel):
                tunnel_win.addstr(idx + 1, 1, line)
            tunnel_win.refresh()

            # If no threads left & queue empty -> done
            if not alive_threads and event_queue.empty():
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        # If a direct KeyboardInterrupt occurs, treat like Ctrl+C
        global CTRL_C_TRIGGERED
        CTRL_C_TRIGGERED = True
        STOP_EVENT.set()
        for proc in CHILD_PROCESSES:
            if proc.poll() is None:
                proc.terminate()

    # 7. Restart NetworkManager at the end
    event_queue.put(("status", "[INFO] Restarting NetworkManager..."))
    try:
        subprocess.run(["systemctl", "restart", "NetworkManager"], check=False)
        event_queue.put(("status", "[INFO] NetworkManager restarted."))
    except Exception as e:
        event_queue.put(("status", f"[ERROR] Could not restart NetworkManager: {e}"))

    if CTRL_C_TRIGGERED:
        # Exit immediately if user pressed Ctrl+C
        return

    # Otherwise, show final logs
    stdscr.nodelay(False)
    stdscr.clear()
    stdscr.addstr(0, 0, "All threads have exited. Press any key to quit.")
    stdscr.refresh()
    stdscr.getch()

def main():
    curses.wrapper(ncurses_main)

if __name__ == "__main__":
    main()
