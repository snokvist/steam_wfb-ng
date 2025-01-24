#!/usr/bin/env python3
import curses
import configparser
import copy
import os
import traceback
import signal

# 3 special actions:
SPECIAL_SAVE_STEAMFPV  = "Save and start SteamFPV"
SPECIAL_SAVE_DEBUG     = "Save & exit to debug"
SPECIAL_EXIT_CURRENT   = "Exit with current config"

DESCRIPTOR_FILE = "config_descriptor.ini"
CONFIG_FILE = "config.cfg"

def print_banner(stdscr, max_y, max_x):
    """
    Print a 10-line ASCII "SteamFPV" banner at the top of the screen in light blue (cyan).
    Attempt to handle smaller terminals by truncating lines that won't fit.
    """
    BANNER_LINES = [
"  ____  _                           _____ _____ __   __ ",
" / ___|| |_ ___  _ __ ___  _ __    |  ___|_   _|\\ \\ / / ",
"| |    | __/ _ \\| '_ ` _ \\| '_ \\   | |_    | |   \\ V /  ",
"| |___ | || (_) | | | | | | |_) |  |  _|   | |    | |   ",
" \\____| \\__\\___/|_| |_| |_| .__/   |_|     |_|    |_|   ",
"                         |_|                             ",
"     ______ ______   ___  ____________   __             ",
"    / ____// ____/  /   |/_  __/ ____/  / /             ",
"   / /    / /      / /| | / / / __/    / /              ",
"  / /___ / /___   / ___ |/ / / /___   / /___            ",
"  \\____/ \\____/  /_/  |_/_/ /_____/  /_____/            "
    ]

    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)

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
    stdscr.clear()
    stdscr.addstr(0, 0, "ERROR:")
    stdscr.addstr(1, 0, msg)
    stdscr.addstr(3, 0, "Press any key to continue...")
    stdscr.refresh()
    stdscr.getch()

def handle_resize(stdscr):
    """Handle screen resize event by clearing and re-drawing or resetting layout if needed."""
    curses.update_lines_cols()
    stdscr.clear()
    stdscr.refresh()

def curses_main(stdscr, original_config, descriptor):
    current_config = copy_config(original_config)

    curses.curs_set(0)
    stdscr.keypad(True)

    sections = list(current_config.sections())
    menu_items = sections + [
        SPECIAL_SAVE_STEAMFPV,
        SPECIAL_SAVE_DEBUG,
        SPECIAL_EXIT_CURRENT
    ]
    idx = 0

    while True:
        # FULL CLEAR to remove any old submenu text:
        stdscr.clear()

        # Re-draw ASCII banner at top
        max_y, max_x = stdscr.getmaxyx()
        print_banner(stdscr, max_y, max_x)

        instructions = "Use D-Pad: UP/DOWN/LEFT/RIGHT. (Right=Select, Ctrl+C=Exit)"
        if len(instructions) > max_x - 1:
            instructions = instructions[:max_x - 1]
        # Place instructions below banner, say row 12:
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
                if "common" in current_config:
                    current_config["common"]["daemon"] = "true"
                return current_config
            elif chosen == SPECIAL_SAVE_DEBUG:
                if "common" in current_config:
                    current_config["common"]["daemon"] = "false"
                return current_config
            elif chosen == SPECIAL_EXIT_CURRENT:
                return None
            else:
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
                    if len(selected_set) == 0:
                        pass
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
            win.addstr(0, 0, "".join(user_input))
            stdscr.refresh()

def run_curses_app(original_config, descriptor):
    """Wrap curses_main with curses.wrapper but also catch Ctrl+C gracefully."""
    try:
        return curses.wrapper(curses_main, original_config, descriptor)
    except KeyboardInterrupt:
        print("\nUser pressed Ctrl+C. Exiting gracefully (no changes saved).")
        return None
    except Exception as e:
        traceback.print_exc()
        return None

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

    final_config = run_curses_app(original_config, descriptor)

    if final_config is None:
        print("Exited without saving or was interrupted. No changes applied.")
    else:
        write_config(final_config)
        print("Saved & exited. Changes written.")


if __name__ == "__main__":
    main()

