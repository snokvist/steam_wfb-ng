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
        # The entire source_dir will appear under 'arcname' in the archive.
        tar.add(source_dir, arcname=arcname)
    bio.seek(0)
    return bio.read()

def compute_checksums(directory):
    """
    Recursively compute SHA1 checksums for all files in a directory.
    
    Returns:
      A list of lines in the format "sha1hash  relative_path", where
      relative_path is computed relative to the given directory.
    """
    checksum_lines = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, start=directory)
            sha1_hash = compute_sha1(file_path)
            checksum_lines.append(f"{sha1_hash}  {rel_path}")
    return checksum_lines

def main():
    parser = argparse.ArgumentParser(
        description="Archive a folder (including its subfolder structure) and compute SHA1 checksums, "
                    "then send it to a server. The server must respond to a VERSION command and accept a "
                    "BIND command with the archive."
    )
    parser.add_argument("folder", nargs="?", help="Path to the folder to archive and send.")
    parser.add_argument("--ip", "-i", default="10.5.99.2", help="IP address of the server (default: 10.5.99.2)")
    parser.add_argument("--port", "-p", type=int, default=5555, help="Port number of the server (default: 5555)")
    parser.add_argument("--max-retries", "-r", type=int, default=30, help="Maximum number of connection retries (default: 30)")
    parser.add_argument("--timeout", "-t", type=int, default=60, help="Timeout (in seconds) for socket operations after connection (default: 60)")
    parser.add_argument("--conn-timeout", "-c", type=int, default=5, help="Timeout (in seconds) for each connection attempt (default: 5)")
    
    args = parser.parse_args()
    
    if not args.folder:
        parser.print_help()
        sys.exit(1)
    
    folder_path = args.folder
    if not os.path.isdir(folder_path):
        print(f"Error: Provided folder '{folder_path}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)
    
    # Set up debugging logging.
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    
    host = args.ip
    port = args.port
    max_retries = args.max_retries
    conn_timeout = args.conn_timeout
    operation_timeout = args.timeout

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
    
    # Increase timeout for subsequent socket operations.
    sock.settimeout(operation_timeout)
    sock_file = sock.makefile('rwb')
    
    try:
        # 1. Send the "VERSION" command.
        version_request = "VERSION\n"
        logging.debug(f"Sending: {version_request.strip()}")
        sock_file.write(version_request.encode('utf-8'))
        sock_file.flush()
        
        # 2. Read and process the VERSION response.
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
        
        # 3. Prepare the folder for archiving.
        # Determine the desired archive base folder name.
        # For example, if the folder argument is "bind/docker-ssc338q", we want the archive base folder to be "bind".
        archive_name = os.path.normpath(args.folder).split(os.sep)[0]
        logging.debug(f"Archive base folder will be: '{archive_name}'")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logging.debug(f"Created temporary directory: {tmpdir}")
            
            # The source folder (absolute path).
            folder_abs_path = os.path.abspath(folder_path)
            
            # Create a new folder in the temp directory with the name 'archive_name'.
            dest_folder = os.path.join(tmpdir, archive_name)
            os.makedirs(dest_folder)
            logging.debug(f"Created temporary base folder for archive: {dest_folder}")
            
            # Copy the entire content of the provided folder into dest_folder.
            for item in os.listdir(folder_abs_path):
                s = os.path.join(folder_abs_path, item)
                d = os.path.join(dest_folder, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
            logging.debug(f"Copied contents of '{folder_abs_path}' into '{dest_folder}'")
            
            # 4. Compute SHA1 checksums for all files in dest_folder.
            logging.debug("Computing SHA1 checksums for files in the folder.")
            checksum_lines = compute_checksums(dest_folder)
            checksum_file = os.path.join(dest_folder, "checksum.txt")
            with open(checksum_file, 'w') as f:
                for line in checksum_lines:
                    f.write(line + "\n")
            logging.debug(f"Wrote checksum file: {checksum_file}")
            
            # 5. Create a tar.gz archive of dest_folder.
            logging.debug("Creating tar.gz archive of the folder.")
            archive_data = create_tar_gz_archive(dest_folder, arcname=archive_name)
            logging.debug(f"Archive created; size = {len(archive_data)} bytes.")
            
            # 6. Base64-encode the archive (without extra line breaks).
            encoded_archive = base64.b64encode(archive_data).decode('utf-8')
            logging.debug(f"Base64-encoded archive length: {len(encoded_archive)} characters.")
            
            # 7. Send the BIND command with the encoded archive.
            bind_message = f"BIND\t{encoded_archive}\n"
            logging.debug("Sending BIND message with the archive.")
            sock_file.write(bind_message.encode('utf-8'))
            sock_file.flush()
        
        # 8. Wait for the final response.
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
            logging.error(f"Bind failed: {msg}")
            sys.exit(1)
        logging.debug("Bind succeeded.")
        
    finally:
        sock_file.close()
        sock.close()
        logging.debug("Connection closed.")

if __name__ == "__main__":
    main()

