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

def handle_sigterm(signum, frame):
    """Signal handler for SIGTERM."""
    global CTRL_C_TRIGGERED
    CTRL_C_TRIGGERED = True
    STOP_EVENT.set()


# Install the signal handler for Ctrl+C
signal.signal(signal.SIGINT, handle_sigint)
signal.signal(signal.SIGTERM, handle_sigterm)

# --------------------------------------------------------------------------
# Helper to parse the 64-bit WLAN ID into IP + indices
# --------------------------------------------------------------------------
def parse_ant_field(wlan_id_hex: str) -> str:
    """
    Given a 64-bit hex string (e.g. '7f00000100000001'),
    interpret it as:
       - top 4 bytes => IP
       - next 3 bytes => wlan_idx
       - last 1 byte => antenna_idx

    Returns e.g. "127.0.0.1_0_1" or fallback if parse fails.
    """
    if not wlan_id_hex:
        return "None"
    try:
        val_64 = int(wlan_id_hex, 16)
        ip_part = (val_64 >> 32) & 0xFFFFFFFF
        wlan_idx = (val_64 >> 8) & 0xFFFFFF
        antenna_idx = val_64 & 0xFF

        # Convert ip_part to dotted-decimal
        ip_address = ".".join(
            str((ip_part >> (8 * i)) & 0xFF) for i in reversed(range(4))
        )
        return f"{ip_address}_{wlan_idx}_{antenna_idx}"
    except Exception:
        return wlan_id_hex

# --------------------------------------------------------------------------
# Wrapping / Drawing Helpers
# --------------------------------------------------------------------------

def wrap_command(cmd_list, width):
    """
    Given a list of command arguments, returns a list of wrapped lines
    that fit within 'width' columns.
    """
    cmd_str = " ".join(cmd_list)
    return textwrap.wrap(cmd_str, width=width)

def draw_window(win, header_lines, log_lines, max_height, max_width):
    """
    Draws a window with:
      - 'header_lines' at the top,
      - Then the last portion of 'log_lines'.
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
        and replace tabs with a single space.
      - Otherwise, just strip trailing newlines.
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
    For non-video (or non-RX_ANT/PKT) lines, keep them as is,
    but strip trailing newlines.
    """
    return line.rstrip('\r\n')

# --------------------------------------------------------------------------
# ASCII Chart Helpers
# --------------------------------------------------------------------------

def get_rssi_color(avg_rssi, color_pairs):
    """
    Return a curses color attribute based on avg_rssi.
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
    Generic helper to produce a bar string of '#' based on ratio.
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
    Build a list of (line_str, color_attr) from the stored RX_ANT data.

    We do:
      - key format = freqchan_wlanid_count
      - parse them out.
      - use parse_ant_field(wlan_id_hex) to display a friendly version.
    """
    if not wfb_rxant_dict:
        return [("No RX_ANT data yet...", color_pairs["red"])]

    items = []
    for composite_key, avg_rssi in wfb_rxant_dict.items():
        # e.g. "5805:3:20_7f00000100000001_42"
        parts = composite_key.split('_', 2)
        if len(parts) == 3:
            freqchan, wlan_id_hex, line_count_str = parts
        elif len(parts) == 2:
            freqchan, wlan_id_hex = parts
            line_count_str = "0"
        else:
            freqchan = "?"
            wlan_id_hex = composite_key
            line_count_str = "0"

        # interpret the wlan_id_hex as IP etc.
        wlan_str = parse_ant_field(wlan_id_hex)

        # if you want a group for sorting, you can do e.g. group = wlan_id_hex[:8]
        # but we'll just keep it simple: sort by -avg_rssi
        items.append((freqchan, wlan_str, line_count_str, avg_rssi))

    # Sort by descending RSSI, or by freqchan ascending, etc. Let's do freqchan ascending, then -avg_rssi.
    # freqchan as a string might do lexicographic sort, but it's usually "5805:3:40" or "5805:2:20", etc.
    items.sort(key=lambda x: (x[0], -x[3]))

    lines = []
    rng = float(rssi_max - rssi_min)
    if rng < 1.0:
        rng = 1.0

    for (freqchan, wlan_str, line_count_str, avg_rssi) in items:
        scale = (avg_rssi - rssi_min) / rng
        scale = max(0.0, min(scale, 1.0))
        bar_len = int(round(scale * bar_count))
        bar_str = "#" * bar_len

        color_attr = get_rssi_color(avg_rssi, color_pairs)
        line_str = f"{freqchan} {wlan_str} [#{line_count_str}]: avg={int(avg_rssi)} | {bar_str}"
        lines.append((line_str, color_attr))

    return lines

