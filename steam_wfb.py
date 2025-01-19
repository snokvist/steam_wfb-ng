#!/usr/bin/env python3

import curses
import configparser
import subprocess
import threading
import queue
import time
import signal
import sys
import textwrap

STOP_EVENT = threading.Event()       # Set when we want to stop all threads
CHILD_PROCESSES = []                # Track all subprocess.Popen objects
CTRL_C_TRIGGERED = False            # True if user pressed Ctrl+C

def handle_sigint(signum, frame):
    """Signal handler for Ctrl+C (SIGINT)."""
    global CTRL_C_TRIGGERED
    CTRL_C_TRIGGERED = True
    STOP_EVENT.set()

# Install the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)

# --------------------------------------------------------------------------
# Text-Wrapping Helper
# --------------------------------------------------------------------------
def wrap_command(cmd_list, width):
    """
    Given a list of command arguments, returns a list of wrapped lines
    that fit within 'width' columns. We join the arguments with spaces
    to get a single string, then wrap it.
    """
    cmd_str = " ".join(cmd_list)
    return textwrap.wrap(cmd_str, width=width)

def draw_window(win, header_lines, log_lines, max_height, max_width):
    """
    Draws a window with:
      - A list of 'header_lines' at the top (wrapped),
      - Followed by the last portion of 'log_lines' so they fit.
    Both are wrapped within 'max_width' and truncated if they exceed 'max_height'.
    """
    win.erase()
    win.border()

    # First print the header lines
    row = 1
    for hline in header_lines:
        if row >= max_height - 1:
            break
        win.addstr(row, 1, hline)
        row += 1

    # Now print the logs in the remaining space
    leftover_lines = max_height - 1 - row
    slice_logs = log_lines[-leftover_lines:] if leftover_lines > 0 else []
    for log_line in slice_logs:
        if row >= max_height - 1:
            break
        win.addstr(row, 1, log_line)
        row += 1

    win.refresh()

