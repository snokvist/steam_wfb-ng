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
      >= -70 => magenta
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

def generate_ascii_bar(value, vmin, vmax, bar_count):
    """
    Generic helper to produce a bar string of '#' according to the ratio
    (value - vmin)/(vmax - vmin). Clamped between 0 and bar_count.
    """
    if vmax <= vmin:
        rng = 1.0
    else:
        rng = float(vmax - vmin)
    val_clamped = max(vmin, min(value, vmax))
    scale = (val_clamped - vmin) / rng
    length = int(round(scale * bar_count))
    return "#" * length

def build_rssi_chart_items(wfb_rxant_dict, rssi_min, rssi_max, bar_count, color_pairs):
    """
    Build a list of (line_str, color_attr) from the stored RX_ANT data,
    grouping by the first 8 hex characters, then sorting within each group
    by descending RSSI, and then sorting the groups by group-string ascending.
    """
    if not wfb_rxant_dict:
        return [("No RX_ANT data yet...", color_pairs["red"])]

    # Step 1: gather them into a list
    items = []
    for wlan_id, avg_rssi in wfb_rxant_dict.items():
        # 'group' is the first 8 hex characters of the ID
        group = wlan_id[:8]
        items.append((wlan_id, group, avg_rssi))

    # Step 2: sort the items:
    #   first by group ascending,
    #   then by descending RSSI
    items.sort(key=lambda x: (x[1], -x[2]))

    # Step 3: build (line_str, color) pairs
    lines = []
    rng = float(rssi_max - rssi_min)
    if rng < 1.0:
        rng = 1.0

    for (wlan_id, group, avg_rssi) in items:
        scale = (avg_rssi - rssi_min) / rng
        scale = max(0.0, min(scale, 1.0))
        bars = int(round(scale * bar_count))
        bar_str = "#" * bars

        line_str = f"{wlan_id}: avg={int(avg_rssi)} | {bar_str}"
        color_attr = get_rssi_color(avg_rssi, color_pairs)
        lines.append((line_str, color_attr))

    return lines

