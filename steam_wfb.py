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

STOP_EVENT = threading.Event()  # Set when we want to stop all threads
CHILD_PROCESSES = []            # Track all subprocess.Popen objects
CTRL_C_TRIGGERED = False        # True if user pressed Ctrl+C

def handle_sigint(signum, frame):
    """Signal handler for Ctrl+C (SIGINT)."""
    global CTRL_C_TRIGGERED
    CTRL_C_TRIGGERED = True
    STOP_EVENT.set()

# Install the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)

# --------------------------------------------------------------------------
# Wrapping / Drawing Helpers
# --------------------------------------------------------------------------

def wrap_command(cmd_list, width):
    """
    Given a list of command arguments, returns a list of wrapped lines
    that fit within 'width' columns. We join the arguments with spaces
    then wrap it.
    """
    cmd_str = " ".join(cmd_list)
    return textwrap.wrap(cmd_str, width=width)

def draw_window(win, header_lines, log_lines, max_height, max_width):
    """
    Draws a window with:
      - 'header_lines' at the top,
      - Then the last portion of 'log_lines'.
    Both are truncated to fit within 'max_height' lines, 'max_width' columns.
    """
    win.erase()
    win.border()

    row = 1
    # Print header lines first
    for hline in header_lines:
        if row >= max_height - 1:
            break
        win.addstr(row, 1, hline)
        row += 1

    # Print the last portion of log_lines
    leftover_lines = max_height - 1 - row
    slice_logs = log_lines[-leftover_lines:] if leftover_lines > 0 else []
    for log_line in slice_logs:
        if row >= max_height - 1:
            break
        win.addstr(row, 1, log_line)
        row += 1

    win.refresh()

# --------------------------------------------------------------------------
# Custom Parsing for Video Lines
# --------------------------------------------------------------------------

def parse_video_line(raw_line: str) -> str:
    """
    For the "video" feed:
      - If the line begins (after optional timestamp) with "RX_ANT" or "PKT",
        then remove the first numeric token if it exists,
        AND replace any tabs with a single space.
      - Otherwise, leave line as-is (just strip trailing newlines).
    """
    line = raw_line.rstrip('\r\n')
    parts = line.split(None, 2)  # up to 3 chunks
    if len(parts) < 2:
        return line

    if parts[1] in ["RX_ANT", "PKT"] and parts[0].isdigit():
        # remove parts[0], then also replace tabs
        if len(parts) == 3:
            new_line = parts[1] + " " + parts[2]
        else:
            new_line = parts[1]
        return new_line.replace('\t', ' ')
    else:
        return line

def clean_line_keep_timestamp(line: str) -> str:
    """
    For non-video (or non-RX_ANT/PKT) lines, we keep them as is,
    except we strip trailing newlines for cleanliness.
    """
    return line.rstrip('\r\n')

# --------------------------------------------------------------------------
# ASCII Chart Helpers
# --------------------------------------------------------------------------

def get_rssi_color(avg_rssi, color_pairs):
    """
    Return a curses color attribute based on avg_rssi:
      >= -50 => green
      >= -60 => yellow
      >= -70 => magenta (orange)
      else   => red
    """
    if avg_rssi >= -50:
        return color_pairs["green"]
    elif avg_rssi >= -60:
        return color_pairs["yellow"]
    elif avg_rssi >= -70:
        return color_pairs["magenta"]
    else:
        return color_pairs["red"]

