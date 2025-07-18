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
HCLOUD_TOKEN = os.getenv("HCLOUD_TOKEN")
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH")
SERVER_NAME = "cijene-ingestion-worker"
SERVER_TYPE = "cpx31"
IMAGE_NAME = "docker-ce"
LOCATION = "fsn1"
WORKER_PRIMARY_IP = os.getenv("WORKER_PRIMARY_IP")
SERVER_IP = os.getenv("SERVER_IP")
PROJECT_DIR_ON_VPS = "/opt/cijene-api"
JOB_COMMANDS = [
    "make build-worker",
    "make crawl CHAIN=roto,trgovina-krk,lorenco,boso",
    "make import-data",
]

# --- Hetzner Cloud Client ---
client = hcloud.Client(token=HCLOUD_TOKEN)

# --- Helper Functions ---

def get_ssh_key_id(key_name):
    """Retrieves the SSHKey object from Hetzner Cloud by its name."""
    ssh_keys = client.ssh_keys.get_all(name=key_name)
    if not ssh_keys:
        raise Exception(f"SSH key '{key_name}' not found in Hetzner Cloud. Please upload it.")
    return ssh_keys[0]

def run_remote_command(ssh_client, command, description="command", sensitive=False):
    """
    Executes a command on the remote VPS and prints its output.
    If 'sensitive' is True, the command content is not logged.
    """
    print(f"--- Executing Remote Step: {description} ---")
    if not sensitive:
        print(f"COMMAND: {command}")
    else:
        print("COMMAND: [Content is sensitive and not logged]")

    stdin, stdout, stderr = ssh_client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    stdout_output = stdout.read().decode().strip()
    stderr_output = stderr.read().decode().strip()

    if stdout_output:
        print(f"STDOUT:\n{stdout_output}")
    if stderr_output:
        print(f"STDERR:\n{stderr_output}")

    if exit_status != 0:
        raise Exception(f"Remote step '{description}' failed with exit status {exit_status}")
    print(f"--- Remote Step '{description}' completed successfully ---\n")

def wait_for_action(action, timeout: int = 180):
    """Waits for a Hetzner Cloud Action to complete by polling its status."""
    start_time = time.time()
    print(f"Waiting for action '{action.command}' (ID: {action.id}) to complete...", end="", flush=True)
    while action.status == "running":
        if time.time() - start_time > timeout:
            print(" TIMEOUT!")
            raise TimeoutError(f"Action '{action.command}' timed out after {timeout} seconds.")
        print(".", end="", flush=True)
        time.sleep(5)
        action.reload()

    if action.status == "success":
        print(" SUCCESS!")
    elif action.status == "error":
        print(" FAILED!")
        raise ActionFailedException(action=action)


# --- Main Execution Logic ---

