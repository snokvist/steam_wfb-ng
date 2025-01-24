#!/usr/bin/python3
import curses
import configparser
import copy
import os
import traceback

DESCRIPTOR_FILE = "config_descriptor.ini"
CONFIG_FILE = "config.cfg"

SPECIAL_SAVE_EXIT = "Save & Exit"
SPECIAL_EXIT_NO_SAVE = "Exit w/o Saving"

def load_descriptor():
    """Load the descriptor from an INI file into a nested dict."""
    print("DEBUG: Entering load_descriptor()")
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
    print("DEBUG: Exiting load_descriptor() with sections:", list(descriptor.keys()))
    return descriptor

def load_config():
    """Load the user config (INI) from disk."""
    print("DEBUG: Entering load_config()")
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(CONFIG_FILE)
    print("DEBUG: Exiting load_config() with sections:", cfg.sections())
    return cfg

def write_config(cfg):
    """Write a ConfigParser to disk."""
    print("DEBUG: Entering write_config()")
    with open(CONFIG_FILE, 'w') as f:
        cfg.write(f)
    print("DEBUG: Exiting write_config() - config saved.")

def copy_config(cfg):
    """Deep-copy a ConfigParser."""
    print("DEBUG: Entering copy_config()")
    new_cfg = configparser.ConfigParser()
    new_cfg.optionxform = str
    for sec in cfg.sections():
        new_cfg.add_section(sec)
        for k, v in cfg.items(sec):
            new_cfg.set(sec, k, v)
    print("DEBUG: Exiting copy_config()")
    return new_cfg

def parse_range(range_str):
    """Parse e.g. '1-65535' -> (1,65535)."""
    parts = range_str.split('-')
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None

def validate_constraint(new_val_str, constraint_str, section, param, config, descriptor):
    """
    Evaluate cross-field constraint like '<= fec_rec_max' or '>= fec_rec_min', etc.
    """
    print(f"DEBUG: validate_constraint() for {section}.{param}, constraint='{constraint_str}', new_val='{new_val_str}'")
    try:
        operator, other_param = constraint_str.split()
    except ValueError:
        return True, ""

    other_val_str = config.get(section, other_param, fallback=None)
    if other_val_str is None:
        return True, ""  # other param doesn't exist

    # Attempt int
    try:
        new_int = int(new_val_str)
        other_int = int(other_val_str)
    except ValueError:
        # If not int, skip
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
    """
    Validate `new_value` based on descriptor rules. Empty is always allowed.
    """
    print(f"DEBUG: validate_value() for {section}.{param}, new='{new_value}'")
    desc = descriptor.get(section, {}).get(param, {})
    ptype = desc.get('type','string').lower()

    # 1) Empty => allowed
    if new_value.strip() == "":
        return True, ""

    # 2) Type checks
    if ptype in ('integer','integer_select'):
        try:
            ival = int(new_value)
        except ValueError:
            return False, f"'{new_value}' is not a valid integer."
        # range
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
                return False, f"'{t}' not in {valid_opts}, custom not allowed."
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
            return False, f"'{new_value}' not in {valid_opts}, custom not allowed."

    # 3) If 'valid_options' is present for other types
    if 'valid_options' in desc and ptype not in (
        'multi_select','ip_port_combo','string_select','integer_select'):
        valid_list = [v.strip() for v in desc['valid_options'].split(',') if v.strip()]
        if new_value not in valid_list:
            return False, f"'{new_value}' not in {valid_list}."

    # 4) Cross-field constraints
    if 'constraint' in desc:
        ok, err = validate_constraint(new_value, desc['constraint'], section, param, config, descriptor)
        if not ok:
            return False, err

    return True, ""

def show_error(stdscr, msg):
    """Pop up an error message until user presses a key."""
    stdscr.clear()
    stdscr.addstr(0, 0, "ERROR:")
    stdscr.addstr(1, 0, msg)
    stdscr.addstr(3, 0, "Press any key to continue...")
    stdscr.refresh()
    stdscr.getch()

