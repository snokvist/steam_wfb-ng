#!/usr/bin/env python3
import curses
import configparser
import copy
import os
import subprocess
import traceback

# 4 special actions:
SPECIAL_SAVE_STEAMFPV  = "Save and start SteamFPV"
SPECIAL_SAVE_DEBUG     = "Save & exit to debug"
SPECIAL_EXIT_CURRENT   = "Exit with current config"
SPECIAL_SAVE_BIND      = "Save and bind drone"  # <-- new special item

DESCRIPTOR_FILE = "config_descriptor.ini"
CONFIG_FILE = "config.cfg"

def print_banner(stdscr, max_y, max_x):
    """
    Print the requested multi-line ASCII banner at the top,
    in light blue (cyan). Attempt to truncate lines if the
    terminal is too narrow.
    """
    BANNER_LINES = [
" ",
" ",
" ",
"   _________ __                        _________________________   ____",
"  /   _____//  |_  ____ _____    _____ \\_   _____/\\______   \\   \\ /   /",
"  \\_____  \\\\   __\\/ __ \\\\__  \\  /     \\ |    __)   |     ___/\\   Y   / ",
"  /        \\|  | \\  ___/ / __ \\|  Y Y  \\|     \\    |    |     \\     /  ",
" /_______  /|__|  \\___  >____  /__|_|  /\\___  /    |____|      \\___/   ",
"         \\/           \\/     \\/      \\/     \\/                           ",
" "
    ]

    curses.start_color()
    # Pair 1: cyan on black
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    # Pair 2: "orange-like" color on black (we use yellow as a stand-in for orange)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)

    stdscr.attron(curses.color_pair(1))
    for i, line in enumerate(BANNER_LINES):
        if i < max_y:
            truncated = line[:max_x - 1]
            stdscr.addstr(i, 0, truncated)
    stdscr.attroff(curses.color_pair(1))

def load_descriptor():
    parser = configparser.ConfigParser()
    parser.read(DESCRIPTOR_FILE)
    descriptor = {}

    for section in parser.sections():
        descriptor[section] = {}
        for key, value in parser.items(section):
            if '.' not in key:
                continue
            param, attr = key.split('.', 1)
            if param not in descriptor[section]:
                descriptor[section][param] = {}
            descriptor[section][param][attr] = value.strip()
    return descriptor

def load_config():
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(CONFIG_FILE)
    return cfg

def write_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        cfg.write(f)

def copy_config(cfg):
    new_cfg = configparser.ConfigParser()
    new_cfg.optionxform = str
    for sec in cfg.sections():
        new_cfg.add_section(sec)
        for k, v in cfg.items(sec):
            new_cfg.set(sec, k, v)
    return new_cfg

def parse_range(range_str):
    parts = range_str.split('-')
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None

def validate_constraint(new_val_str, constraint_str, section, param, config, descriptor):
    try:
        operator, other_param = constraint_str.split()
    except ValueError:
        return True, ""

    other_val_str = config.get(section, other_param, fallback=None)
    if other_val_str is None:
        return True, ""

    try:
        new_int = int(new_val_str)
        other_int = int(other_val_str)
    except ValueError:
        return True, ""

    if operator == '<':
        if not (new_int < other_int):
            return False, f"{param} must be < {other_param} ({other_int})."
    elif operator == '<=':
        if not (new_int <= other_int):
            return False, f"{param} must be <= {other_param} ({other_int})."
    elif operator == '>':
        if not (new_int > other_int):
            return False, f"{param} must be > {other_param} ({other_int})."
    elif operator == '>=':
        if not (new_int >= other_int):
            return False, f"{param} must be >= {other_param} ({other_int})."
    elif operator == '==':
        if not (new_int == other_int):
            return False, f"{param} must be == {other_param} ({other_int})."
    elif operator == '!=':
        if not (new_int != other_int):
            return False, f"{param} must be != {other_param} ({other_int})."

    return True, ""

