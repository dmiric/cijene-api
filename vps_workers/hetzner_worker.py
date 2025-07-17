# This file will contain the Python script to provision a Hetzner VPS,
# run the data ingestion job, and then spin down the VPS.

import hcloud
import paramiko
import time
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file in the current directory
load_dotenv()

# --- Configuration ---
# It's recommended to load these from environment variables or a secure config management system
HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH") # Path to your SSH private key on the machine running this script
SERVER_NAME = "cijene-ingestion-worker"
SERVER_TYPE = "cpx31"
IMAGE_NAME = "ubuntu-22.04"
LOCATION = "fsn1" # e.g., "nbg1", "hel1". Note: Hetzner Cloud API typically uses broader locations like 'fsn1', not specific data centers like 'fsn1-dc14' for server creation.
FLOATING_IP_ADDRESS = os.getenv("FLOATING_IP_ADDRESS") # The fixed public IP address to assign to the VPS
SERVER_IP = os.getenv("SERVER_IP") # The IP address of the master database server
PROJECT_DIR_ON_VPS = "/root/cijene-api-clone" # Where your project will be cloned on the VPS
MAKE_COMMAND = "make crawl CHAIN=roto,trgovina-krk,lorenco,boso && make import-data" # The command to run on the VPS
# MASTER_DATABASE_URL is no longer needed as DB_DSN in .env will be updated directly

# --- Hetzner Cloud Client ---
client = hcloud.Client(token=HCLOUD_TOKEN)

def get_ssh_key_id(key_name):
    """Retrieves the ID of an SSH key uploaded to Hetzner Cloud by its name."""
    ssh_keys = client.ssh_keys.get_all(name=key_name)
    if not ssh_keys:
        raise Exception(f"SSH key '{key_name}' not found in Hetzner Cloud. Please upload it.")
    return ssh_keys[0].id

def run_remote_command(ssh_client, command, description="command"):
    """Executes a command on the remote VPS and prints its output."""
    print(f"Executing remote {description}: {command}")
    stdin, stdout, stderr = ssh_client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status() # Wait for command to finish
    stdout_output = stdout.read().decode().strip()
    stderr_output = stderr.read().decode().strip()

    if stdout_output:
        print(f"STDOUT:\n{stdout_output}")
    if stderr_output:
        print(f"STDERR:\n{stderr_output}")

    if exit_status != 0:
        raise Exception(f"Remote {description} failed with exit status {exit_status}")
    print(f"Remote {description} completed successfully.")