# --------------------------------------------------------------------------
# Worker Functions (stderr -> stdout merged)
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode,
                event_queue, retry_timeout=5):
    """
    Runs wlan_init.sh <iface> <tx_power> <channel> <region> <bandwidth> <mode>
    in a loop if it fails.

    - On success (exit code 0), it breaks the loop.
    - On failure, it waits `retry_timeout` seconds and tries again,
      unless STOP_EVENT is set.
    """
    while not STOP_EVENT.is_set():
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

            # Forward all lines from wlan_init.sh to the queue
            for raw_line in process.stdout:
                if STOP_EVENT.is_set():
                    process.terminate()
                    break
                line = clean_line_keep_timestamp(raw_line)
                if line.strip():
                    event_queue.put(("status", f"[{interface}] {line}"))

            return_code = process.wait()
            if return_code == 0:
                # Script ended "cleanly"
                event_queue.put(("status", f"[COMPLETED] WLAN: {interface} (mode={mode})"))
                break
            else:
                # Non-zero exit code => failure
                event_queue.put(("status",
                                 f"[FAILED/TERMINATED] WLAN: {interface} (mode={mode}), code {return_code}"))
                if STOP_EVENT.is_set():
                    break
                event_queue.put(("status",
                                 f"[RETRY] Waiting {retry_timeout}s before retrying WLAN init: {interface}"))
                time.sleep(retry_timeout)

        except Exception as e:
            event_queue.put(("status", f"[ERROR] WLAN: {interface} (mode={mode}) - {str(e)}"))
            if STOP_EVENT.is_set():
                break
            event_queue.put(("status",
                             f"[RETRY] Exception thrown. Waiting {retry_timeout}s before retrying: {interface}"))
            time.sleep(retry_timeout)

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

            if tag == "wfb":  # "video" lines
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
    retry_timeout = config.getint("common", "wlan_retry_timeout", fallback=5)

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
    event_queue.put(("status", f"[DEBUG] wlan_retry_timeout={retry_timeout}"))

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
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue, retry_timeout),
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
            "-u", "10002",
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
            "-l", "54682",
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
    retry_timeout = config.getint("common", "wlan_retry_timeout", fallback=5)

    # PKT + bar settings
    fec_rec_min  = config.getint("common", "fec_rec_min", fallback=0)
    fec_rec_max  = config.getint("common", "fec_rec_max", fallback=10)
    p_lost_min   = config.getint("common", "p_lost_min",  fallback=0)
    p_lost_max   = config.getint("common", "p_lost_max",  fallback=10)
    bar_count    = config.getint("common", "bar_count",   fallback=35)

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
    log_interval_ms = config.getint("tunnel", "log_interval", fallback=2000)

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    all_tx_wlans = tx_wlans_str.split() if tx_wlans_str else []

    event_queue = queue.Queue()
    threads = []

    # Debug info
    event_queue.put(("status", f"[DEBUG] rx_wlans = {rx_wlans}"))
    event_queue.put(("status", f"[DEBUG] tx_wlans (all) = {all_tx_wlans}"))
    event_queue.put(("status", f"[DEBUG] remote_injector = '{remote_injector}'"))
    event_queue.put(("status", f"[DEBUG] log_interval_ms={log_interval_ms}"))
    event_queue.put(("status", f"[DEBUG] rssi_min={rssi_min}, rssi_max={rssi_max}"))
    event_queue.put(("status", f"[DEBUG] fec_rec_min={fec_rec_min}, fec_rec_max={fec_rec_max}"))
    event_queue.put(("status", f"[DEBUG] p_lost_min={p_lost_min}, p_lost_max={p_lost_max}"))
    event_queue.put(("status", f"[DEBUG] bar_count={bar_count}"))
    event_queue.put(("status", f"[DEBUG] wlan_retry_timeout={retry_timeout}"))

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

    # Launch wlan_init.sh for each iface (with retry)
    for iface in all_ifaces:
        mode = get_mode(iface)
        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue, retry_timeout),
            daemon=True
        )
        t.start()
        threads.append(t)

    # Start wfb_rx for "video"
    wfb_video_cmd = [
        "./wfb_rx",
        "-a", "10000",
        "-p", "0",
        "-c", ip,
        "-u", port,
        "-K", key_path,
        "-R", "2097152",
        "-l", str(log_interval_ms),
        "-i", "7669206"
    ]
    wfb_video_thread = threading.Thread(
        target=wfb_rx_worker,
        args=(wfb_video_cmd, event_queue, "wfb"),
        daemon=True
    )
    wfb_video_thread.start()
    threads.append(wfb_video_thread)

    # Tunnel
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
            "-l", str(log_interval_ms),
            "-i", "7669206"
        ]
        tunnel_tx_cmd = [
            "./wfb_tx",
            "-d",
            "-f", "data",
            "-p", "160",
            "-u", "10002",
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
            "-l", str(log_interval_ms),
            "-C", "0",
            final_injector
        ]
        tunnel_tun_cmd = [
            "./wfb_tun",
            "-a", "10.5.0.1/24",
            "-l", "54682",
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
        event_queue.put(("tunnel", "[TUNNEL DISABLED] No local TX adapter or remote_injector."))

    # Buffers
    status_logs = []
    wfb_logs = []
    tunnel_logs = []

    # We'll keep TWO dictionaries:
    #   wfb_rxant_dict_current => accumulates new RX_ANT lines since last PKT
    #   wfb_rxant_dict_display => the set we actually show in the ASCII chart
    wfb_rxant_dict_current = {}
    wfb_rxant_dict_display = {}

    last_pkt_data = {
        "fec_rec": 0,
        "p_lost":  0,
        "b_all":   0,
        "b_out":   0
    }
    bitrate_all = 0.0
    bitrate_out = 0.0

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

    MAX_STATUS_LINES = half_height - 2
    MAX_STATS_LINES  = half_height - 2
    MAX_WFB_LINES    = bottom_height - 2
    MAX_TUNNEL_LINES = bottom_height - 2

    curses.curs_set(0)
    stdscr.nodelay(True)

    stats_header = ["[ASCII RSSI Chart for VIDEO feed (latest chunk)]"]
    wfb_header_lines = ["[VIDEO RX COMMAND]:"]
    wfb_header_lines += wrap_command(wfb_video_cmd, wfb_width - 2)

    tunnel_header_lines = ["[TUNNEL RX COMMAND]:"]
    if enable_tunnel:
        tunnel_header_lines += wrap_command(tunnel_rx_cmd, tunnel_width - 2)
        tunnel_header_lines.append("[TUNNEL TX COMMAND]:")
        tunnel_header_lines += wrap_command(tunnel_tx_cmd, tunnel_width - 2)
        tunnel_header_lines.append("[TUNNEL TUN COMMAND]:")
        tunnel_header_lines += wrap_command(tunnel_tun_cmd, tunnel_width - 2)
    else:
        tunnel_header_lines.append("tunnel disabled")

    status_header = []

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

                # Parse lines
                parts = text.split()
                if len(parts) >= 4 and parts[0] == "RX_ANT":
                    # Store new RX_ANT in "current"
                    wlan_id = parts[2]
                    chunk = parts[3].split(':')
                    avg_rssi = -9999.0
                    if len(chunk) >= 3:
                        try:
                            avg_rssi = float(chunk[2])
                        except ValueError:
                            avg_rssi = -9999.0

                    wfb_rxant_dict_current[wlan_id] = avg_rssi

                elif len(parts) >= 2 and parts[0] == "PKT":
                    # We parse PKT
                    try:
                        pkt_fields = parts[1].split(":")
                        if len(pkt_fields) == 9:
                            p_all      = int(pkt_fields[0])
                            b_all      = int(pkt_fields[1])
                            p_dec_err  = int(pkt_fields[2])
                            p_dec_ok   = int(pkt_fields[3])
                            fec_rec    = int(pkt_fields[4])
                            p_lost     = int(pkt_fields[5])
                            p_bad      = int(pkt_fields[6])
                            p_outgoing = int(pkt_fields[7])
                            b_outgoing = int(pkt_fields[8])

                            last_pkt_data["fec_rec"] = fec_rec
                            last_pkt_data["p_lost"]  = p_lost
                            last_pkt_data["b_all"]   = b_all
                            last_pkt_data["b_out"]   = b_outgoing

                            interval_s = float(log_interval_ms) / 1000.0
                            if interval_s > 0:
                                bitrate_all = (b_all * 8.0) / interval_s / 1e6
                                bitrate_out = (b_outgoing * 8.0) / interval_s / 1e6

                    except ValueError:
                        pass

                    # Now that we've ended a chunk, move "current" => "display"
                    wfb_rxant_dict_display = dict(wfb_rxant_dict_current)
                    wfb_rxant_dict_current.clear()

                    # We'll keep using 'wfb_rxant_dict_display' in the chart

            elif kind == "tunnel":
                tunnel_logs.append(text)
                if len(tunnel_logs) > 1000:
                    tunnel_logs.pop(0)

        # Redraw status window
        status_win.erase()
        status_win.border()
        leftover_status_lines = MAX_STATUS_LINES - 1 - len(status_header)
        row = 1
        for hl in status_header:
            if row >= MAX_STATUS_LINES:
                break
            status_win.addstr(row, 1, hl)
            row += 1
        slice_status = status_logs[-leftover_status_lines:] if leftover_status_lines > 0 else []
        for line in slice_status:
            if row >= MAX_STATUS_LINES:
                break
            status_win.addstr(row, 1, line)
            row += 1
        status_win.refresh()

        # Redraw stats window
        stats_win.erase()
        stats_win.border()
        leftover_stats_lines = MAX_STATS_LINES - 1
        row = 1

        # Print chart header
        for hl in stats_header:
            if row >= MAX_STATS_LINES:
                break
            stats_win.addstr(row, 1, hl)
            row += 1
            leftover_stats_lines -= 1

        # Build RSSI lines from wfb_rxant_dict_display
        rssi_items = build_rssi_chart_items(
            wfb_rxant_dict_display,
            rssi_min,
            rssi_max,
            bar_count,
            color_pairs
        )
        for (line_str, color_attr) in rssi_items:
            if leftover_stats_lines <= 0:
                break
            stats_win.addstr(row, 1, line_str, color_attr)
            row += 1
            leftover_stats_lines -= 1

        # Then FEC Rec bar
        if leftover_stats_lines > 0:
            fec_val = last_pkt_data["fec_rec"]
            fec_bar = generate_ascii_bar(fec_val, fec_rec_min, fec_rec_max, bar_count)
            line_str = f"FEC Rec: {fec_val} | {fec_bar}"
            stats_win.addstr(row, 1, line_str, color_pairs["magenta"])
            row += 1
            leftover_stats_lines -= 1

        # Lost bar
        if leftover_stats_lines > 0:
            lost_val = last_pkt_data["p_lost"]
            lost_bar = generate_ascii_bar(lost_val, p_lost_min, p_lost_max, bar_count)
            line_str = f"Lost   : {lost_val} | {lost_bar}"
            stats_win.addstr(row, 1, line_str, color_pairs["red"])
            row += 1
            leftover_stats_lines -= 1

        # Throughput lines
        if leftover_stats_lines > 0:
            stats_win.addstr(row, 1, f"All: {bitrate_all:.2f} mbit/s")
            row += 1
            leftover_stats_lines -= 1

        if leftover_stats_lines > 0:
            stats_win.addstr(row, 1, f"Data out: {bitrate_out:.2f} mbit/s")
            row += 1
            leftover_stats_lines -= 1

        stats_win.refresh()

        # Bottom-left: WFB logs
        draw_window(wfb_win, wfb_header_lines, wfb_logs, bottom_height, wfb_width)

        # Bottom-right: Tunnel logs
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
