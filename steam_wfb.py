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

from collections import OrderedDict

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
# Helpers
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

    # Remaining lines for logs
    leftover_lines = max_height - 1 - row
    slice_logs = log_lines[-leftover_lines:] if leftover_lines > 0 else []
    for log_line in slice_logs:
        if row >= max_height - 1:
            break
        win.addstr(row, 1, log_line)
        row += 1

    win.refresh()

def clean_wfb_line(raw_line):
    """
    Merge stderr into stdout, so we only read from stdout. We want to:
      1) Strip trailing whitespace
      2) Replace tabs with single spaces
      3) If the first token is purely digits, remove it (the 'timestamp')
      4) Rejoin the remainder with single spaces
    """
    line = raw_line.strip().replace('\t', ' ')
    parts = line.split()
    if parts and parts[0].isdigit():
        parts = parts[1:]
    return ' '.join(parts)

# --------------------------------------------------------------------------
# ASCII Chart for RSSI
# --------------------------------------------------------------------------

def generate_rssi_chart(rxant_dict, rssi_min, rssi_max):
    """
    For each unique ID in 'rxant_dict', we parse the latest line to find the avg_rssi.
    Only show the most recent line for each ID (that's all we store).
    Return a list of ASCII bar lines. For example:
       c0a8013400000001: avg=-41 | ####
    """
    if not rxant_dict:
        return ["No RX_ANT data yet..."]

    lines = []
    rng = float(rssi_max - rssi_min)
    if rng < 1.0:
        rng = 1.0  # avoid divide by zero

    # We can sort by ID or by insertion order. Let's do sorted by ID for clarity
    for wlan_id in sorted(rxant_dict.keys()):
        entry = rxant_dict[wlan_id]
        parts = entry.split()
        # parts typically: ["RX_ANT","5805:2:20","c0a8013400000001","2811:-42:-41:-41:0:0:0"]
        if len(parts) < 4:
            lines.append(entry)
            continue
        # The ID is parts[2], the chunk with rssi is parts[3]
        rssi_chunk = parts[3]
        subparts = rssi_chunk.split(':')
        if len(subparts) < 3:
            lines.append(entry)
            continue

        try:
            avg_rssi = float(subparts[2])  # the 'avg' is the 3rd field in "2811:-42:-41:-41..."
        except ValueError:
            lines.append(entry)
            continue

        # scale  => [0..1]
        scale = (avg_rssi - rssi_min)/rng
        scale = max(0.0, min(scale, 1.0))

        bar_count = int(scale * 30)  # 0..10
        bar = "#" * bar_count
        line_text = f"{wlan_id}: avg={int(avg_rssi)} | {bar}"
        lines.append(line_text)

    return lines

# --------------------------------------------------------------------------
# Worker Threads (with stderr -> stdout merged)
# --------------------------------------------------------------------------