def curses_main(stdscr, original_config, descriptor):
    """
    Main TUI. We create a working copy of config. 
    """
    print("DEBUG: Entering curses_main()")
    current_config = copy_config(original_config)

    curses.curs_set(0)
    stdscr.keypad(True)

    sections = list(current_config.sections())
    menu_items = sections + [SPECIAL_SAVE_EXIT, SPECIAL_EXIT_NO_SAVE]

    idx = 0
    editing = True

    while editing:
        stdscr.clear()
        stdscr.addstr(0, 0, "Use D-Pad: UP/DOWN/LEFT/RIGHT. (Right=Select)")
        stdscr.addstr(1, 0, "Select a section or action below.")
        
        for i, item in enumerate(menu_items):
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(3 + i, 2, item)
                stdscr.attroff(curses.A_REVERSE)
            else:
                stdscr.addstr(3 + i, 2, item)

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            print(f"DEBUG: Main menu RIGHT pressed on '{chosen}'")
            if chosen == SPECIAL_SAVE_EXIT:
                print("DEBUG: User selected Save & Exit")
                return current_config
            elif chosen == SPECIAL_EXIT_NO_SAVE:
                print("DEBUG: User selected Exit w/o Saving")
                return None
            else:
                edit_section(stdscr, current_config, descriptor, chosen)
        elif key == curses.KEY_LEFT:
            print("DEBUG: Main menu LEFT pressed, doing nothing at top level.")
            pass

        # Extra: old shortcuts
        if key in [ord('s'), ord('S')]:
            print("DEBUG: Shortcut 's' => Save & Exit.")
            return current_config
        if key in [ord('q'), ord('Q')]:
            print("DEBUG: Shortcut 'q' => Exit w/o Saving.")
            return None

def edit_section(stdscr, config, descriptor, section):
    """List parameters in this section. Up/Down, Right=edit, Left=back."""
    print(f"DEBUG: Entering edit_section({section})")
    params = list(config[section].keys())
    idx = 0
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, f"[{section}] - LEFT=Back, RIGHT=Edit")
        for i, p in enumerate(params):
            val = config.get(section, p, fallback="")
            line = f"{p} = {val}"
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(2 + i, 2, line)
                stdscr.attroff(curses.A_REVERSE)
            else:
                stdscr.addstr(2 + i, 2, line)
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            idx = (idx - 1) % len(params)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(params)
        elif key == curses.KEY_LEFT:
            print(f"DEBUG: edit_section({section}) -> LEFT => back to main menu")
            break
        elif key == curses.KEY_RIGHT:
            print(f"DEBUG: edit_section({section}) -> RIGHT => editing param '{params[idx]}'")
            edit_parameter(stdscr, config, descriptor, section, params[idx])
        else:
            pass

def edit_parameter(stdscr, config, descriptor, section, param):
    """Handle the input for a single parameter."""
    print(f"DEBUG: Entering edit_parameter({section}.{param})")
    desc = descriptor.get(section, {}).get(param, {})
    help_text = desc.get('help','')
    current_val = config.get(section, param, fallback="")

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, f"Editing: [{section}.{param}] (LEFT=Cancel/back, RIGHT=Proceed)")
        stdscr.addstr(1, 0, f"Help: {help_text}")
        stdscr.addstr(2, 0, f"Current value: '{current_val}'")
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_LEFT:
            print(f"DEBUG: edit_parameter({section}.{param}) -> LEFT => cancel/back")
            return
        elif key == curses.KEY_RIGHT:
            new_val = handle_parameter_input(stdscr, config, descriptor, section, param)
            if new_val is None:
                print(f"DEBUG: edit_parameter({section}.{param}) => user canceled input")
                return

            valid, err = validate_value(section, param, new_val, descriptor, config)
            if valid:
                print(f"DEBUG: Successfully validated new value '{new_val}' for {section}.{param}")
                config.set(section, param, new_val)
                return
            else:
                print(f"DEBUG: Validation error => {err}")
                show_error(stdscr, err)

def handle_parameter_input(stdscr, config, descriptor, section, param):
    """
    Dispatch input style based on param type. Return new value or None if canceled.
    """
    print(f"DEBUG: handle_parameter_input({section}.{param})")
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
        # default free text
        return free_text_input(stdscr, current_val)