def validate_value(section, param, new_value, descriptor, config):
    desc = descriptor.get(section, {}).get(param, {})
    ptype = desc.get('type','string').lower()

    # Empty => allowed
    if new_value.strip() == "":
        return True, ""

    # Type checks
    if ptype in ('integer','integer_select'):
        try:
            ival = int(new_value)
        except ValueError:
            return False, f"'{new_value}' is not a valid integer."
        # range check
        if 'range' in desc:
            mn, mx = parse_range(desc['range'])
            if mn is not None and mx is not None:
                if not (mn <= ival <= mx):
                    return False, f"Value must be in {mn}-{mx}."
    elif ptype == 'toggle01':
        if new_value not in ['0','1']:
            return False, "Value must be '0' or '1'."
    elif ptype == 'multi_select':
        tokens = new_value.split()
        valid_opts = [x.strip() for x in desc.get('valid_options','').split(',') if x.strip()]
        allow_custom = desc.get('allow_custom','false').lower() == 'true'
        for t in tokens:
            if t not in valid_opts and not allow_custom:
                return False, f"'{t}' not in {valid_opts}."
    elif ptype == 'ip_port_combo':
        parts = new_value.split(':')
        if len(parts) != 2:
            return False, "Must be 'IP:PORT' or empty."
        ip_str, port_str = parts[0].strip(), parts[1].strip()
        valid_ips = [x.strip() for x in desc.get('valid_ips','').split(',') if x.strip()]
        allow_ip_custom = desc.get('allow_custom_ip','false').lower() == 'true'
        if ip_str and ip_str not in valid_ips and not allow_ip_custom and valid_ips:
            return False, f"IP '{ip_str}' not in {valid_ips}."

        valid_ports = [x.strip() for x in desc.get('valid_ports','').split(',') if x.strip()]
        allow_port_custom = desc.get('allow_custom_port','false').lower() == 'true'
        try:
            int_port = int(port_str)
        except ValueError:
            return False, f"Port '{port_str}' is not a valid integer."

        if port_str not in valid_ports and not allow_port_custom and valid_ports:
            return False, f"Port '{port_str}' not in {valid_ports}."
    elif ptype == 'string_select':
        valid_opts = [x.strip() for x in desc.get('valid_options','').split(',') if x.strip()]
        allow_custom = desc.get('allow_custom','false').lower() == 'true'
        if valid_opts and new_value not in valid_opts and not allow_custom:
            return False, f"'{new_value}' not in {valid_opts}."

    # Additional 'valid_options' check for unknown type
    if 'valid_options' in desc and ptype not in (
        'multi_select','ip_port_combo','string_select','integer_select'):
        valid_list = [v.strip() for v in desc['valid_options'].split(',') if v.strip()]
        if new_value not in valid_list:
            return False, f"'{new_value}' not in {valid_list}."

    # Cross-field constraints
    if 'constraint' in desc:
        ok, err = validate_constraint(new_value, desc['constraint'], section, param, config, descriptor)
        if not ok:
            return False, err

    return True, ""

def show_error(stdscr, msg):
    """
    Show an error message and wait for one key press.
    """
    max_y, max_x = stdscr.getmaxyx()
    stdscr.clear()
    lines = [f"ERROR: {msg}", "", "Press any key to continue..."]
    for i, line in enumerate(lines):
        if i < max_y:
            stdscr.addstr(i, 0, line[:max_x - 1])
    stdscr.refresh()
    stdscr.getch()

def handle_resize(stdscr):
    """Handle screen resize event by clearing and re-drawing or resetting layout if needed."""
    curses.update_lines_cols()
    stdscr.clear()
    stdscr.refresh()

def confirm_dialog(stdscr, message):
    """
    Display a simple "Are you sure?"-style message, with instructions:
      LEFT => Cancel (False)
      RIGHT => Confirm (True)
    """
    max_y, max_x = stdscr.getmaxyx()
    stdscr.clear()
    lines = message.split('\n')
    for i, line in enumerate(lines):
        if i < max_y:
            stdscr.addstr(i, 0, line[:max_x - 1])
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            return False
        elif key == curses.KEY_LEFT:
            return False
        elif key == curses.KEY_RIGHT:
            return True

