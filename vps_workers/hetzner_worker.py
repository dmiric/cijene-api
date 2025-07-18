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
        # --- Steps 1-4 remain unchanged ---
        # ... (validation, env prep, resource gathering, user_data script) ...

        # --- 5. Provision the server ---
        print(f"Creating server '{SERVER_NAME}' and assigning Primary IP '{WORKER_PRIMARY_IP}'...")
        server_create_result = client.servers.create(name=SERVER_NAME, server_type=server_type_obj, image=image_obj, location=location_obj, ssh_keys=[ssh_key_obj], user_data=user_data_script, public_net=public_net_config, start_after_create=True)
        server = server_create_result.server
        action = server_create_result.action

        # --- REPLACED THIS SECTION ---
        # Call our new robust waiting function with a 3-minute timeout
        wait_for_action(action, timeout=180)
        
        server = client.servers.get_by_id(server.id) # Refresh server object to get final state
        print(f"Server '{SERVER_NAME}' is running with IP: {server.public_net.ipv4.ip}")

        # --- Step 6 remains unchanged ---
        # ... (SSH connection and running the remote command) ...

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
                    # --- REPLACED THIS SECTION ---
                    # Call our new robust waiting function with a 1-minute timeout
                    wait_for_action(delete_action, timeout=60)
                else:
                    print("Server appears to have been deleted already.")
            except Exception as e:
                print(f"ERROR during server deletion: {e}")
                print("You may need to delete the server manually via the Hetzner Cloud console.")

if __name__ == "__main__":
    main()