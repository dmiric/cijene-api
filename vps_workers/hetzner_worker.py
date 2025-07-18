# This file will contain the Python script to provision a Hetzner VPS,
# run the data ingestion job, and then spin down the VPS.

import hcloud
from hcloud.servers.domain import ServerCreatePublicNetwork
from hcloud.actions.domain import ActionFailedException
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

def wait_for_action(action, timeout: int = 180):
    """
    Waits for a Hetzner Cloud Action to complete by polling its status,
    with a custom timeout.

    :param action: The hcloud.actions.client.BoundAction object to wait for.
    :param timeout: The maximum time to wait in seconds.
    :raises TimeoutError: If the action does not complete within the timeout.
    :raises ActionFailedException: If the action status becomes 'error'.
    """
    start_time = time.time()
    print(f"Waiting for action '{action.command}' (ID: {action.id}) to complete...", end="", flush=True)

    while action.status == "running":
        # 1. Check for timeout
        if time.time() - start_time > timeout:
            print(" TIMEOUT!")
            raise TimeoutError(f"Action '{action.command}' timed out after {timeout} seconds.")

        # 2. Print progress and wait before polling again
        print(".", end="", flush=True)
        time.sleep(5)  # Poll every 5 seconds

        # 3. Get the latest status from the API
        action.reload()

    # The loop has finished, now check the final status
    if action.status == "success":
        print(" SUCCESS!")
        return # Action completed successfully
    
    if action.status == "error":
        print(" FAILED!")
        # Raise the specific library exception which contains useful details
        raise ActionFailedException(action=action)    

def main():
    """
    Provisions a Hetzner Cloud server, assigns a Primary IP, runs a data
    ingestion job, and then deletes the server.
    """
    server = None
    try:
        # --- 1. Validate required environment variables ---
        print("Validating environment variables...")
        if not HCLOUD_TOKEN:
            raise Exception("HCLOUD_TOKEN environment variable not set.")
        if not SSH_KEY_PATH:
            raise Exception("SSH_KEY_PATH environment variable not set.")
        if not WORKER_PRIMARY_IP:
            raise Exception("WORKER_PRIMARY_IP environment variable not set.")
        if not SERVER_IP:
            raise Exception("SERVER_IP environment variable not set. This is the master database IP.")
        print("Validation successful.")

        # --- 2. Prepare .env content for the remote server ---
        print("Reading local .env file to prepare remote configuration...")
        local_env_content = ""
        try:
            with open(".env", "r") as f:
                local_env_content = f.read()
        except FileNotFoundError:
            print("Warning: .env file not found. Assuming environment variables are set externally.")

        if "DB_DSN=" in local_env_content:
            lines = local_env_content.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("DB_DSN="):
                    original_dsn = line
                    lines[i] = line.replace("@db:", f"@{SERVER_IP}:")
                    print(f"Modified DB_DSN: '{original_dsn}' -> '{lines[i]}'")
                    break
            local_env_content = "\n".join(lines)
        else:
             print("Warning: DB_DSN not found in .env content. The job might fail if it requires it.")

        # --- 3. Gather all required Hetzner Cloud resources ---
        print("Gathering Hetzner Cloud resources...")
        ssh_key_obj = get_ssh_key_id("pricemice-worker-key")
        server_type_obj = client.server_types.get_by_name(SERVER_TYPE)
        image_obj = client.images.get_by_name(IMAGE_NAME)
        location_obj = client.locations.get_by_name(LOCATION)

        primary_ips_page = client.primary_ips.get_list(ip=WORKER_PRIMARY_IP)
        if not primary_ips_page.primary_ips:
            raise Exception(f"Primary IP '{WORKER_PRIMARY_IP}' not found in your Hetzner project.")
        primary_ip_obj = primary_ips_page.primary_ips[0]

        if primary_ip_obj.assignee_id is not None:
             raise Exception(f"Primary IP '{WORKER_PRIMARY_IP}' is already assigned to another resource (ID: {primary_ip_obj.assignee_id}). Please unassign it first.")
        print("All resources located successfully.")

        # --- 4. Define the server configuration ---
        public_net_config = ServerCreatePublicNetwork(ipv4=primary_ip_obj)
        user_data_script = f"""
            #cloud-config
            packages:
            - git
            - make
            runcmd:
            - [ sh, -c, "git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}" ]
            write_files:
            - path: {PROJECT_DIR_ON_VPS}/.env
                permissions: '0644'
                content: |
            {local_env_content}
            """

        # --- 5. Provision the server ---
        print(f"Creating server '{SERVER_NAME}' and assigning Primary IP '{WORKER_PRIMARY_IP}'...")
        server_create_result = client.servers.create(name=SERVER_NAME, server_type=server_type_obj, image=image_obj, location=location_obj, ssh_keys=[ssh_key_obj], user_data=user_data_script, public_net=public_net_config, start_after_create=True)
        server = server_create_result.server
        action = server_create_result.action

        # Call our robust waiting function
        wait_for_action(action, timeout=300)

        server = client.servers.get_by_id(server.id)
        print(f"Server '{SERVER_NAME}' is running with IP: {server.public_net.ipv4.ip}")

        # --- 6. Connect via SSH and execute the job ---
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        private_key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)

        print(f"Attempting to connect to {WORKER_PRIMARY_IP} via SSH...")
        for i in range(15):
            try:
                ssh_client.connect(hostname=WORKER_PRIMARY_IP, username="root", pkey=private_key, timeout=10)
                print("SSH connection established successfully.")
                break
            except Exception as e:
                print(f"SSH connection failed ({i+1}/15): {e}. Retrying in 10 seconds...")
                time.sleep(10)
        else:
            raise Exception("Could not establish SSH connection after multiple retries.")

        run_remote_command(ssh_client, f"cd {PROJECT_DIR_ON_VPS} && {MAKE_COMMAND}", "data ingestion job")

        ssh_client.close()
        print("SSH connection closed.")

    except (hcloud.APIException, ActionFailedException) as e:
        # We now catch ActionFailedException as well
        print(f"HETZNER CLOUD ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"AN UNEXPECTED ERROR OCCURRED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # --- 7. Clean up and de-provision the server ---
        if server:
            print(f"--- Teardown: Deleting server '{SERVER_NAME}' (ID: {server.id}) ---")
            try:
                # To be safe, re-fetch the server object before deleting
                server_to_delete = client.servers.get_by_id(server.id)
                if server_to_delete:
                    delete_action = client.servers.delete(server_to_delete)
                    # Call our robust waiting function
                    wait_for_action(delete_action, timeout=60)
                else:
                    print("Server appears to have been deleted already.")
            except Exception as e:
                print(f"ERROR during server deletion: {e}")
                print("You may need to delete the server manually via the Hetzner Cloud console.")

if __name__ == "__main__":
    main()