def show_script_output(stdscr, stdout_str, stderr_str):
    """
    Show the combined stdout/stderr from the script in a scrollable manner if needed,
    then prompt for a key press to continue.
    """
    max_y, max_x = stdscr.getmaxyx()
    stdscr.clear()

    # Combine output for display
    combined = []
    if stdout_str.strip():
        combined.append("STDOUT:")
        combined.extend(stdout_str.splitlines())
    if stderr_str.strip():
        combined.append("")
        combined.append("STDERR:")
        combined.extend(stderr_str.splitlines())
    if not stdout_str.strip() and not stderr_str.strip():
        combined.append("(No output)")

    # We'll do a simple paginated display:
    line_idx = 0
    while True:
        stdscr.clear()
        # Print a page's worth of lines
        last_printed = 0
        for row in range(max_y - 2):
            real_idx = line_idx + row
            if real_idx >= len(combined):
                break
            line = combined[real_idx]
            stdscr.addstr(row, 0, line[:max_x-1])
            last_printed = row
        stdscr.addstr(max_y-2, 0,
                      f"Lines {line_idx+1}-{line_idx+last_printed+1} of {len(combined)}")
        stdscr.addstr(max_y-1, 0, "Use UP/DOWN to scroll, RIGHT/LEFT to exit.")
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            line_idx = max(0, line_idx-1)
        elif key == curses.KEY_DOWN:
            if line_idx + (max_y - 2) < len(combined):
                line_idx += 1
        elif key == curses.KEY_RIGHT or key == curses.KEY_LEFT:
            break
        elif key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            # On resize, reset to top
            line_idx = 0

def run_bind_protocol(stdscr, config):
    """
    1) Confirm
    2) Validate tx_wlan (wlans->tx_wlan) + bind_data_folder (common->bind_data_folder)
    3) If valid, save config + run gs_bind.sh with the first tokens
    4) Show output
    5) Return to menu
    """
    # 1) Confirm
    sure = confirm_dialog(
        stdscr,
        "Are you REALLY sure you want to initiate bind protocol with current settings?\n\n"
        "LEFT=Cancel   RIGHT=Proceed"
    )
    if not sure:
        return  # user canceled

    # 2) Extract first tokens from tx_wlan and bind_data_folder
    tx_wlan_val = config.get('wlans', 'tx_wlan', fallback="").strip()
    bind_folder_val = config.get('common', 'bind_data_folder', fallback="").strip()

    # if either is empty or no tokens => error
    if not tx_wlan_val:
        show_error(stdscr, "wlans.tx_wlan is empty! Cannot bind.")
        return
    if not bind_folder_val:
        show_error(stdscr, "common.bind_data_folder is empty! Cannot bind.")
        return

    tx_wlan_first = tx_wlan_val.split()[0]
    bind_folder_first = bind_folder_val.split()[0]

    # 3) Save current config
    write_config(config)

    # 4) Run the bind script with 2 args
    try:
        result = subprocess.run(
            ["./gs_bind.sh", tx_wlan_first, bind_folder_first],
            capture_output=True, text=True
        )
    except Exception as e:
        show_error(stdscr, f"Failed to run ./gs_bind.sh:\n{e}")
        return

    # 5) Display the output from script
    stdout_str = result.stdout
    stderr_str = result.stderr
    show_script_output(stdscr, stdout_str, stderr_str)
    # user then returns to main menu