def main():
    server = None
    try:
        # Validate required environment variables
        if not HCLOUD_TOKEN:
            raise Exception("HCLOUD_TOKEN environment variable not set. Please set it in your .env file or shell.")
        if not SSH_KEY_PATH:
            raise Exception("SSH_KEY_PATH environment variable not set. Please set it in your .env file or shell.")
        if not FLOATING_IP_ADDRESS:
            raise Exception("FLOATING_IP_ADDRESS environment variable not set. Please set it in your .env file or shell.")
        if not SERVER_IP:
            raise Exception("SERVER_IP environment variable not set. Please set it in your .env file or shell.")

        # Read all environment variables from the local .env file
        local_env_content = ""
        try:
            with open(".env", "r") as f:
                local_env_content = f.read()
        except FileNotFoundError:
            print("Warning: .env file not found in the current directory. Ensure all necessary variables are set as system environment variables.")
        
        # Replace @db with SERVER_IP in DB_DSN within the .env content
        if "DB_DSN=" in local_env_content:
            lines = local_env_content.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("DB_DSN="):
                    lines[i] = line.replace("@db:", f"@{SERVER_IP}:")
                    print(f"Modified DB_DSN in .env content: {lines[i]}")
                    break
            local_env_content = "\n".join(lines)

        # 1. Get SSH Key ID
        # Assuming your SSH key is named 'price-mice-deploy-key' in Hetzner Cloud
        ssh_key_id = get_ssh_key_id("price-mice-deploy-key") # Replace with your actual SSH key name

        # 2. Define user_data for initial VPS setup
        # This script will run on the VPS upon first boot
        user_data_script = f"""
        #cloud-config
        packages:
          - git
          - make
          - python3-pip
          - docker.io
          - docker-compose
        runcmd:
          - [ sh, -c, "git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}" ]
          - [ sh, -c, "cd {PROJECT_DIR_ON_VPS} && make setup-docker" ] # Assuming you have a make target for docker setup
        write_files:
          - path: {PROJECT_DIR_ON_VPS}/.env # Create .env file on VPS
            permissions: '0644'
            content: |
              {local_env_content}
        """

        # 3. Provision a VPS
        print(f"Creating server {SERVER_NAME}...")
        server_create_result = client.servers.create(
            name=SERVER_NAME,
            server_type=SERVER_TYPE,
            image=IMAGE_NAME,
            location=LOCATION,
            ssh_keys=[ssh_key_id],
            user_data=user_data_script,
            start_after_create=True
        )
        server = client.servers.get_by_id(server_create_result.id) # Refresh server object

        print(f"Server {SERVER_NAME} created. Waiting for it to become active...")
        while server.status != "running":
            time.sleep(5)
            server = client.servers.get_by_id(server.id)
        print(f"Server {SERVER_NAME} is running at IP: {server.public_net.ipv4.ip}")

        # 4. Assign Floating IP
        print(f"Assigning Floating IP {FLOATING_IP_ADDRESS} to server {SERVER_NAME}...")
        floating_ip = client.floating_ips.get_by_ip(FLOATING_IP_ADDRESS)
        if not floating_ip:
            # If floating IP doesn't exist, create it.
            # Note: This assumes the floating IP is not already created in Hetzner Cloud.
            # If it is, ensure its type (IPv4) and home location match.
            print(f"Floating IP {FLOATING_IP_ADDRESS} not found. Attempting to create it...")
            floating_ip_create_result = client.floating_ips.create(
                type="ipv4",
                home_location=LOCATION,
                name=f"ingestion-worker-ip-{FLOATING_IP_ADDRESS}"
            )
            floating_ip = client.floating_ips.get_by_id(floating_ip_create_result.id)
            print(f"Floating IP {FLOATING_IP_ADDRESS} created.")

        floating_ip.assign(server)
        print(f"Floating IP {FLOATING_IP_ADDRESS} assigned to {SERVER_NAME}.")

        # 5. SSH into the new VPS using the Floating IP
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        private_key = paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)

        print("Connecting via SSH...")
        # Retry SSH connection as it might take a moment for SSH daemon to start
        for i in range(15): # Increased retries
            try:
                ssh_client.connect(hostname=FLOATING_IP_ADDRESS, username="root", pkey=private_key, timeout=10)
                print("SSH connected.")
                break
            except Exception as e:
                print(f"SSH connection failed ({i+1}/15): {e}. Retrying in 10 seconds...")
                time.sleep(10)
        else:
            raise Exception("Could not establish SSH connection to the VPS after multiple retries.")

        # 5. Run the Job on VPS
        # The .env file should be created by user_data, and the repo cloned.
        # Now, execute the make command.
        run_remote_command(ssh_client, f"cd {PROJECT_DIR_ON_VPS} && {MAKE_COMMAND}", "data ingestion job")

        # 6. Retrieve Results (Optional - add your logic here)
        # Example: scp logs or output files back to your local machine/storage
        # You would need to set up scp or sftp using paramiko as well.
        # For instance:
        # sftp_client = ssh_client.open_sftp()
        # sftp_client.get(f"{PROJECT_DIR_ON_VPS}/logs/crawler.log", "local_crawler.log")
        # sftp_client.close()

        ssh_client.close()
        print("SSH connection closed.")

    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)
    finally:
        # 7. De-provision the VPS and detach Floating IP (always try to clean up)
        if server and server.status != "deleted":
            print(f"Deleting server {SERVER_NAME}...")
            try:
                # Detach Floating IP if it was assigned
                if FLOATING_IP_ADDRESS:
                    floating_ip = client.floating_ips.get_by_ip(FLOATING_IP_ADDRESS)
                    if floating_ip and floating_ip.server and floating_ip.server.id == server.id:
                        print(f"Detaching Floating IP {FLOATING_IP_ADDRESS} from {SERVER_NAME}...")
                        floating_ip.unassign()
                        print(f"Floating IP {FLOATING_IP_ADDRESS} unassigned.")
                
                client.servers.delete(server)
                print(f"Server {SERVER_NAME} deleted.")
            except Exception as e:
                print(f"Error deleting server {SERVER_NAME} or detaching Floating IP: {e}")

if __name__ == "__main__":
    main()