# --------------------------------------------------------------------------
# Worker Functions (stderr->stdout)
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode,
                event_queue, retry_timeout=5):
    """
    Runs wlan_init.sh in a loop if it fails.
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
                break
            else:
                event_queue.put(("status", f"[FAILED/TERMINATED] WLAN: {interface} (mode={mode}), code {return_code}"))
                if STOP_EVENT.is_set():
                    break
                event_queue.put(("status", f"[RETRY] Waiting {retry_timeout}s before retrying WLAN init: {interface}"))
                time.sleep(retry_timeout)

        except Exception as e:
            event_queue.put(("status", f"[ERROR] WLAN: {interface} (mode={mode}) - {str(e)}"))
            if STOP_EVENT.is_set():
                break
            event_queue.put(("status", f"[RETRY] Exception. Wait {retry_timeout}s before retrying: {interface}"))
            time.sleep(retry_timeout)

def wfb_rx_worker(command_list, event_queue, tag="wfb"):
    """
    Worker for wfb_rx with merged stderr->stdout.
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
            if tag == "wfb":
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
    Worker for wfb_tx with merged stderr->stdout.
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
    Worker for wfb_tun with merged stderr->stdout.
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
# Daemon Mode
# --------------------------------------------------------------------------

def daemon_main():
    """
    This version does NOT use ncurses. Just read config, spawn threads,
    and pipe logs to stdout until done.
    """
    import configparser

    config = configparser.ConfigParser()
    config.read("config.cfg")

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

    print("[STATUS] Daemon mode active.")
    print(f"[STATUS] rx_wlans={rx_wlans}, tx_wlans={all_tx_wlans}, remote_injector='{remote_injector}'")

    tx_adapter = None
    if remote_injector.strip():
        print("[STATUS] remote_injector => ignoring tx_wlan")
    else:
        if len(all_tx_wlans) > 0:
            tx_adapter = all_tx_wlans[0]
            print(f"[STATUS] Using TX adapter => {tx_adapter}")

    all_ifaces = set(rx_wlans)
    if tx_adapter:
        all_ifaces.add(tx_adapter)

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

    for iface in all_ifaces:
        mode = get_mode(iface)
        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue, retry_timeout),
            daemon=True
        )
        t.start()
        threads.append(t)

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
        event_queue.put(("tunnel", "[TUNNEL DISABLED] No local TX adapter or remote_injector."))

    # Main loop
    while True:
        if STOP_EVENT.is_set():
            for proc in CHILD_PROCESSES:
                if proc.poll() is None:
                    proc.terminate()
            break
        alive_threads = any(t.is_alive() for t in threads)

        while True:
            try:
                k, txt = event_queue.get_nowait()
            except queue.Empty:
                break
            print(f"[{k.upper()}] {txt}")

        if not alive_threads and event_queue.empty():
            break
        time.sleep(0.1)

    print("[STATUS] Final cleanup (daemon mode)")
    try:
        subprocess.run(["./final_cleanup.sh"], check=False)
    except:
        pass
    if CTRL_C_TRIGGERED:
        return
    else:
        print("[STATUS] All threads exited. Daemon done.")

# --------------------------------------------------------------------------
# ncurses Main (Interactive Mode)
# --------------------------------------------------------------------------