def curses_main(stdscr, original_config, descriptor):
    """
    Return a tuple: (config_or_None, action_string)
    Where action_string can be:
      - "steamfpv"  => means "Save and start SteamFPV"
      - "debug"     => means "Save & exit to debug"
      - "exit"      => means "Exit with current config" (discard)
      or None if user didn't pick a special action
    """
    current_config = copy_config(original_config)

    curses.curs_set(0)
    stdscr.keypad(True)

    sections = list(current_config.sections())

    # Main menu with the new special bind item at the very end:
    menu_items = sections + [
        SPECIAL_SAVE_STEAMFPV,
        # SPECIAL_SAVE_DEBUG,  # if you want to re-enable
        SPECIAL_EXIT_CURRENT,
        SPECIAL_SAVE_BIND  # <-- appended last
    ]
    idx = 0

    while True:
        # FULL CLEAR to remove any old submenu text
        stdscr.clear()

        # Re-draw ASCII banner at top
        max_y, max_x = stdscr.getmaxyx()
        print_banner(stdscr, max_y, max_x)

        instructions = "Use D-Pad: UP/DOWN/LEFT/RIGHT. (Right=Select, Ctrl+C=Exit)"
        if len(instructions) > max_x - 1:
            instructions = instructions[:max_x - 1]
        # Place instructions below banner, e.g. row 12
        row_instruct = 12
        if row_instruct < max_y:
            stdscr.move(row_instruct, 0)
            stdscr.clrtoeol()
            stdscr.addstr(row_instruct, 0, instructions)

        # Print main menu items starting from row 14
        row_start = 14
        for row in range(row_start, max_y):
            stdscr.move(row, 0)
            stdscr.clrtoeol()

        for i, item in enumerate(menu_items):
            row = row_start + i
            if row >= max_y:
                break
            prefix = "-> " if i == idx else "   "
            line = prefix + item
            if len(line) > max_x - 1:
                line = line[:max_x - 1]

            # If it's the "Save and bind drone" item, use orange-ish color:
            if item == SPECIAL_SAVE_BIND:
                stdscr.attron(curses.color_pair(2))
                stdscr.addstr(row, 0, line)
                stdscr.attroff(curses.color_pair(2))
            else:
                stdscr.addstr(row, 0, line)

        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue

        if key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            if chosen == SPECIAL_SAVE_STEAMFPV:
                # Example tweak: set "daemon" = false
                if "common" in current_config:
                    current_config["common"]["daemon"] = "false"
                return (current_config, "steamfpv")

            elif chosen == SPECIAL_SAVE_DEBUG:
                # Another example tweak
                if "common" in current_config:
                    current_config["common"]["daemon"] = "false"
                return (current_config, "debug")

            elif chosen == SPECIAL_EXIT_CURRENT:
                # discard changes
                return (None, "exit")

            elif chosen == SPECIAL_SAVE_BIND:
                # Perform "Save and bind drone" flow
                run_bind_protocol(stdscr, current_config)
                # After returning from run_bind_protocol, user sees main menu again
                pass

            else:
                # It's a section name => edit that section
                edit_section(stdscr, current_config, descriptor, chosen)
        elif key == curses.KEY_LEFT:
            # do nothing at top-level
            pass

def edit_section(stdscr, config, descriptor, section):
    params = list(config[section].keys())
    idx = 0

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        title = f"[{section}] - LEFT=Back, RIGHT=Edit, (Ctrl+C=Exit)"
        if len(title) > max_x-1:
            title = title[:max_x-1]
        stdscr.addstr(0, 0, title)

        for i, p in enumerate(params):
            row = 2 + i
            if row >= max_y:
                break
            val = config.get(section, p, fallback="")
            prefix = "-> " if i == idx else "   "
            line = f"{prefix}{p} = {val}"
            if len(line) > max_x-1:
                line = line[:max_x-1]
            stdscr.addstr(row, 0, line)

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue
        elif key == curses.KEY_UP:
            idx = (idx - 1) % len(params)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(params)
        elif key == curses.KEY_LEFT:
            break
        elif key == curses.KEY_RIGHT:
            edit_parameter(stdscr, config, descriptor, section, params[idx])

def edit_parameter(stdscr, config, descriptor, section, param):
    desc = descriptor.get(section, {}).get(param, {})
    help_text = desc.get('help','')
    current_val = config.get(section, param, fallback="")

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        line1 = f"Editing: [{section}.{param}] (LEFT=Cancel, RIGHT=Proceed, Ctrl+C=Exit)"
        if len(line1) > max_x-1:
            line1 = line1[:max_x-1]
        stdscr.addstr(0, 0, line1)

        line2 = f"Help: {help_text}"
        if len(line2) > max_x-1:
            line2 = line2[:max_x-1]
        stdscr.addstr(1, 0, line2)

        line3 = f"Current value: '{current_val}'"
        if len(line3) > max_x-1:
            line3 = line3[:max_x-1]
        stdscr.addstr(2, 0, line3)

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue
        elif key == curses.KEY_LEFT:
            return
        elif key == curses.KEY_RIGHT:
            new_val = handle_parameter_input(stdscr, config, descriptor, section, param)
            if new_val is None:
                return
            valid, err = validate_value(section, param, new_val, descriptor, config)
            if valid:
                config.set(section, param, new_val)
                return
            else:
                show_error(stdscr, err)