def main():
    """
    Provisions a server, sets it up sequentially via SSH, runs a series of jobs,
    and then de-provisions the server.
    """
    server = None
    try:
        # --- 1. Validate environment variables ---
        print("Validating environment variables...")
        if not all([HCLOUD_TOKEN, SSH_KEY_PATH, WORKER_PRIMARY_IP, SERVER_IP]):
            raise Exception("One or more required environment variables are not set.")
        print("Validation successful.")

        # --- 2. Prepare .env content ---
        print("Reading local .env file to prepare remote configuration...")
        local_env_content = ""
        try:
            with open(".env", "r") as f:
                local_env_content = f.read()
        except FileNotFoundError:
            print("Warning: .env file not found.")

        if "DB_DSN=" in local_env_content:
            lines = local_env_content.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("DB_DSN="):
                    lines[i] = line.replace("@db:", f"@{SERVER_IP}:")
            local_env_content = "\n".join(lines)

        # --- 3. Gather Hetzner Cloud resources ---
        print("Gathering Hetzner Cloud resources...")
        ssh_key_obj = get_ssh_key_id("pricemice-worker-key")
        server_type_obj = client.server_types.get_by_name(SERVER_TYPE)
        image_obj = client.images.get_by_name(IMAGE_NAME)
        location_obj = client.locations.get_by_name(LOCATION)
        primary_ips_page = client.primary_ips.get_list(ip=WORKER_PRIMARY_IP)
        if not primary_ips_page.primary_ips:
            raise Exception(f"Primary IP '{WORKER_PRIMARY_IP}' not found.")
        primary_ip_obj = primary_ips_page.primary_ips[0]
        if primary_ip_obj.assignee_id is not None:
             raise Exception(f"Primary IP '{WORKER_PRIMARY_IP}' is already assigned.")
        print("All resources located successfully.")

        # --- 4. Define server configuration ---
        public_net_config = ServerCreatePublicNetwork(ipv4=primary_ip_obj)

        # --- 5. Provision the server (NO user_data needed) ---
        print(f"Creating server '{SERVER_NAME}' and assigning Primary IP '{WORKER_PRIMARY_IP}'...")
        server_create_result = client.servers.create(
            name=SERVER_NAME,
            server_type=server_type_obj,
            image=image_obj,
            location=location_obj,
            ssh_keys=[ssh_key_obj],
            public_net=public_net_config,
            start_after_create=True
        )
        server = server_create_result.server
        action = server_create_result.action
        wait_for_action(action, timeout=120) # 2 min timeout just for server boot

        server = client.servers.get_by_id(server.id)
        print(f"Server '{SERVER_NAME}' is running with IP: {server.public_net.ipv4.ip}")

        # --- 6. Connect via SSH ---
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        private_key = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)

        print(f"\nAttempting to connect to {WORKER_PRIMARY_IP} via SSH...")
        for i in range(15):
            try:
                ssh_client.connect(hostname=WORKER_PRIMARY_IP, username="root", pkey=private_key, timeout=10)
                print("SSH connection established successfully.\n")
                break
            except Exception as e:
                print(f"SSH connection failed ({i+1}/15): {e}. Retrying in 10 seconds...")
                time.sleep(10)
        else:
            raise Exception("Could not establish SSH connection after multiple retries.")
            
        # --- 7. Perform setup sequentially via SSH ---
        # NEW, ROBUST COMMAND
        install_deps_command = "export DEBIAN_FRONTEND=noninteractive && apt-get update -q && apt-get install -y -q git make"
        run_remote_command(ssh_client, install_deps_command, "Install Dependencies")

        git_clone_command = f"git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}"
        run_remote_command(ssh_client, git_clone_command, "Git Clone")
        
        write_env_command = f"cat <<'EOF' > {PROJECT_DIR_ON_VPS}/.env\n{local_env_content}\nEOF"
        run_remote_command(ssh_client, write_env_command, "Write .env file", sensitive=True)

        # --- 8. Run the sequence of job commands ---
        for command in JOB_COMMANDS:
            full_remote_command = f"cd {PROJECT_DIR_ON_VPS} && {command}"
            run_remote_command(ssh_client, full_remote_command, f"Job: {command}")

        ssh_client.close()
        print("SSH connection closed.")
        print("\n*** WORKER JOB COMPLETED SUCCESSFULLY ***\n")

    except (hcloud.APIException, ActionFailedException) as e:
        print(f"HETZNER CLOUD ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAN UNEXPECTED ERROR OCCURRED: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # --- 9. Clean up and de-provision the server ---
        if server:
            print(f"--- Teardown: Deleting server '{SERVER_NAME}' (ID: {server.id}) ---")
            try:
                server_to_delete = client.servers.get_by_id(server.id)
                if server_to_delete:
                    delete_action = client.servers.delete(server_to_delete)
                    wait_for_action(delete_action, timeout=120)
            except hcloud.APIException as e:
                if e.code == "not_found":
                    print(f"Server (ID: {server.id}) not found; likely already deleted.")
                else:
                    print(f"ERROR during server deletion (API): {e}")
            except Exception as e:
                print(f"ERROR during server deletion: {e}")
                print("You may need to check the server status manually.")

# --- Script Entry Point ---
if __name__ == "__main__":
    main()