def multi_select_menu(stdscr, valid_opts, selected_set, allow_custom):
    """
    D-pad multi select. 
    """
    print(f"DEBUG: multi_select_menu() with valid_opts={valid_opts}, selected={selected_set}, allow_custom={allow_custom}")
    menu_items = ["<EMPTY>"] + valid_opts
    if allow_custom:
        menu_items.append("[Add custom]")
    menu_items.append("[Done]")
    idx = 0

    def is_selected(opt):
        if opt == "<EMPTY>":
            return (len(selected_set) == 0)
        return (opt in selected_set)

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Multi-select: UP/DOWN=move, RIGHT=toggle/select, LEFT=cancel")
        row = 2
        for i, opt in enumerate(menu_items):
            if opt == "<EMPTY>":
                mark = "[x]" if (len(selected_set) == 0) else "[ ]"
            elif opt in selected_set:
                mark = "[x]"
            elif opt.startswith("["):
                mark = "  "
            else:
                mark = "[ ]"

            if i == idx:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(row+i, 2, f"{mark} {opt}")
                stdscr.attroff(curses.A_REVERSE)
            else:
                stdscr.addstr(row+i, 2, f"{mark} {opt}")
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_LEFT:
            print("DEBUG: multi_select_menu() => canceled with LEFT")
            return None
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            print(f"DEBUG: multi_select_menu() => RIGHT on '{chosen}'")
            if chosen == "<EMPTY>":
                selected_set.clear()
            elif chosen == "[Add custom]":
                new_val = free_text_input(stdscr, "")
                if new_val is not None and new_val.strip():
                    selected_set.add(new_val.strip())
            elif chosen == "[Done]":
                print("DEBUG: multi_select_menu() => DONE, returning set")
                return selected_set
            else:
                # normal valid_opt
                if chosen in selected_set:
                    selected_set.remove(chosen)
                else:
                    if len(selected_set) == 0:
                        # was <EMPTY>, now picking
                        pass
                    selected_set.add(chosen)