def handle_parameter_input(stdscr, config, descriptor, section, param):
    desc = descriptor.get(section, {}).get(param, {})
    ptype = desc.get('type', 'string').lower()
    current_val = config.get(section, param, fallback="")

    if ptype == 'multi_select':
        valid_opts = [v.strip() for v in desc.get('valid_options','').split(',') if v.strip()]
        allow_custom = desc.get('allow_custom','false').lower() == 'true'
        selected = set(current_val.split()) if current_val.strip() else set()
        result = multi_select_menu(stdscr, valid_opts, selected, allow_custom)
        return None if result is None else " ".join(result)
    elif ptype == 'toggle01':
        return toggle_menu_0_1(stdscr, current_val)
    elif ptype in ('string_select','integer_select'):
        valid_list = [v.strip() for v in desc.get('valid_options','').split(',') if v.strip()]
        allow_custom = desc.get('allow_custom','false').lower() == 'true'
        is_int = (ptype == 'integer_select')
        return single_select_menu(stdscr, valid_list, current_val, allow_custom, is_int)
    elif ptype == 'ip_port_combo':
        return ip_port_combo_input(stdscr, current_val, desc)
    else:
        return free_text_input(stdscr, current_val)

def multi_select_menu(stdscr, valid_opts, selected_set, allow_custom):
    menu_items = ["<EMPTY>"] + valid_opts
    if allow_custom:
        menu_items.append("[Add custom]")
    menu_items.append("[Done]")
    idx = 0

    def is_selected(opt):
        if opt == "<EMPTY>":
            return (len(selected_set) == 0)
        return opt in selected_set

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        header = "Multi-select: UP/DOWN=move, RIGHT=toggle/select, LEFT=cancel, Ctrl+C=Exit"
        stdscr.addstr(0, 0, header[:max_x-1])

        for i, opt in enumerate(menu_items):
            row = 2 + i
            if row >= max_y:
                break
            if opt == "<EMPTY>":
                mark = "[x]" if (len(selected_set) == 0) else "[ ]"
            elif opt in selected_set:
                mark = "[x]"
            elif opt.startswith("["):
                mark = "  "
            else:
                mark = "[ ]"

            prefix = "->" if i == idx else "  "
            line = f"{prefix}{mark} {opt}"
            if len(line) > max_x-1:
                line = line[:max_x-1]
            stdscr.addstr(row, 0, line)

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue
        elif key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_LEFT:
            return None
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            if chosen == "<EMPTY>":
                selected_set.clear()
            elif chosen == "[Add custom]":
                new_val = free_text_input(stdscr, "")
                if new_val is not None and new_val.strip():
                    selected_set.add(new_val.strip())
            elif chosen == "[Done]":
                return selected_set
            else:
                if chosen in selected_set:
                    selected_set.remove(chosen)
                else:
                    selected_set.add(chosen)

