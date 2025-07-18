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
IMAGE_NAME = "docker-ce" # Using Hetzner's pre-installed Docker CE image
LOCATION = "fsn1" # e.g., "nbg1", "hel1". Note: Hetzner Cloud API typically uses broader locations like 'fsn1', not specific data centers like 'fsn1-dc14' for server creation.
WORKER_PRIMARY_IP = os.getenv("WORKER_PRIMARY_IP") # The fixed public IP address to assign to the VPS
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
    return ssh_keys[0] # Return the SSHKey object

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
        # --- (Your validation code remains the same) ---
        if not HCLOUD_TOKEN:
            raise Exception("HCLOUD_TOKEN environment variable not set. Please set it in your .env file or shell.")
        if not SSH_KEY_PATH:
            raise Exception("SSH_KEY_PATH environment variable not set. Please set it in your .env file or shell.")
        if not WORKER_PRIMARY_IP:
            raise Exception("WORKER_PRIMARY_IP environment variable not set. Please set it in your .env file or shell.")
        if not SERVER_IP:
            raise Exception("SERVER_IP environment variable not set. Please set it in your .env file or shell.")

        # --- (Your .env file processing remains the same) ---
        local_env_content = ""
        try:
            with open(".env", "r") as f:
                local_env_content = f.read()
        except FileNotFoundError:
            print("Warning: .env file not found in the current directory. Ensure all necessary variables are set as system environment variables.")
        
        if "DB_DSN=" in local_env_content:
            lines = local_env_content.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("DB_DSN="):
                    lines[i] = line.replace("@db:", f"@{SERVER_IP}:")
                    print(f"Modified DB_DSN in .env content: {lines[i]}")
                    break
            local_env_content = "\n".join(lines)

        # 1. Get SSH Key Object
        ssh_key_obj = get_ssh_key_id("pricemice-worker-key")

        # 2. Define user_data for initial VPS setup
        user_data_script = f"""
        #cloud-config
        packages:
          - git
          - make
          - python3-pip
        runcmd:
          - [ sh, -c, "git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}" ]
        write_files:
          - path: {PROJECT_DIR_ON_VPS}/.env
            permissions: '0644'
            content: |
              {local_env_content}
        """

        # 3. Get objects for server creation
        print("Gathering resources for server creation...")
        server_type_obj = client.server_types.get_by_name(SERVER_TYPE)
        image_obj = client.images.get_by_name(IMAGE_NAME)
        location_obj = client.locations.get_by_name(LOCATION)

        # 4. Get the Primary IP object and check its status
        primary_ips_page = client.primary_ips.get_list(ip=WORKER_PRIMARY_IP)
        if not primary_ips_page.primary_ips:
            raise Exception(f"Primary IP '{WORKER_PRIMARY_IP}' not found in Hetzner Cloud. Aborting.")
        
        primary_ip_obj = primary_ips_page.primary_ips[0]
        
        # Check if the Primary IP is already assigned to a different, existing server
        if primary_ip_obj.assignee_id is not None:
            print(f"Warning: Primary IP '{WORKER_PRIMARY_IP}' is already assigned to resource ID {primary_ip_obj.assignee_id}. It will be unassigned and reassigned.")
            # You might want to add logic here to unassign it first if needed,
            # though the assign call should handle this.
            # client.primary_ips.unassign(primary_ip_obj)

        # 5. Provision a VPS (without assigning the primary IP yet)
        print(f"Creating server {SERVER_NAME}...")
        server_create_result = client.servers.create(
            name=SERVER_NAME,
            server_type=server_type_obj,
            image=image_obj,
            location=location_obj,
            ssh_keys=[ssh_key_obj],
            user_data=user_data_script,
            start_after_create=True
            # The incorrect 'assign_primary_ip' argument is removed
        )
        server = server_create_result.server
        action = server_create_result.action

        print(f"Server {SERVER_NAME} creation initiated. Waiting for action to complete...")
        action.wait_until_finished() # Wait for the creation action to finish
        server = client.servers.get_by_id(server.id) # Refresh the server object to get the latest status
        print(f"Server {SERVER_NAME} is created with status: {server.status} and temp IP: {server.public_net.ipv4.ip}")

        # 6. Assign the Primary IP to the new server
        print(f"Assigning Primary IP {WORKER_PRIMARY_IP} to server {server.name} ({server.id})...")
        assign_action = primary_ip_obj.assign(assignee_id=server.id, assignee_type='server')
        assign_action.wait_until_finished() # Wait for the assignment to complete
        print(f"Primary IP {WORKER_PRIMARY_IP} assigned successfully.")

        # 7. SSH into the new VPS using the WORKER_PRIMARY_IP
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        private_key = paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)

        print("Connecting via SSH...")
        for i in range(15):
            try:
                ssh_client.connect(hostname=WORKER_PRIMARY_IP, username="root", pkey=private_key, timeout=10)
                print("SSH connected.")
                break
            except Exception as e:
                print(f"SSH connection failed ({i+1}/15): {e}. Retrying in 10 seconds...")
                time.sleep(10)
        else:
            raise Exception("Could not establish SSH connection to the VPS after multiple retries.")

        # 8. Run the Job on VPS
        run_remote_command(ssh_client, f"cd {PROJECT_DIR_ON_VPS} && {MAKE_COMMAND}", "data ingestion job")

        ssh_client.close()
        print("SSH connection closed.")

    except hcloud.APIException as e:
        print(f"Hetzner Cloud API Error: Code={e.code}, Message={e.message}, Details={e.details}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 9. De-provision the VPS
        if server:
            print(f"Deleting server {SERVER_NAME}...")
            try:
                delete_action = client.servers.delete(server)
                delete_action.wait_until_finished()
                print(f"Server {SERVER_NAME} deleted.")
                # The Primary IP is now unassigned and remains in your project for future use.
            except Exception as e:
                print(f"Error deleting server {SERVER_NAME}: {e}")

# --- (The rest of your script (get_ssh_key_id, run_remote_command, __main__) remains the same) ---
if __name__ == "__main__":
    main()