def toggle_menu_0_1(stdscr, current_val):
    """
    D-pad toggle between '0' and '1', plus <EMPTY>.
    """
    print(f"DEBUG: toggle_menu_0_1(current_val={current_val})")
    items = ["<EMPTY>", "0", "1", "[Done]"]
    idx = 0
    if current_val.strip() == "":
        idx = 0
    elif current_val == "0":
        idx = 1
    elif current_val == "1":
        idx = 2

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Toggle 0/1. Up/Down=move, Right=choose, Left=cancel.")
        row = 2
        for i, v in enumerate(items):
            mark = "->" if i == idx else "  "
            stdscr.addstr(row+i, 2, f"{mark} {v}")
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            idx = (idx - 1) % len(items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(items)
        elif key == curses.KEY_LEFT:
            print("DEBUG: toggle_menu_0_1() => canceled with LEFT")
            return None
        elif key == curses.KEY_RIGHT:
            chosen = items[idx]
            print(f"DEBUG: toggle_menu_0_1() => RIGHT on '{chosen}'")
            if chosen == "[Done]":
                # finalize based on idx
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
    """
    Show a list: <EMPTY>, each valid option, optional [Add custom], then [Done].
    """
    print(f"DEBUG: single_select_menu(current_val={current_val}, valid_list={valid_list}, allow_custom={allow_custom}, is_int={is_int})")
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
        stdscr.clear()
        stdscr.addstr(0, 0, "Select or choose custom. Right=pick, Left=cancel.")
        row = 2
        for i, v in enumerate(menu_items):
            mark = "->" if i == idx else "  "
            stdscr.addstr(row + i, 2, f"{mark} {v}")
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_UP:
            idx = (idx - 1) % len(menu_items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(menu_items)
        elif key == curses.KEY_LEFT:
            print("DEBUG: single_select_menu() => canceled with LEFT")
            return None
        elif key == curses.KEY_RIGHT:
            chosen = menu_items[idx]
            print(f"DEBUG: single_select_menu() => RIGHT on '{chosen}'")
            if chosen == "[Done]":
                # pick the highlighted
                if idx == 0:
                    return ""
                else:
                    # if it's one of the valid_list or empty
                    if menu_items[idx] in valid_list:
                        return menu_items[idx]
                    else:
                        # If user is highlighting [Done] but not on a real item, default to empty
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
                # normal item from valid_list
                return chosen

def ip_port_combo_input(stdscr, current_val, desc):
    """
    For param_type=ip_port_combo. Let user pick or custom IP, then pick or custom port.
    """
    print(f"DEBUG: ip_port_combo_input(current_val={current_val})")
    valid_ips = [x.strip() for x in desc.get('valid_ips','').split(',') if x.strip()]
    allow_ip_custom = desc.get('allow_custom_ip','false').lower() == 'true'
    valid_ports = [x.strip() for x in desc.get('valid_ports','').split(',') if x.strip()]
    allow_port_custom = desc.get('allow_custom_port','false').lower() == 'true'

    ip_str, port_str = "", ""
    if ":" in current_val:
        ip_str, port_str = current_val.split(':',1)
        ip_str, port_str = ip_str.strip(), port_str.strip()

    print(f"DEBUG: ip_port_combo_input => ip_str={ip_str}, port_str={port_str}")

    # Step 1: IP
    new_ip = single_select_menu(stdscr, valid_ips, ip_str, allow_ip_custom, is_int=False)
    if new_ip is None:
        print("DEBUG: ip_port_combo_input => user canceled IP input")
        return None
    # Step 2: Port
    new_port = single_select_menu(stdscr, valid_ports, port_str, allow_port_custom, is_int=True)
    if new_port is None:
        print("DEBUG: ip_port_combo_input => user canceled PORT input")
        return None

    if new_ip.strip() == "" or new_port.strip() == "":
        return ""
    return f"{new_ip}:{new_port}"

def free_text_input(stdscr, current_val):
    """
    Provide a text input box. Arrow keys: LEFT=cancel, ENTER=confirm.
    """
    print(f"DEBUG: free_text_input(current_val={current_val})")
    curses.echo()
    stdscr.clear()
    stdscr.addstr(0,0,"Enter new value (ENTER=confirm, LEFT=cancel). Current:")
    stdscr.addstr(1,0, current_val)
    stdscr.addstr(3,0,"New value: ")
    stdscr.refresh()

    win = curses.newwin(1,60,3,11)
    user_input = list()
    pos = 0

    while True:
        ch = stdscr.getch()
        if ch == curses.KEY_LEFT:
            print("DEBUG: free_text_input => canceled with LEFT")
            curses.noecho()
            return None
        elif ch in [curses.KEY_ENTER, 10, 13]:
            curses.noecho()
            return "".join(user_input)
        elif ch in [curses.KEY_BACKSPACE, 127]:
            if user_input:
                user_input.pop()
                pos -= 1
                win.clear()
                win.addstr(0,0,"".join(user_input))
                stdscr.refresh()
        else:
            c = chr(ch)
            user_input.append(c)
            pos += 1
            win.addstr(0,0,"".join(user_input))
            stdscr.refresh()

def main():
    print("DEBUG: Starting main()")
    # Check descriptor
    if not os.path.exists(DESCRIPTOR_FILE):
        print(f"Descriptor '{DESCRIPTOR_FILE}' not found.")
        return
    # Check config
    if not os.path.exists(CONFIG_FILE):
        print(f"Config file '{CONFIG_FILE}' not found.")
        return

    print("DEBUG: Loading config and descriptor...")
    original_config = load_config()
    descriptor = load_descriptor()

    print("DEBUG: Starting curses.wrapper...")
    try:
        final_config = curses.wrapper(curses_main, original_config, descriptor)
    except Exception as e:
        print("DEBUG: Caught Exception during curses.wrapper:")
        traceback.print_exc()
        return

    if final_config is None:
        print("Exited without saving. No changes applied.")
    else:
        write_config(final_config)
        print("Saved & exited. Changes written.")

if __name__ == "__main__":
    main()