def toggle_menu_0_1(stdscr, current_val):
    items = ["<EMPTY>", "0", "1", "[Done]"]
    idx = 0
    if current_val.strip() == "":
        idx = 0
    elif current_val == "0":
        idx = 1
    elif current_val == "1":
        idx = 2

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        header = "Toggle 0/1. Up/Down=move, Right=choose, Left=cancel, Ctrl+C=Exit"
        stdscr.addstr(0, 0, header[:max_x-1])

        for i, val in enumerate(items):
            row = 2 + i
            if row >= max_y:
                break
            prefix = "->" if i == idx else "  "
            line = f"{prefix}{val}"
            if len(line) > max_x-1:
                line = line[:max_x-1]
            stdscr.addstr(row, 0, line)

        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue
        elif key == curses.KEY_UP:
            idx = (idx - 1) % len(items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(items)
        elif key == curses.KEY_LEFT:
            return None
        elif key == curses.KEY_RIGHT:
            chosen = items[idx]
            if chosen == "[Done]":
                if idx == 0:
                    return ""
                elif idx == 1:
                    return "0"
                elif idx == 2:
                    return "1"
            elif chosen == "<EMPTY>":
                return ""
            elif chosen == "0":
                return "0"
            elif chosen == "1":
                return "1"

def single_select_menu(stdscr, valid_list, current_val, allow_custom, is_int=False):
    menu_items = ["<EMPTY>"] + valid_list
    if allow_custom:
        menu_items.append("[Add custom]")
    menu_items.append("[Done]")

    idx = 0
    if current_val.strip() == "":
        idx = 0
    elif current_val in valid_list:
        idx = menu_items.index(current_val)

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        header = "Select or choose custom. Right=pick, Left=cancel, Ctrl+C=Exit"
        stdscr.addstr(0, 0, header[:max_x-1])

        for i, val in enumerate(menu_items):
            row = 2 + i
            if row >= max_y:
                break
            prefix = "->" if i == idx else "  "
            line = f"{prefix}{val}"
            if len(line) > max_x-1:
                line = line[:max_x-1]
            stdscr.addstr(row, 0, line)

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            continue
        elif key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_LEFT:
            return None
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            if chosen == "[Done]":
                if idx == 0:
                    return ""
                else:
                    if menu_items[idx] in valid_list:
                        return menu_items[idx]
                    else:
                        return ""
            elif chosen == "<EMPTY>":
                return ""
            elif chosen == "[Add custom]":
                new_val = free_text_input(stdscr, "")
                if new_val is None:
                    continue
                new_val = new_val.strip()
                if new_val == "":
                    return ""
                if is_int:
                    try:
                        int(new_val)
                    except ValueError:
                        show_error(stdscr, f"'{new_val}' is not a valid integer.")
                        continue
                return new_val
            else:
                return chosen

def ip_port_combo_input(stdscr, current_val, desc):
    valid_ips = [x.strip() for x in desc.get('valid_ips','').split(',') if x.strip()]
    allow_ip_custom = desc.get('allow_custom_ip','false').lower() == 'true'
    valid_ports = [x.strip() for x in desc.get('valid_ports','').split(',') if x.strip()]
    allow_port_custom = desc.get('allow_custom_port','false').lower() == 'true'

    ip_str, port_str = "", ""
    if ":" in current_val:
        ip_str, port_str = current_val.split(':',1)
        ip_str, port_str = ip_str.strip(), port_str.strip()

    # First choose IP
    new_ip = single_select_menu(stdscr, valid_ips, ip_str, allow_ip_custom, is_int=False)
    if new_ip is None:
        return None
    # Then choose port
    new_port = single_select_menu(stdscr, valid_ports, port_str, allow_port_custom, is_int=True)
    if new_port is None:
        return None

    if new_ip.strip() == "" or new_port.strip() == "":
        return ""
    return f"{new_ip}:{new_port}"

def free_text_input(stdscr, current_val):
    curses.echo()
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()

    line1 = "Enter new value (ENTER=confirm, LEFT=cancel, Ctrl+C=Exit). Current:"
    stdscr.addstr(0, 0, line1[:max_x-1])
    stdscr.addstr(1, 0, current_val[:max_x-1])
    prompt = "New value: "
    stdscr.addstr(3, 0, prompt[:max_x-1])
    stdscr.refresh()

    win_width = max_x - (len(prompt) + 1)
    if win_width < 1:
        win_width = 1
    win = curses.newwin(1, win_width, 3, len(prompt)+1)
    user_input = []
    pos = 0

    while True:
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            handle_resize(stdscr)
            curses.noecho()
            return None
        elif key == curses.KEY_LEFT:
            curses.noecho()
            return None
        elif key in [curses.KEY_ENTER, 10, 13]:
            curses.noecho()
            return "".join(user_input)
        elif key in [curses.KEY_BACKSPACE, 127]:
            if user_input:
                user_input.pop()
                pos -= 1
                win.clear()
                win.addstr(0, 0, "".join(user_input))
                stdscr.refresh()
        else:
            c = chr(key)
            user_input.append(c)
            pos += 1
            win.clear()
            win.addstr(0, 0, "".join(user_input))
            stdscr.refresh()

def run_curses_app(original_config, descriptor):
    """
    We wrap curses_main in try/except to handle Ctrl+C gracefully.
    Return (config_or_None, action_str).
    """
    try:
        return curses.wrapper(curses_main, original_config, descriptor)
    except KeyboardInterrupt:
        print("\nUser pressed Ctrl+C. Exiting gracefully (no changes saved).")
        return (None, "exit")
    except Exception as e:
        traceback.print_exc()
        return (None, "exit")

def main():
    print("HELLO: If you see this, the script is running at all!")
    # Check descriptor
    if not os.path.exists(DESCRIPTOR_FILE):
        print(f"Descriptor '{DESCRIPTOR_FILE}' not found.")
        return
    # Check config
    if not os.path.exists(CONFIG_FILE):
        print(f"Config file '{CONFIG_FILE}' not found.")
        return

    original_config = load_config()
    descriptor = load_descriptor()

    final_config, action = run_curses_app(original_config, descriptor)

    if final_config is None:
        print("Exited without saving (or was interrupted). No changes applied.")
        return

    # In normal scenarios (SteamFPV, debug, etc.) we finalize the config:
    write_config(final_config)
    print("Saved config to disk.")

if __name__ == "__main__":
    main()
