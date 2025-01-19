#!/usr/bin/env python3
"""
add_shortcut.py

Closes Steam, adds a new Non-Steam shortcut to shortcuts.vdf, then restarts Steam.
No external 'python-vdf' module needed—uses a minimal built-in parser for the binary VDF format.
"""

import os
import sys
import struct
import subprocess
import time

###############################################################################
# 1. Gracefully close Steam
###############################################################################

def close_steam():
    """Close Steam gracefully via `steam --shutdown` and wait for it to exit."""
    print("Shutting down Steam...")
    # This works on many Linux systems (including Steam Deck Desktop Mode):
    subprocess.run(["steam", "--shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait until Steam processes have fully exited
    # We'll check repeatedly if "steam" is still running.
    for _ in range(30):
        # If "steam" is not found in pgrep, break
        ret = subprocess.run(["pgrep", "-x", "steam"], stdout=subprocess.DEVNULL)
        if ret.returncode != 0:
            # pgrep didn't find any "steam" process => means it's shut down
            print("Steam is no longer running.")
            return
        time.sleep(1)
    print("Warning: Steam may still be running after 30 seconds of waiting.")


###############################################################################
# 2. Find the shortcuts.vdf file
###############################################################################

def find_shortcuts_vdf():
    """
    Return the path to shortcuts.vdf for the first numeric Steam user ID found, or None if missing.
    Typical path on Linux: ~/.local/share/Steam/userdata/12345678/config/shortcuts.vdf
    """
    steam_dir = os.path.expanduser("~/.local/share/Steam")
    userdata_dir = os.path.join(steam_dir, "userdata")
    if not os.path.isdir(userdata_dir):
        return None
    
    # Pick the first numeric user directory
    for name in os.listdir(userdata_dir):
        if name.isdigit():
            shortcuts_path = os.path.join(userdata_dir, name, "config", "shortcuts.vdf")
            if os.path.isfile(shortcuts_path):
                return shortcuts_path
    
    return None


###############################################################################
# 3. Minimal parser/serializer for the "binary VDF" format
###############################################################################
#
# Steam's shortcuts.vdf is a binary key-value format that differs from the
# text-based VDF. We'll implement just enough reading/writing to handle the
# typical fields in shortcuts.

TYPE_END = 0         # Indicates end of an object
TYPE_STRING = 1      # A UTF-8 string
TYPE_SUBKEY = 2      # A nested object (dictionary)
TYPE_WIDE_STRING = 3 # Not typically used in shortcuts
TYPE_INT32 = 6
TYPE_INT64 = 7
# Some references say 7 is int64, 8 might also appear. We'll handle if we see them.

def read_cstring(f):
    """Read a zero-terminated byte string from file, return as Python str (UTF-8)."""
    byte_arr = []
    while True:
        c = f.read(1)
        if not c or c == b"\x00":
            break
        byte_arr.append(c)
    return b"".join(byte_arr).decode("utf-8", errors="replace")

def write_cstring(f, s):
    """Write a Python string as zero-terminated UTF-8 to file."""
    f.write(s.encode("utf-8", errors="replace"))
    f.write(b"\x00")

def parse_vdf_object(f):
    """
    Recursively parse a VDF object until TYPE_END.
    Returns a dict of key -> value (value can be str, int, or a sub-dict).
    """
    obj = {}
    
    while True:
        # Read type byte
        t = f.read(1)
        if not t:
            # Unexpected EOF
            break
        
        t_val = t[0]
        if t_val == TYPE_END:
            # End of this object
            break
        
        key = read_cstring(f)
        
        if t_val == TYPE_STRING:
            # A string value
            val_str = read_cstring(f)
            obj[key] = val_str
            
        elif t_val == TYPE_SUBKEY:
            # A nested object
            sub_obj = parse_vdf_object(f)
            obj[key] = sub_obj
        
        elif t_val == TYPE_INT32:
            # 32-bit integer
            raw = f.read(4)
            if len(raw) < 4:
                # Broken file
                obj[key] = 0
            else:
                val_int = struct.unpack("<i", raw)[0]
                obj[key] = val_int
                
        elif t_val == TYPE_INT64:
            # 64-bit integer
            raw = f.read(8)
            if len(raw) < 8:
                # Broken file
                obj[key] = 0
            else:
                val_int = struct.unpack("<q", raw)[0]
                obj[key] = val_int
        
        else:
            # Unknown type—try to skip it. For safety, we skip until next TYPE_END?
            # Realistically, shortcuts.vdf rarely uses anything else.
            print(f"Warning: encountered unknown type {t_val} for key '{key}', skipping.")
            # We'll do a best-effort read: assume it's a string.
            # Then read until next 0. This might break for other types, but it's a fallback.
            _ = read_cstring(f)
            obj[key] = ""
    
    return obj

def write_vdf_object(f, obj):
    """
    Write a dictionary to the VDF file in the same binary structure:
      - For str values => TYPE_STRING
      - For int values => decide int32 vs. int64 if large
      - For dict values => TYPE_SUBKEY
      - End with TYPE_END
    """
    for key, val in obj.items():
        if isinstance(val, dict):
            # Write subkey
            f.write(bytes([TYPE_SUBKEY]))
            write_cstring(f, key)
            write_vdf_object(f, val)
        elif isinstance(val, str):
            f.write(bytes([TYPE_STRING]))
            write_cstring(f, key)
            write_cstring(f, val)
        elif isinstance(val, int):
            # Decide 32-bit vs 64-bit.  If fits in signed 32-bit, use int32; else int64
            if -2**31 <= val <= 2**31-1:
                f.write(bytes([TYPE_INT32]))
                write_cstring(f, key)
                f.write(struct.pack("<i", val))
            else:
                f.write(bytes([TYPE_INT64]))
                write_cstring(f, key)
                f.write(struct.pack("<q", val))
        else:
            # Fallback to string if something else
            f.write(bytes([TYPE_STRING]))
            write_cstring(f, key)
            write_cstring(f, str(val))
    
    # Write the end of object marker
    f.write(bytes([TYPE_END]))

def load_shortcuts_vdf(path):
    """Load the entire shortcuts.vdf into a Python dict."""
    with open(path, "rb") as f:
        return parse_vdf_object(f)

def save_shortcuts_vdf(path, data):
    """Save the Python dict to shortcuts.vdf in the correct binary format."""
    with open(path, "wb") as f:
        write_vdf_object(f, data)


###############################################################################
# 4. The main routine: close Steam, modify shortcuts, restart Steam
###############################################################################

def main():
    # 1. Close Steam
    close_steam()
    
    # 2. Find shortcuts.vdf
    shortcuts_path = find_shortcuts_vdf()
    if not shortcuts_path:
        print("ERROR: Could not find shortcuts.vdf. Is Steam installed?")
        sys.exit(1)
    print(f"Found shortcuts.vdf at: {shortcuts_path}")
    
    # 3. Load the existing shortcuts data
    data = load_shortcuts_vdf(shortcuts_path)
    
    # By convention, the "shortcuts" dictionary is inside data["shortcuts"] if it exists
    # If "shortcuts" doesn't exist, create it
    if "shortcuts" not in data:
        data["shortcuts"] = {}
    shortcuts = data["shortcuts"]
    
    # The keys under data["shortcuts"] are "0", "1", "2", etc.
    # Find the highest numeric key so we know the next index
    if len(shortcuts) == 0:
        next_index = 0
    else:
        existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
        next_index = max(existing_indices) + 1
    
    # 4. Build the new shortcut entry you want to add
    # Adjust "appname", "exe", etc. as needed
    new_shortcut = {
        "appname": "WFB Script",
        "exe": "/home/deck/steamdeck/steam_wfb.sh",
        "StartDir": "/home/deck/steamdeck",
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": "",
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "LastPlayTime": 0,
        "FlatpakAppID": "",  # Steam Deck field (often just empty)
        # Optionally, you can include tags:
        "tags": {
            "0": "favorite"  # Example: put it in "Favorites"
        }
    }
    
    # 5. Insert the new shortcut
    shortcuts[str(next_index)] = new_shortcut
    
    # 6. Save the updated data back to shortcuts.vdf
    print(f"Adding new shortcut at index {next_index}: '{new_shortcut['appname']}'")
    save_shortcuts_vdf(shortcuts_path, data)
    print("Successfully updated shortcuts.vdf.")
    
    # 7. Restart Steam
    print("Restarting Steam...")
    subprocess.Popen(["steam"])
    print("Done. Steam is launching in the background.")

if __name__ == "__main__":
    main()