def generate_rssi_chart(wfb_rxant_dict, rssi_min, rssi_max):
    """
    Build a list of (line_text, avg_rssi) for each unique ID in wfb_rxant_dict,
    sorted descending by avg_rssi.
    """
    if not wfb_rxant_dict:
        return [("No RX_ANT data yet...", 0)]

    lines = []
    rng = float(rssi_max - rssi_min)
    if rng < 1.0:
        rng = 1.0

    items = []
    for wlan_id, entry in wfb_rxant_dict.items():
        parts = entry.split()
        # e.g. ["RX_ANT","5805:2:20","c0a8013400000001","2811:-42:-41:-41:0:0:0"]
        if len(parts) < 4:
            items.append((wlan_id, -9999, entry))
            continue
        chunk = parts[3]  # the "2811:-42:-41:-41:..." portion
        sub = chunk.split(':')
        if len(sub) < 3:
            items.append((wlan_id, -9999, entry))
            continue
        try:
            avg_rssi = float(sub[2])
        except ValueError:
            avg_rssi = -9999
        items.append((wlan_id, avg_rssi, entry))

    # Sort descending by avg_rssi
    items.sort(key=lambda x: x[1], reverse=True)

    for wlan_id, avg_rssi, entry in items:
        if avg_rssi < -200:  # invalid
            lines.append((f"{wlan_id}: ???", avg_rssi))
            continue
        scale = (avg_rssi - rssi_min)/rng
        scale = max(0.0, min(scale, 1.0))
        bar_count = int(scale * 35)
        bar_str = "#" * bar_count
        line_str = f"{wlan_id}: avg={int(avg_rssi)} | {bar_str}"
        lines.append((line_str, avg_rssi))

    return lines