def wlan_worker(interface, tx_power, channel, region, bandwidth, mode, event_queue):
    """
    Runs wlan_init.sh <iface> <tx_power> <channel> <region> <bandwidth> <mode>.
    Merges stderr->stdout so we read from a single stream.
    """
    try:
        command_list = [
            "bash", "-c",
            f"./wlan_init.sh {interface} {tx_power} {channel} {region} {bandwidth} {mode}"
        ]
        event_queue.put(("status", f"[STARTING] WLAN: {interface} (mode={mode})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # <== Merge stderr into stdout
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_wfb_line(raw_line)
            if line:
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
    wfb_rx with merged stderr->stdout.
    """
    try:
        event_queue.put((tag, f"[STARTING] {command_list[0]} command (tag={tag})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_wfb_line(raw_line)
            if line:
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
    wfb_tx with merged stderr->stdout.
    """
    try:
        event_queue.put((tag, f"[STARTING] {command_list[0]} (tag={tag})"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_wfb_line(raw_line)
            if line:
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
    wfb_tun with merged stderr->stdout.
    """
    try:
        event_queue.put(("tunnel", "[STARTING] wfb_tun"))

        process = subprocess.Popen(
            command_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge
            text=True
        )
        CHILD_PROCESSES.append(process)

        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                process.terminate()
                break
            line = clean_wfb_line(raw_line)
            if line:
                event_queue.put(("tunnel", line))

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

    # Common stuff + rssi_min/rssi_max for ASCII bar scale
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
    remote_injector = config.get("tunnel", "remote_injector", fallback="")
    log_interval    = config.get("tunnel", "log_interval", fallback="2000")

    # 2. Parse WLANS
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

    # 3. Determine actual TX adapter
    tx_adapter = None
    if remote_injector.strip():
        event_queue.put(("status", "[INFO] remote_injector set -> ignoring tx_wlan entirely"))
    else:
        if len(all_tx_wlans) > 0:
            tx_adapter = all_tx_wlans[0]
            event_queue.put(("status", f"[INFO] Only first TX adapter used => {tx_adapter}"))

    # 4. Build set of all ifaces
    if tx_adapter:
        all_ifaces = set(rx_wlans + [tx_adapter])
    else:
        all_ifaces = set(rx_wlans)

    event_queue.put(("status", f"[DEBUG] final all_ifaces = {list(all_ifaces)}"))

    # 5. Determine mode for each interface
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

    # 6. Launch wlan_init.sh threads
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
        "-K", "/etc/gs.key",
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

    # 8. Tunnel wfb_rx / wfb_tx / wfb_tun
    default_injector = "127.0.0.1:11001"
    final_injector = remote_injector.strip() if remote_injector.strip() else default_injector

    tunnel_rx_cmd = [
        "./wfb_rx",
        "-a", "10001",
        "-p", "32",
        "-u", "54682",
        "-K", tunnel_key,
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
        "-l", str(log_interval),
        "-C", "0",
        final_injector
    ]
    tunnel_tun_cmd = [
        "sudo", "./wfb_tun",
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

    # ----------------------------------------------------------------------
    # 9. Prepare curses windows
    stdscr.clear()
    height, width = stdscr.getmaxyx()

    # We'll split top half into (status_win, stats_win),
    # bottom half into (wfb_win, tunnel_win)
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

    # Buffers for logs
    status_logs = []
    stats_logs = []
    wfb_logs = []
    tunnel_logs = []

    # Instead of storing 20 lines, we store a dictionary of the latest line per ID
    wfb_rxant_dict = {}  # key=unique ID, value=the entire line

    # Window headers
    status_header = []
    stats_header  = ["[ASCII RSSI Chart for video feed]"]

    wfb_header_lines = ["[VIDEO RX COMMAND]:"]
    wfb_header_lines += wrap_command(wfb_video_cmd, wfb_width - 2)

    tunnel_header_lines = ["[TUNNEL RX COMMAND]:"]
    tunnel_header_lines += wrap_command(tunnel_rx_cmd, tunnel_width - 2)
    tunnel_header_lines.append("[TUNNEL TX COMMAND]:")
    tunnel_header_lines += wrap_command(tunnel_tx_cmd, tunnel_width - 2)
    tunnel_header_lines.append("[TUNNEL TUN COMMAND]:")
    tunnel_header_lines += wrap_command(tunnel_tun_cmd, tunnel_width - 2)

    # Some max lines for each window
    MAX_STATUS_LINES = half_height - 2
    MAX_STATS_LINES  = half_height - 2
    MAX_WFB_LINES    = bottom_height - 2
    MAX_TUNNEL_LINES = bottom_height - 2

    curses.curs_set(0)
    stdscr.nodelay(True)

    # ----------------------------------------------------------------------
    # 10. Main UI loop
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

                    parts = text.split()
                    if len(parts) >= 4 and parts[0] == "RX_ANT":
                        # ID is parts[2], the rssi chunk is parts[3], store the entire line
                        wlan_id = parts[2]
                        wfb_rxant_dict[wlan_id] = text

                elif kind == "tunnel":
                    tunnel_logs.append(text)
                    if len(tunnel_logs) > 1000:
                        tunnel_logs.pop(0)
                    # If you also want to handle RX_ANT for tunnel, do similarly:
                    # parts = text.split()
                    # if len(parts) >= 4 and parts[0] == "RX_ANT":
                    #     ...
                    #     store in a separate dictionary

            # Redraw top-left (status)
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

            # Redraw top-right (stats)
            stats_win.erase()
            stats_win.border()
            # We'll generate ASCII bars from the dictionary wfb_rxant_dict
            ascii_bars = generate_rssi_chart(wfb_rxant_dict, rssi_min, rssi_max)
            leftover_stats_lines = MAX_STATS_LINES - 1 - len(stats_header)

            row = 1
            for hl in stats_header:
                if row >= MAX_STATS_LINES:
                    break
                stats_win.addstr(row, 1, hl)
                row += 1

            # Show the ASCII bars
            relevant_bars = ascii_bars[-leftover_stats_lines:] if leftover_stats_lines > 0 else []
            for bar_line in relevant_bars:
                if row >= MAX_STATS_LINES:
                    break
                stats_win.addstr(row, 1, bar_line)
                row += 1

            stats_win.refresh()

            # Bottom-left: video (wfb)
            draw_window(wfb_win, wfb_header_lines, wfb_logs, bottom_height, wfb_width)

            # Bottom-right: tunnel
            draw_window(tunnel_win, tunnel_header_lines, tunnel_logs, bottom_height, tunnel_width)

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

    # ----------------------------------------------------------------------
    # 11. Final cleanup step
    event_queue.put(("status", "[INFO] Executing final cleanup..."))
    try:
        subprocess.run(["sudo", "./final_cleanup.sh"], check=False)
        event_queue.put(("status", "[INFO] Final cleanup completed."))
    except Exception as e:
        event_queue.put(("status", f"[ERROR] Could not complete the final cleanup: {e}"))

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
