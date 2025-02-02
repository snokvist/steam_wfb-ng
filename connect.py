#!/usr/bin/env python3
import socket
import time
import logging
import sys
import os
import argparse
import tempfile
import shutil
import tarfile
import io
import base64
import hashlib

def compute_sha1(file_path):
    """Compute the SHA1 hash of the given file."""
    hash_obj = hashlib.sha1()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()

def create_tar_gz_archive(source_dir, arcname):
    """
    Create a tar.gz archive (in memory) of the source directory.

    Parameters:
      source_dir: Directory to archive.
      arcname: The name to be used as the top-level directory in the archive.
             
    Returns:
      The bytes of the tar.gz archive.
    """
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode='w:gz') as tar:
        tar.add(source_dir, arcname=arcname)
    bio.seek(0)
    return bio.read()

def compute_checksums(directory):
    """
    Recursively compute SHA1 checksums for all files in a directory.
    
    Returns:
      A list of lines in the format "sha1hash  relative_path".
    """
    checksum_lines = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, start=directory)
            sha1_hash = compute_sha1(file_path)
            checksum_lines.append(f"{sha1_hash}  {rel_path}")
    return checksum_lines

def connect_to_server(host, port, max_retries, conn_timeout, op_timeout):
    """Connect to the server with retries; return the socket and its file-like wrapper."""
    sock = None
    for attempt in range(1, max_retries + 1):
        try:
            logging.debug(f"Attempt {attempt}: Connecting to {host}:{port} ...")
            sock = socket.create_connection((host, port), timeout=conn_timeout)
            logging.debug("Connection established.")
            break
        except Exception as e:
            logging.debug(f"Attempt {attempt} failed: {e}")
            time.sleep(1)
    if not sock:
        logging.error("Unable to connect to the server after multiple attempts.")
        sys.exit(1)
    sock.settimeout(op_timeout)
    sock_file = sock.makefile('rwb')
    return sock, sock_file