def ncurses_main(stdscr):
    curses.start_color()
    curses.use_default_colors()

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

    import configparser
    config = configparser.ConfigParser()
    config.read("config.cfg")

    ip = config.get("common", "ip_address", fallback="192.168.1.49")
    port = config.get("common", "port", fallback="5600")
    region = config.get("common", "region", fallback="00")
    rssi_min = config.getint("common", "rssi_min", fallback=-80)
    rssi_max = config.getint("common", "rssi_max", fallback=-20)
    retry_timeout = config.getint("common", "wlan_retry_timeout", fallback=5)

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

    tunnel_bw       = config.get("tunnel", "bandwidth", fallback="20")
    tunnel_stbc     = config.get("tunnel", "stbc", fallback="1")
    tunnel_ldpc     = config.get("tunnel", "ldpc", fallback="0")
    tunnel_mcs      = config.get("tunnel", "mcs", fallback="1")
    tunnel_fec_k    = config.get("tunnel", "fec_k", fallback="1")
    tunnel_fec_n    = config.get("tunnel", "fec_n", fallback="2")
    tunnel_fec_time = config.get("tunnel", "fec_timeout", fallback="0")
    tunnel_agg_time = config.get("tunnel", "agg_timeout", fallback="5")
    remote_injector = config.get("tunnel", "remote_injector", fallback="")
    log_interval_ms = config.get("tunnel", "log_interval", fallback=2000)

    rx_wlans = rx_wlans_str.split() if rx_wlans_str else []
    all_tx_wlans = tx_wlans_str.split() if tx_wlans_str else []

    event_queue = queue.Queue()
    threads = []

    # We'll keep a line counter for each RX_ANT so we can always produce unique keys
    rxant_line_counter = 0

    # We store lines from last PKT in "display", new ones in "current"
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

    status_logs = []
    wfb_logs = []
    tunnel_logs = []

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

    tx_adapter = None
    if remote_injector.strip():
        event_queue.put(("status", "[INFO] remote_injector => ignoring tx_wlan"))
    else:
        if len(all_tx_wlans) > 0:
            tx_adapter = all_tx_wlans[0]
            event_queue.put(("status", f"[INFO] Using TX adapter => {tx_adapter}"))

    all_ifaces = set(rx_wlans)
    if tx_adapter:
        all_ifaces.add(tx_adapter)

    for iface in all_ifaces:
        mode = get_mode(iface)
        t = threading.Thread(
            target=wlan_worker,
            args=(iface, tx_power, channel, region, bandwidth, mode, event_queue, retry_timeout),
            daemon=True
        )
        t.start()
        threads.append(t)

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

    # Prepare curses
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

    stats_header = ["[ASCII RSSI Chart (last interval)]"]
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

                parts = text.split()
                if len(parts) >= 4 and parts[0] == "RX_ANT":
                    # e.g.: RX_ANT 5805:3:20 7f00000100000001 664:-57:-53:-50:...
                    freqchan = parts[1]
                    wlan_id_hex = parts[2]
                    chunk = parts[3].split(':')

                    avg_rssi = -9999.0
                    if len(chunk) >= 3:
                        try:
                            avg_rssi = float(chunk[2])
                        except ValueError:
                            avg_rssi = -9999.0

                    rxant_line_counter += 1
                    # unique composite key
                    composite_key = f"{freqchan}_{wlan_id_hex}_{rxant_line_counter}"
                    wfb_rxant_dict_current[composite_key] = avg_rssi

                elif len(parts) >= 2 and parts[0] == "PKT":
                    # parse PKT
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

                    # end chunk => move to display
                    wfb_rxant_dict_display = dict(wfb_rxant_dict_current)
                    wfb_rxant_dict_current.clear()
                    rxant_line_counter = 0

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

        # Chart header
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

        # FEC Rec bar
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

        # Bottom-left: wfb logs
        draw_window(wfb_win, wfb_header_lines, wfb_logs, bottom_height, wfb_width)
        # Bottom-right: tunnel logs
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
        event_queue.put(("status", f"[ERROR] Could not complete final cleanup: {e}"))

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
    import configparser
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