# --------------------------------------------------------------------------
# Worker Functions (stderr -> stdout merged)
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode, event_queue):
    """
    Runs wlan_init.sh <iface> <tx_power> <channel> <region> <bandwidth> <mode>,
    merging stderr->stdout. We pass every line to 'status' basically unchanged.
    """
    try:
        cmd = f"./wlan_init.sh {interface} {tx_power} {channel} {region} {bandwidth} {mode}"
        command_list = ["bash", "-c", cmd]

        event_queue.put(("status", f"[STARTING] WLAN: {interface} (mode={mode})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_line_keep_timestamp(raw_line)
            if line.strip():
                event_queue.put(("status", f"[{interface}] {line}"))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("status", f"[COMPLETED] WLAN: {interface} (mode={mode})"))
        else:
            event_queue.put(("status", f"[FAILED/TERMINATED] WLAN: {interface} (mode={mode}), code {return_code}"))

    except Exception as e:
        event_queue.put(("status", f"[ERROR] WLAN: {interface} (mode={mode}) - {str(e)}"))

def wfb_rx_worker(command_list, event_queue, tag="wfb"):
    """
    Generic worker for wfb_rx with merged stderr->stdout.
    Lines for tag="wfb" are parsed with parse_video_line(),
    otherwise we just strip trailing newlines.
    """
    try:
        event_queue.put((tag, f"[STARTING] {command_list[0]} (tag={tag})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break

            if tag == "wfb":  # "video"
                line = parse_video_line(raw_line)
            else:
                line = clean_line_keep_timestamp(raw_line)

            if line.strip():
                event_queue.put((tag, line))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put((tag, f"[COMPLETED] {command_list[0]}"))
        else:
            event_queue.put((tag, f"[FAILED/TERMINATED] {command_list[0]}, code {return_code}"))

    except Exception as e:
        event_queue.put((tag, f"[ERROR] {command_list[0]}: {str(e)}"))

def wfb_tx_worker(command_list, event_queue, tag="tunnel"):
    """
    Generic worker for wfb_tx with merged stderr->stdout.
    We simply strip trailing newlines and send them to event_queue.
    """
    try:
        event_queue.put((tag, f"[STARTING] {command_list[0]} (tag={tag})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_line_keep_timestamp(raw_line)
            if line.strip():
                event_queue.put((tag, line))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put((tag, f"[COMPLETED] {command_list[0]}"))
        else:
            event_queue.put((tag, f"[FAILED/TERMINATED] {command_list[0]}, code {return_code}"))

    except Exception as e:
        event_queue.put((tag, f"[ERROR] {command_list[0]}: {str(e)}"))

def wfb_tun_worker(command_list, event_queue):
    """
    Generic worker for wfb_tun with merged stderr->stdout.
    Lines are stripped and sent to event_queue with tag="tunnel".
    """
    try:
        event_queue.put(("tunnel", "[STARTING] wfb_tun"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_line_keep_timestamp(raw_line)
            if line.strip():
                event_queue.put(("tunnel", line))

        return_code = process.wait()
        if return_code == 0:
            event_queue.put(("tunnel", "[COMPLETED] wfb_tun"))
        else:
            event_queue.put(("tunnel", f"[FAILED/TERMINATED] wfb_tun, code {return_code}"))

    except Exception as e:
        event_queue.put(("tunnel", f"[ERROR] wfb_tun: {str(e)}"))

# --------------------------------------------------------------------------
# Daemon Mode Logic
# --------------------------------------------------------------------------

def daemon_main():
    """
    This version does NOT use ncurses. It simply reads from config, launches
    the same threads/processes, and prints all logs to stdout until completion.
    """

    # 1. Load config
    config = configparser.ConfigParser()
    config.read("config.cfg")

    # Common
    ip = config.get("common", "ip_address", fallback="192.168.1.49")
    port = config.get("common", "port", fallback="5600")
    region = config.get("common", "region", fallback="00")
    rssi_min = config.getint("common", "rssi_min", fallback=-80)
    rssi_max = config.getint("common", "rssi_max", fallback=-20)

    rx_wlans_str = config.get("wlans", "rx_wlans", fallback="").strip()
    tx_wlans_str = config.get("wlans", "tx_wlan",  fallback="").strip()
    tx_power     = config.get("wlans", "tx_power", fallback="100")
    channel      = config.get("wlans", "channel",  fallback="161")
    bandwidth    = config.get("wlans", "bandwidth",fallback="HT20")
    key_path     = config.get("common", "key_path", fallback="/etc/gs.key")

    # Tunnel
    tunnel_bw       = config.get("tunnel", "bandwidth", fallback="20")
    tunnel_stbc     = config.get("tunnel", "stbc", fallback="1")
    tunnel_ldpc     = config.get("tunnel", "ldpc", fallback="0")
    tunnel_mcs      = config.get("tunnel", "mcs", fallback="1")
    tunnel_fec_k    = config.get("tunnel", "fec_k", fallback="1")
    tunnel_fec_n    = config.get("tunnel", "fec_n", fallback="2")
    tunnel_fec_time = config.get("tunnel", "fec_timeout", fallback="0")
    tunnel_agg_time = config.get("tunnel", "agg_timeout", fallback="5")
    remote_injector = config.get("tunnel", "remote_injector", fallback="")
    log_interval    = config.get("tunnel", "log_interval", fallback="2000")

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    all_tx_wlans = tx_wlans_str.split() if tx_wlans_str else []

    event_queue = queue.Queue()
    threads = []

    # Debug prints
    event_queue.put(("status", f"[DEBUG] Daemon mode active."))
    event_queue.put(("status", f"[DEBUG] rx_wlans = {rx_wlans}"))
    event_queue.put(("status", f"[DEBUG] tx_wlans (all) = {all_tx_wlans}"))
    event_queue.put(("status", f"[DEBUG] remote_injector = '{remote_injector}'"))
    event_queue.put(("status", f"[DEBUG] log_interval = {log_interval}"))
    event_queue.put(("status", f"[DEBUG] rssi_min={rssi_min}, rssi_max={rssi_max}"))

    # Determine the actual TX adapter (if any)
    tx_adapter = None
    if remote_injector.strip():
        event_queue.put(("status", "[INFO] remote_injector set -> ignoring tx_wlan entirely"))
    else:
        if len(all_tx_wlans) > 0:
            tx_adapter = all_tx_wlans[0]
            event_queue.put(("status", f"[INFO] Only first TX adapter used => {tx_adapter}"))

    all_ifaces = set(rx_wlans)
    if tx_adapter:
        all_ifaces.add(tx_adapter)

    event_queue.put(("status", f"[DEBUG] final all_ifaces = {list(all_ifaces)}"))

    def get_mode(iface):
        in_rx = iface in rx_wlans
        is_tx = (iface == tx_adapter)
        if in_rx and is_tx:
            return "rx-tx"
        elif in_rx:
            return "rx"
        elif is_tx:
            return "tx"
        return "unknown"

    # Launch wlan_init.sh for each interface
    for iface in all_ifaces:
        mode = get_mode(iface)
        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue),
            daemon=True
        )
        t.start()
        threads.append(t)

    # Start wfb_rx for VIDEO
    wfb_video_cmd = [
        "./wfb_rx",
        "-a", "10000",
        "-p", "0",
        "-c", ip,
        "-u", port,
        "-K", key_path,
        "-R", "2097152",
        "-l", str(log_interval),
        "-i", "7669206"
    ]
    wfb_video_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(wfb_video_cmd, event_queue, "wfb"),
        daemon=True
    )
    wfb_video_thread.start()
    threads.append(wfb_video_thread)

    # TUNNEL logic: only if we have a local TX adapter or a remote_injector
    enable_tunnel = (tx_adapter is not None or remote_injector.strip() != "")
    if enable_tunnel:
        default_injector = "127.0.0.1:11001"
        final_injector = remote_injector.strip() if remote_injector.strip() else default_injector

        # Build commands
        tunnel_rx_cmd = [
            "./wfb_rx",
            "-a", "10001",
            "-p", "32",
            "-u", "54682",
            "-K", key_path,
            "-R", "2097152",
            "-l", str(log_interval),
            "-i", "7669206"
        ]
        tunnel_tx_cmd = [
            "./wfb_tx",
            "-d",
            "-f", "data",
            "-p", "160",
            "-u", "0",
            "-K", key_path,
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
            "-l", str(log_interval),
            "-C", "0",
            final_injector
        ]
        tunnel_tun_cmd = [
            "./wfb_tun",
            "-a", "10.5.0.1/24",
            "-l", "10001",
            "-u", "10002",
            "-T", str(tunnel_agg_time)
        ]

        t_rx = threading.Thread(
            target=wfb_rx_worker,
            args=(tunnel_rx_cmd, event_queue, "tunnel"),
            daemon=True
        )
        t_rx.start()
        threads.append(t_rx)

        t_tx = threading.Thread(
            target=wfb_tx_worker,
            args=(tunnel_tx_cmd, event_queue, "tunnel"),
            daemon=True
        )
        t_tx.start()
        threads.append(t_tx)

        t_tun = threading.Thread(
            target=wfb_tun_worker,
            args=(tunnel_tun_cmd, event_queue),
            daemon=True
        )
        t_tun.start()
        threads.append(t_tun)
    else:
        # Tunnel disabled
        event_queue.put(("tunnel", "[TUNNEL DISABLED] No local TX adapter or remote_injector."))

    # Main loop: read from event_queue and print
    while True:
        if STOP_EVENT.is_set():
            # Terminate all child processes
            for proc in CHILD_PROCESSES:
                if proc.poll() is None:
                    proc.terminate()
            break

        alive_threads = any(t.is_alive() for t in threads)

        # Drain the queue
        while True:
            try:
                kind, text = event_queue.get_nowait()
            except queue.Empty:
                break

            # Just print to stdout
            print(f"[{kind.upper()}] {text}")

        # If no threads left & queue is empty -> done
        if not alive_threads and event_queue.empty():
            break

        time.sleep(0.1)

    # Final cleanup
    print("[STATUS] Executing final cleanup...")
    try:
        subprocess.run(["./final_cleanup.sh"], check=False)
        print("[STATUS] Final cleanup completed.")
    except Exception as e:
        print(f"[STATUS] [ERROR] Could not complete final cleanup: {e}")

    if CTRL_C_TRIGGERED:
        return
    else:
        print("[STATUS] All threads have exited. Daemon mode quitting now.")

# --------------------------------------------------------------------------
# ncurses Main (Interactive Mode)
# --------------------------------------------------------------------------

def ncurses_main(stdscr):
    curses.start_color()
    curses.use_default_colors()

    # Define color pairs
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_MAGENTA, -1)
    curses.init_pair(4, curses.COLOR_RED, -1)

    color_pairs = {
        "green": curses.color_pair(1),
        "yellow": curses.color_pair(2),
        "magenta": curses.color_pair(3),
        "red": curses.color_pair(4),
    }

    # 1. Load config
    config = configparser.ConfigParser()
    config.read("config.cfg")

    # Common stuff
    ip = config.get("common", "ip_address", fallback="192.168.1.49")
    port = config.get("common", "port", fallback="5600")
    region = config.get("common", "region", fallback="00")
    rssi_min = config.getint("common", "rssi_min", fallback=-80)
    rssi_max = config.getint("common", "rssi_max", fallback=-20)

    rx_wlans_str = config.get("wlans", "rx_wlans", fallback="").strip()
    tx_wlans_str = config.get("wlans", "tx_wlan",  fallback="").strip()
    tx_power     = config.get("wlans", "tx_power", fallback="100")
    channel      = config.get("wlans", "channel",  fallback="161")
    bandwidth    = config.get("wlans", "bandwidth",fallback="HT20")
    key_path     = config.get("common", "key_path", fallback="/etc/gs.key")

    # Tunnel
    tunnel_bw       = config.get("tunnel", "bandwidth", fallback="20")
    tunnel_stbc     = config.get("tunnel", "stbc", fallback="1")
    tunnel_ldpc     = config.get("tunnel", "ldpc", fallback="0")
    tunnel_mcs      = config.get("tunnel", "mcs", fallback="1")
    tunnel_fec_k    = config.get("tunnel", "fec_k", fallback="1")
    tunnel_fec_n    = config.get("tunnel", "fec_n", fallback="2")
    tunnel_fec_time = config.get("tunnel", "fec_timeout", fallback="0")
    tunnel_agg_time = config.get("tunnel", "agg_timeout", fallback="5")
    remote_injector = config.get("tunnel", "remote_injector", fallback="")
    log_interval    = config.get("tunnel", "log_interval", fallback="2000")

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    all_tx_wlans = tx_wlans_str.split() if tx_wlans_str else []

    event_queue = queue.Queue()
    threads = []

    # Debug info
    event_queue.put(("status", f"[DEBUG] rx_wlans = {rx_wlans}"))
    event_queue.put(("status", f"[DEBUG] tx_wlans (all) = {all_tx_wlans}"))
    event_queue.put(("status", f"[DEBUG] remote_injector = '{remote_injector}'"))
    event_queue.put(("status", f"[DEBUG] log_interval = {log_interval}"))
    event_queue.put(("status", f"[DEBUG] rssi_min={rssi_min}, rssi_max={rssi_max}"))

    # Determine TX adapter
    tx_adapter = None
    if remote_injector.strip():
        event_queue.put(("status", "[INFO] remote_injector set -> ignoring tx_wlan entirely"))
    else:
        if len(all_tx_wlans) > 0:
            tx_adapter = all_tx_wlans[0]
            event_queue.put(("status", f"[INFO] Only first TX adapter used => {tx_adapter}"))

    all_ifaces = set(rx_wlans)
    if tx_adapter:
        all_ifaces.add(tx_adapter)

    event_queue.put(("status", f"[DEBUG] final all_ifaces = {list(all_ifaces)}"))

    def get_mode(iface):
        in_rx = iface in rx_wlans
        is_tx = (iface == tx_adapter)
        if in_rx and is_tx:
            return "rx-tx"
        elif in_rx:
            return "rx"
        elif is_tx:
            return "tx"
        return "unknown"

    # 6. Launch wlan_init.sh for each iface
    for iface in all_ifaces:
        mode = get_mode(iface)
        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue),
            daemon=True
        )
        t.start()
        threads.append(t)

    # 7. wfb_rx for "video"
    wfb_video_cmd = [
        "./wfb_rx",
        "-a", "10000",
        "-p", "0",
        "-c", ip,
        "-u", port,
        "-K", key_path,
        "-R", "2097152",
        "-l", str(log_interval),
        "-i", "7669206"
    ]
    wfb_video_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(wfb_video_cmd, event_queue, "wfb"),
        daemon=True
    )
    wfb_video_thread.start()
    threads.append(wfb_video_thread)

    # 8. Tunnel
    enable_tunnel = (tx_adapter is not None or remote_injector.strip() != "")
    if enable_tunnel:
        default_injector = "127.0.0.1:11001"
        final_injector = remote_injector.strip() if remote_injector.strip() else default_injector

        tunnel_rx_cmd = [
            "./wfb_rx",
            "-a", "10001",
            "-p", "32",
            "-u", "54682",
            "-K", key_path,
            "-R", "2097152",
            "-l", str(log_interval),
            "-i", "7669206"
        ]
        tunnel_tx_cmd = [
            "./wfb_tx",
            "-d",
            "-f", "data",
            "-p", "160",
            "-u", "0",
            "-K", key_path,
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
            "-l", str(log_interval),
            "-C", "0",
            final_injector
        ]
        tunnel_tun_cmd = [
            "./wfb_tun",
            "-a", "10.5.0.1/24",
            "-l", "10001",
            "-u", "10002",
            "-T", str(tunnel_agg_time)
        ]

        t_rx = threading.Thread(
            target=wfb_rx_worker,
            args=(tunnel_rx_cmd, event_queue, "tunnel"),
            daemon=True
        )
        t_rx.start()
        threads.append(t_rx)

        t_tx = threading.Thread(
            target=wfb_tx_worker,
            args=(tunnel_tx_cmd, event_queue, "tunnel"),
            daemon=True
        )
        t_tx.start()
        threads.append(t_tx)

        t_tun = threading.Thread(
            target=wfb_tun_worker,
            args=(tunnel_tun_cmd, event_queue),
            daemon=True
        )
        t_tun.start()
        threads.append(t_tun)
    else:
        # No need to spawn tunnel threads
        event_queue.put(("tunnel", "[TUNNEL DISABLED] No local TX adapter or remote_injector."))

    # Prepare curses windows
    stdscr.clear()
    height, width = stdscr.getmaxyx()

    half_height = height // 2
    top_left_width = width // 2
    top_right_width = width - top_left_width

    status_win = curses.newwin(half_height, top_left_width, 0, 0)
    status_win.nodelay(True)
    status_win.scrollok(True)

    stats_win = curses.newwin(half_height, top_right_width, 0, top_left_width)
    stats_win.nodelay(True)
    stats_win.scrollok(True)

    bottom_height = height - half_height
    wfb_width = width // 2
    wfb_win = curses.newwin(bottom_height, wfb_width, half_height, 0)
    wfb_win.nodelay(True)
    wfb_win.scrollok(True)

    tunnel_width = width - wfb_width
    tunnel_win = curses.newwin(bottom_height, tunnel_width, half_height, wfb_width)
    tunnel_win.nodelay(True)
    tunnel_win.scrollok(True)

    # Buffers
    status_logs = []
    wfb_logs = []
    tunnel_logs = []
    wfb_rxant_dict = {}

    status_header = []
    stats_header  = ["[ASCII RSSI Chart for VIDEO feed (color-coded)]"]

    wfb_header_lines = ["[VIDEO RX COMMAND]:"]
    wfb_header_lines += wrap_command(wfb_video_cmd, wfb_width - 2)

    tunnel_header_lines = ["[TUNNEL RX COMMAND]:"]
    if enable_tunnel:
        tunnel_rx_cmd = [
            "./wfb_rx",
            "-a", "11001",
            "-p", "32",
            "-u", "54682",
            "-K", key_path,
            "-R", "2097152",
            "-l", str(log_interval),
            "-i", "7669206"
        ]
        tunnel_header_lines += wrap_command(tunnel_rx_cmd, tunnel_width - 2)
        tunnel_header_lines.append("[TUNNEL TX COMMAND]:")
        tunnel_tx_cmd = [
            "./wfb_tx",
            "-d",
            "-f", "data",
            "-p", "160",
            "-u", "0",
            "-K", key_path,
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
            "-l", str(log_interval),
            "-C", "0",
            (remote_injector.strip() if remote_injector.strip() else "127.0.0.1:11001")
        ]
        tunnel_header_lines += wrap_command(tunnel_tx_cmd, tunnel_width - 2)
        tunnel_header_lines.append("[TUNNEL TUN COMMAND]:")
        tunnel_tun_cmd = [
            "./wfb_tun",
            "-a", "10.5.0.1/24",
            "-l", "11001",
            "-u", "10002",
            "-T", str(tunnel_agg_time)
        ]
        tunnel_header_lines += wrap_command(tunnel_tun_cmd, tunnel_width - 2)
    else:
        tunnel_header_lines.append("tunnel disabled")

    MAX_STATUS_LINES = half_height - 2
    MAX_STATS_LINES  = half_height - 2
    MAX_WFB_LINES    = bottom_height - 2
    MAX_TUNNEL_LINES = bottom_height - 2

    curses.curs_set(0)
    stdscr.nodelay(True)

    # Main UI loop
    while True:
        if STOP_EVENT.is_set():
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
                # Track RX_ANT
                parts = text.split()
                if len(parts) >= 4 and parts[0] == "RX_ANT":
                    wlan_id = parts[2]
                    wfb_rxant_dict[wlan_id] = text

            elif kind == "tunnel":
                tunnel_logs.append(text)
                if len(tunnel_logs) > 1000:
                    tunnel_logs.pop(0)

        # Redraw status window
        status_win.erase()
        status_win.border()
        leftover_status_lines = MAX_STATUS_LINES - 1 - len(status_header)
        slice_status = status_logs[-leftover_status_lines:] if leftover_status_lines > 0 else []
        row = 1
        for hl in status_header:
            if row >= MAX_STATUS_LINES:
                break
            status_win.addstr(row, 1, hl)
            row += 1
        for line in slice_status:
            if row >= MAX_STATUS_LINES:
                break
            status_win.addstr(row, 1, line)
            row += 1
        status_win.refresh()

        # Redraw stats window (RSSI chart)
        stats_win.erase()
        stats_win.border()
        chart_lines = generate_rssi_chart(wfb_rxant_dict, rssi_min, rssi_max)
        leftover_stats_lines = MAX_STATS_LINES - 1 - len(stats_header)
        row = 1
        for hl in stats_header:
            if row >= MAX_STATS_LINES:
                break
            stats_win.addstr(row, 1, hl)
            row += 1
        for line_str, avg_rssi in chart_lines[-leftover_stats_lines:]:
            if row >= MAX_STATS_LINES:
                break
            color_attr = get_rssi_color(avg_rssi, color_pairs)
            stats_win.addstr(row, 1, line_str, color_attr)
            row += 1
        stats_win.refresh()

        # Bottom-left: WFB (video)
        draw_window(wfb_win, wfb_header_lines, wfb_logs, bottom_height, wfb_width)

        # Bottom-right: tunnel
        draw_window(tunnel_win, tunnel_header_lines, tunnel_logs, bottom_height, tunnel_width)

        if not alive_threads and event_queue.empty():
            break

        time.sleep(0.1)

    # Final cleanup
    event_queue.put(("status", "[INFO] Executing final cleanup..."))
    try:
        subprocess.run(["./final_cleanup.sh"], check=False)
        event_queue.put(("status", "[INFO] Final cleanup completed."))
    except Exception as e:
        event_queue.put(("status", f"[ERROR] Could not complete the final cleanup: {e}"))

    if CTRL_C_TRIGGERED:
        return

    stdscr.nodelay(False)
    stdscr.clear()
    stdscr.addstr(0, 0, "All threads have exited. Press any key to quit.")
    stdscr.refresh()
    stdscr.getch()

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    # Read config just to check if we're in daemon mode or not
    config = configparser.ConfigParser()
    config.read("config.cfg")
    daemon_str = config.get("common", "daemon", fallback="false").strip().lower()
    daemon_mode = (daemon_str == "true")

    if daemon_mode:
        daemon_main()
    else:
        curses.wrapper(ncurses_main)

if __name__ == "__main__":
    main()