# --------------------------------------------------------------------------
# Worker Functions
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode, event_queue):
    """
    Runs wlan_init.sh <iface> <tx_power> <channel> <region> <bandwidth> <mode>.
    Captures stdout/stderr, sends lines to event_queue under 'status'.
    """
    try:
        command_list = [
            "bash",
            "-c",
            f"./wlan_init.sh {interface} {tx_power} {channel} {region} {bandwidth} {mode}"
        ]
        event_queue.put(("status", f"[STARTING] WLAN: {interface} (mode={mode})"))

        process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def wfb_rx_worker(ip, port, event_queue, wfb_cmd_list):
    """
    Worker for the "video" wfb_rx command (bottom-left logs).
    Captures stdout/stderr, sends lines to event_queue under 'wfb'.
    'wfb_cmd_list' is the final list of arguments (for display and debugging).
    """
    try:
        event_queue.put(("wfb", "[STARTING] wfb_rx command (video)"))

        process = subprocess.Popen(wfb_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def tunnel_rx_worker(key_path, event_queue, tunnel_rx_cmd_list):
    """
    Worker for the "tunnel" wfb_rx command (bottom-right logs).
    Captures stdout/stderr, sends lines to event_queue under 'tunnel'.
    """
    try:
        event_queue.put(("tunnel", "[STARTING] wfb_rx (tunnel)"))

        process = subprocess.Popen(tunnel_rx_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def tunnel_tx_worker(key_path, bandwidth, stbc, ldpc, mcs, fec_k, fec_n, fec_timeout, event_queue, tunnel_tx_cmd_list):
    """
    Worker for the "tunnel" wfb_tx command (bottom-right logs).
    Captures stdout/stderr, sends lines to event_queue under 'tunnel'.
    """
    try:
        event_queue.put(("tunnel", "[STARTING] wfb_tx (tunnel)"))

        process = subprocess.Popen(tunnel_tx_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

def tunnel_tun_worker(agg_timeout, event_queue, tunnel_tun_cmd_list):
    """
    Worker for wfb_tun, controlling the tunnel interface.
    Captures stdout/stderr, sends lines to event_queue under 'tunnel'.
    """
    try:
        event_queue.put(("tunnel", "[STARTING] wfb_tun"))

        process = subprocess.Popen(tunnel_tun_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
            event_queue.put(("tunnel", "[COMPLETED] wfb_tun"))
        else:
            event_queue.put(("tunnel", f"[FAILED/TERMINATED] wfb_tun, code {return_code}"))

    except Exception as e:
        event_queue.put(("tunnel", f"[ERROR] wfb_tun: {str(e)}"))


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
    tunnel_agg_time = config.get("tunnel", "agg_timeout", fallback="5")

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    tx_wlans = tx_wlan_str.split()  if tx_wlan_str  else []
    all_ifaces = set(rx_wlans + tx_wlans)

    event_queue = queue.Queue()
    threads = []

    # Debug info in 'status'
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

    # 3. Build the wfb_rx (video) command
    wfb_video_cmd_list = [
        "./wfb_rx",
        "-a", "10000",
        "-p", "0",
        "-c", ip,
        "-u", port,
        "-K", "/etc/gs.key",
        "-R", "2097152",
        "-l", "3000",
        "-i", "7669206"
    ]
    wfb_video_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(ip, port, event_queue, wfb_video_cmd_list),
        daemon=True
    )
    wfb_video_thread.start()
    threads.append(wfb_video_thread)

    # 4. Build new tunnel commands for wfb_rx + wfb_tx + wfb_tun
    tunnel_rx_cmd_list = [
        "./wfb_rx",
        "-a", "10001",
        "-p", "32",
        "-u", "10002",
        "-K", tunnel_key,
        "-R", "2097152",
        "-l", "3000",
        "-i", "7669206"
    ]
    tunnel_tx_cmd_list = [
        "./wfb_tx",
        "-d",         # run as daemon
        "-f", "data",
        "-p", "160",
        "-u", "0",
        "-K", tunnel_key,
        "-B", str(tunnel_bw),
        "-G", "long",
        "-S", str(tunnel_stbc),
        "-L", str(tunnel_ldpc),
        "-M", str(tunnel_mcs),
        "-k", str(tunnel_fec_k),
        "-n", str(tunnel_fec_n),
        "-T", str(tunnel_fec_time),
        "-F", "0",
        "-i", "7669206",
        "-R", "2097152",
        "-l", "3000",
        "-C", "0",
        "127.0.0.1:11001"
    ]
    tunnel_tun_cmd_list = [
        "sudo",
        "./wfb_tun",
        "-a", "10.5.0.1/24",
        "-l", "10001",
        "-u", "10002",
        "-T", str(tunnel_agg_time)
        # Could add -t tun_name, etc. if desired
    ]

    tunnel_rx_t = threading.Thread(
        target=tunnel_rx_worker,
        args=(tunnel_key, event_queue, tunnel_rx_cmd_list),
        daemon=True
    )
    tunnel_rx_t.start()
    threads.append(tunnel_rx_t)

    tunnel_tx_t = threading.Thread(
        target=tunnel_tx_worker,
        args=(tunnel_key, tunnel_bw, tunnel_stbc, tunnel_ldpc, tunnel_mcs,
              tunnel_fec_k, tunnel_fec_n, tunnel_fec_time, event_queue, tunnel_tx_cmd_list),
        daemon=True
    )
    tunnel_tx_t.start()
    threads.append(tunnel_tx_t)

    tunnel_tun_t = threading.Thread(
        target=tunnel_tun_worker,
        args=(tunnel_agg_time, event_queue, tunnel_tun_cmd_list),
        daemon=True
    )
    tunnel_tun_t.start()
    threads.append(tunnel_tun_t)

    # 5. Setup curses windows
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

    # Bottom-left: wfb logs (video)
    wfb_win = curses.newwin(bottom_height, wfb_width, half_height, 0)
    wfb_win.nodelay(True)
    wfb_win.scrollok(True)

    # Bottom-right: tunnel logs
    tunnel_width = width - wfb_width
    tunnel_win = curses.newwin(bottom_height, tunnel_width, half_height, wfb_width)
    tunnel_win.nodelay(True)
    tunnel_win.scrollok(True)

    # Prepare log buffers
    status_logs = []
    wfb_logs = []
    tunnel_logs = []

    # Headers for wfb (video) window
    wfb_header_lines = ["[VIDEO RX COMMAND]:"]
    wfb_header_lines += wrap_command(wfb_video_cmd_list, wfb_width - 2)

    # Headers for tunnel window: RX, TX, TUN
    tunnel_header_lines = ["[TUNNEL RX COMMAND]:"]
    tunnel_header_lines += wrap_command(tunnel_rx_cmd_list, tunnel_width - 2)
    #tunnel_header_lines.append("")
    tunnel_header_lines.append("[TUNNEL TX COMMAND]:")
    tunnel_header_lines += wrap_command(tunnel_tx_cmd_list, tunnel_width - 2)
    #tunnel_header_lines.append("")
    tunnel_header_lines.append("[TUNNEL TUN COMMAND]:")
    tunnel_header_lines += wrap_command(tunnel_tun_cmd_list, tunnel_width - 2)

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

            # Draw bottom-left (video wfb) with header + logs
            draw_window(
                wfb_win,
                wfb_header_lines,
                wfb_logs,
                bottom_height,
                wfb_width
            )

            # Draw bottom-right (tunnel) with header + logs
            draw_window(
                tunnel_win,
                tunnel_header_lines,
                tunnel_logs,
                bottom_height,
                tunnel_width
            )

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
    event_queue.put(("status", "[INFO] Final cleanup..."))
    try:
        subprocess.run(["./final_cleanup.sh"], check=False)
        event_queue.put(("status", "[INFO] Final cleanup called"))
    except Exception as e:
        event_queue.put(("status", f"[ERROR] Error calling final cleanup: {e}"))

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