def prepare_archive(folder_path, archive_name):
    """
    Prepare the archive from the given folder.

    This function:
      - Verifies the folder exists.
      - Creates a temporary directory.
      - Creates a subfolder named as archive_name inside it.
      - Copies all contents from folder_path to the new folder.
      - Computes SHA1 checksums and writes them to checksum.txt.
      - Creates a tar.gz archive (with the given archive_name as the top-level folder).
      - Base64-encodes the archive and returns the resulting string.
    """
    if not os.path.isdir(folder_path):
        logging.error(f"Provided folder '{folder_path}' is not a valid directory.")
        sys.exit(1)
    folder_abs_path = os.path.abspath(folder_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        logging.debug(f"Created temporary directory: {tmpdir}")
        dest_folder = os.path.join(tmpdir, archive_name)
        os.makedirs(dest_folder)
        logging.debug(f"Created temporary base folder for archive: {dest_folder}")
        # Copy all files and subfolders from the source folder.
        for item in os.listdir(folder_abs_path):
            s = os.path.join(folder_abs_path, item)
            d = os.path.join(dest_folder, item)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        logging.debug(f"Copied contents of '{folder_abs_path}' into '{dest_folder}'")
        
        # Compute checksums and write to checksum.txt.
        logging.debug("Computing SHA1 checksums for files in the folder.")
        checksum_lines = compute_checksums(dest_folder)
        checksum_file = os.path.join(dest_folder, "checksum.txt")
        with open(checksum_file, 'w') as f:
            for line in checksum_lines:
                f.write(line + "\n")
        logging.debug(f"Wrote checksum file: {checksum_file}")
        
        # Create the tar.gz archive.
        logging.debug("Creating tar.gz archive of the folder.")
        archive_data = create_tar_gz_archive(dest_folder, arcname=archive_name)
        logging.debug(f"Archive created; size = {len(archive_data)} bytes.")
        
        # Base64-encode the archive.
        encoded_archive = base64.b64encode(archive_data).decode('utf-8')
        logging.debug(f"Base64-encoded archive length: {len(encoded_archive)} characters.")
        return encoded_archive

def bind_operation(folder_path, args):
    """
    Perform the BIND operation:
      - Connect to the server.
      - Send the VERSION command and verify the response.
      - Prepare the archive from the folder.
      - Send the BIND command with the encoded archive.
      - Wait for and process the final response.
    """
    host = args.ip
    port = args.port
    max_retries = args.max_retries
    conn_timeout = args.conn_timeout
    op_timeout = args.timeout

    sock, sock_file = connect_to_server(host, port, max_retries, conn_timeout, op_timeout)
    try:
        # Send VERSION command.
        version_request = "VERSION\n"
        logging.debug(f"Sending: {version_request.strip()}")
        sock_file.write(version_request.encode('utf-8'))
        sock_file.flush()
        try:
            response_line = sock_file.readline().decode('utf-8').strip()
        except socket.timeout:
            logging.error("Timeout occurred while waiting for response after sending VERSION.")
            sys.exit(1)
        logging.debug(f"Received: {response_line}")
        parts = response_line.split('\t')
        if len(parts) < 2:
            logging.error("Invalid response format; expected two tab-separated fields.")
            sys.exit(1)
        status, version = parts[0], parts[1]
        if status != "OK":
            logging.error(f"Unable to fetch version; received status: {status}")
            sys.exit(1)
        logging.debug(f"Server version: {version}")

        # Determine the archive base folder name from the folder path.
        archive_name = os.path.normpath(folder_path).split(os.sep)[0]
        logging.debug(f"BIND operation: Archive base folder will be: '{archive_name}'")
        encoded_archive = prepare_archive(folder_path, archive_name)
        
        # Send the BIND command with the encoded archive.
        bind_message = f"BIND\t{encoded_archive}\n"
        logging.debug("Sending BIND message with the archive.")
        sock_file.write(bind_message.encode('utf-8'))
        sock_file.flush()
        
        # Wait for the final response.
        try:
            response_line = sock_file.readline().decode('utf-8').strip()
        except socket.timeout:
            logging.error("Timeout occurred while waiting for final response from the server.")
            sys.exit(1)
        logging.debug(f"Received response after BIND: {response_line}")
        parts = response_line.split('\t', 1)
        status = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        if status != "OK":
            logging.error(f"BIND failed: {msg}")
            sys.exit(1)
        logging.debug("BIND succeeded.")
    finally:
        sock_file.close()
        sock.close()
        logging.debug("Connection closed.")

def flash_operation(archive_file, args):
    """
    Perform the FLASH operation:
      - Verify the provided archive file exists.
      - Read the tar.gz archive file, base64-encode its contents.
      - Connect to the server.
      - Send the VERSION command and verify the response.
      - Send the FLASH command with the encoded archive.
      - Wait for and process the final response.
    """
    if not os.path.isfile(archive_file):
        logging.error(f"Provided archive file '{archive_file}' does not exist or is not a file.")
        sys.exit(1)
    
    try:
        with open(archive_file, 'rb') as f:
            file_data = f.read()
    except Exception as e:
        logging.error(f"Failed to read file '{archive_file}': {e}")
        sys.exit(1)
    
    encoded_archive = base64.b64encode(file_data).decode('utf-8')
    logging.debug(f"FLASH operation: Read and base64-encoded file '{archive_file}' ({len(file_data)} bytes, {len(encoded_archive)} characters).")
    
    host = args.ip
    port = args.port
    max_retries = args.max_retries
    conn_timeout = args.conn_timeout
    op_timeout = args.timeout

    sock, sock_file = connect_to_server(host, port, max_retries, conn_timeout, op_timeout)
    try:
        # Send VERSION command.
        version_request = "VERSION\n"
        logging.debug(f"Sending: {version_request.strip()}")
        sock_file.write(version_request.encode('utf-8'))
        sock_file.flush()
        try:
            response_line = sock_file.readline().decode('utf-8').strip()
        except socket.timeout:
            logging.error("Timeout occurred while waiting for response after sending VERSION.")
            sys.exit(1)
        logging.debug(f"Received: {response_line}")
        parts = response_line.split('\t')
        if len(parts) < 2:
            logging.error("Invalid response format; expected two tab-separated fields.")
            sys.exit(1)
        status, version = parts[0], parts[1]
        if status != "OK":
            logging.error(f"Unable to fetch version; received status: {status}")
            sys.exit(1)
        logging.debug(f"Server version: {version}")

        # For FLASH, we send the archive as-is.
        flash_message = f"FLASH\t{encoded_archive}\n"
        logging.debug("Sending FLASH message with the archive file.")
        sock_file.write(flash_message.encode('utf-8'))
        sock_file.flush()
        
        # Wait for the final response.
        try:
            response_line = sock_file.readline().decode('utf-8').strip()
        except socket.timeout:
            logging.error("Timeout occurred while waiting for final response from the server.")
            sys.exit(1)
        logging.debug(f"Received response after FLASH: {response_line}")
        parts = response_line.split('\t', 1)
        status = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        if status != "OK":
            logging.error(f"FLASH failed: {msg}")
            sys.exit(1)
        logging.debug("FLASH succeeded.")
    finally:
        sock_file.close()
        sock.close()
        logging.debug("Connection closed.")

def simple_command_operation(command, args):
    """
    Perform an operation that sends a single command (e.g. UNBIND or INFO)
    and waits for the response.
    """
    host = args.ip
    port = args.port
    max_retries = args.max_retries
    conn_timeout = args.conn_timeout
    op_timeout = args.timeout

    sock, sock_file = connect_to_server(host, port, max_retries, conn_timeout, op_timeout)
    try:
        command_request = f"{command}\n"
        logging.debug(f"Sending: {command_request.strip()}")
        sock_file.write(command_request.encode('utf-8'))
        sock_file.flush()
        try:
            response_line = sock_file.readline().decode('utf-8').strip()
        except socket.timeout:
            logging.error(f"Timeout occurred while waiting for response after sending {command}.")
            sys.exit(1)
        logging.debug(f"Received: {response_line}")
        # Print the response for the user.
        print(response_line)
    finally:
        sock_file.close()
        sock.close()
        logging.debug("Connection closed.")

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Perform one of several operations with the server:\n\n"
            "Operations:\n"
            "  --bind    Archive a folder and send the BIND command (the folder will be archived on-the-fly).\n"
            "  --flash   Send an already-created tar.gz archive file with the FLASH command.\n"
            "  --unbind  Send the UNBIND command.\n"
            "  --info    Send the INFO command (server reply includes additional info from /etc/is-release or /etc/os-release).\n\n"
            "Usage:\n"
            "  For BIND:  script.py [--bind] <folder>\n"
            "  For FLASH: script.py --flash <archive_file.tgz>\n"
            "  For UNBIND or INFO: script.py --unbind|--info\n\n"
            "If no arguments are provided, this help message is displayed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("folder", nargs="?", help="Path to the folder to archive (for BIND) or to the tar.gz archive file (for FLASH)")
    parser.add_argument("--bind", action="store_true", help="Perform BIND operation (archive folder and send to server)")
    parser.add_argument("--flash", action="store_true", help="Perform FLASH operation (send an existing tar.gz archive file)")
    parser.add_argument("--unbind", action="store_true", help="Perform UNBIND operation")
    parser.add_argument("--info", action="store_true", help="Perform INFO operation")
    parser.add_argument("--ip", "-i", default="10.5.99.2", help="IP address of the server (default: 10.5.99.2)")
    parser.add_argument("--port", "-p", type=int, default=5555, help="Port number of the server (default: 5555)")
    parser.add_argument("--max-retries", "-r", type=int, default=30, help="Maximum number of connection retries (default: 30)")
    parser.add_argument("--timeout", "-t", type=int, default=60, help="Timeout (in seconds) for socket operations after connection (default: 60)")
    parser.add_argument("--conn-timeout", "-c", type=int, default=5, help="Timeout (in seconds) for each connection attempt (default: 5)")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    # Enforce that only one of the operation flags is used.
    op_flags = [args.bind, args.flash, args.unbind, args.info]
    if sum(bool(x) for x in op_flags) > 1:
        parser.error("Please specify only one of --bind, --flash, --unbind, or --info.")

    # Decide the operation mode.
    if args.unbind:
        operation = "unbind"
    elif args.info:
        operation = "info"
    elif args.flash:
        operation = "flash"
    elif args.bind or args.folder:
        operation = "bind"
    else:
        parser.print_help()
        sys.exit(1)

    # For operations that require a folder or file, ensure the argument is provided.
    if operation in ("bind", "flash"):
        if not args.folder:
            parser.error(f"{operation.upper()} operation requires a folder/archive argument.")

    if operation == "bind":
        bind_operation(args.folder, args)
    elif operation == "flash":
        flash_operation(args.folder, args)
    elif operation == "unbind":
        simple_command_operation("UNBIND", args)
    elif operation == "info":
        simple_command_operation("INFO", args)

if __name__ == "__main__":
    # Set up debugging logging.
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    main()

