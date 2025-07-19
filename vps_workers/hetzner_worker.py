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
import requests
from datetime import date

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
API_BASE_URL = f"http://{SERVER_IP}:8000/v1" # Assuming API is accessible on port 8000
JOB_COMMANDS = [
    "make build-worker",
    "make crawl",
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

def get_active_chains():
    """Fetches active chains from the API, sending the required API Key."""
    url = f"{API_BASE_URL}/chains/"
    print(f"DEBUG: Attempting to fetch active chains from: {url}")
    
    try:
        # --- FIX: Added API key header to this request ---
        api_key = os.getenv("API_KEY")
        if not api_key:
            print("ERROR: API_KEY is not set in the environment. Cannot authenticate.")
            sys.exit(1)
            
        headers = {"X-API-Key": api_key}
        
        # Added timeout of 15 seconds and the headers
        response = requests.get(url, headers=headers, timeout=15)
        
        # This will raise an exception for errors like 4xx or 5xx
        response.raise_for_status() 
        
        chains_data = response.json()
        return [chain for chain in chains_data.get("chains", []) if chain.get("active")]

    except requests.exceptions.HTTPError as e:
        # This block catches specific HTTP errors like 422, 401, 403, 404
        print(f"\nERROR: The API server responded with an error: {e}")
        print("DETAILS: This is not a connection issue. The server is reachable but rejected the request.")
        print("\nDEBUGGING TIPS:")
        print("1. Is the API_KEY in your .env file correct?")
        print(f"2. Does the key have permission to access the '{url}' endpoint?")
        print(f"3. Check the API server logs for more details about why this {e.response.status_code} error occurred.")
        sys.exit(1)

    except requests.exceptions.RequestException as e:
        # This block catches lower-level issues like connection timeouts or DNS failures
        print(f"\nERROR: Could not connect to the API at {url}. The script cannot proceed.")
        print(f"DETAILS: {e}")
        print("\nDEBUGGING TIPS:")
        print(f"1. Is the API server running and accessible at SERVER_IP='{SERVER_IP}'?")
        print("2. Is a firewall blocking the connection?")
        sys.exit(1)

def get_crawl_run_status(chain_name: str, crawl_date: date):
    """Fetches the crawl run status for a given chain and date from the API."""
    try:
        api_key = os.getenv("API_KEY") # Assuming API_KEY is available in .env
        headers = {"X-API-Key": api_key}
        
        date_str = crawl_date.strftime("%Y-%m-%d")
        url = f"{API_BASE_URL}/crawler/status/{chain_name}/{date_str}"
        
        # --- DEBUGGING LINES ADDED ---
        print(f"DEBUG: Checking crawl status at: {url}")

        # Added timeout of 15 seconds
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        status_data = response.json()
        return status_data.get("status")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching crawl run status for {chain_name} on {crawl_date}: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 404:
            return "not_found"
        return "error"

def get_import_run_status(chain_name: str, import_date: date):
    """Fetches the import run status for a given chain and date from the API."""
    try:
        api_key = os.getenv("API_KEY")
        headers = {"X-API-Key": api_key}
        
        date_str = import_date.strftime("%Y-%m-%d")
        url = f"{API_BASE_URL}/importer/status/{chain_name}/{date_str}"
        
        print(f"DEBUG: Checking import status at: {url}")

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        status_data = response.json()
        return status_data.get("status")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching import run status for {chain_name} on {import_date}: {e}")
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 404:
            return "not_found"
        return "error"


# --- Main Execution Logic ---

def main():
    """
    Checks if a job needs to be run, provisions a server, sets it up,
    runs the jobs, and then de-provisions the server.
    """
    server = None
    try:
        # --- 1. Validate environment variables ---
        print("Validating environment variables...")
        if not all([HCLOUD_TOKEN, SSH_KEY_PATH, WORKER_PRIMARY_IP, SERVER_IP]):
            raise Exception("One or more required environment variables are not set. Check SERVER_IP in particular.")
        print(f"Validation successful. API server target is: {SERVER_IP}")

        # --- 2. Check chain statuses to see if any work is needed ---
        print("\n--- Checking for chains that need crawling and importing ---")
        active_chains = get_active_chains()
        if not active_chains:
            print("No active chains found via API. Exiting.")
            sys.exit(0)
            
        chains_to_process = []
        today = date.today()

        for chain in active_chains:
            chain_code = chain.get("code")
            if chain_code:
                crawl_status = get_crawl_run_status(chain_code, today)
                import_status = get_import_run_status(chain_code, today)

                if crawl_status != "success" or import_status != "success":
                    print(f"Chain '{chain_code}': Crawl status '{crawl_status}', Import status '{import_status}'. Adding to process list.")
                    chains_to_process.append(chain_code)
                else:
                    print(f"Chain '{chain_code}' already has 'success' for both crawl and import today. Skipping.")
        
        if not chains_to_process:
            print("\nAll active chains already have successful crawl and import for today. No jobs to run. Exiting.")
            sys.exit(0)

        print(f"\nProceeding with worker provisioning for chains: {', '.join(chains_to_process)}")

        # --- 3. Prepare .env content ---
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

        # --- 4. Gather Hetzner Cloud resources ---
        print("\nGathering Hetzner Cloud resources...")
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

        # --- 5. Define server configuration and Provision ---
        public_net_config = ServerCreatePublicNetwork(ipv4=primary_ip_obj)

        print(f"\nCreating server '{SERVER_NAME}' and assigning Primary IP '{WORKER_PRIMARY_IP}'...")
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
        wait_for_action(action, timeout=120)

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
        install_deps_command = "export DEBIAN_FRONTEND=noninteractive && apt-get update -q && apt-get install -y -q git make"
        run_remote_command(ssh_client, install_deps_command, "Install Dependencies")

        git_clone_command = f"git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}"
        run_remote_command(ssh_client, git_clone_command, "Git Clone")
        
        write_env_command = f"cat <<'EOF' > {PROJECT_DIR_ON_VPS}/.env\n{local_env_content}\nEOF"
        run_remote_command(ssh_client, write_env_command, "Write .env file", sensitive=True)

        # --- 8. Run jobs for the chains identified earlier ---
        modified_job_commands = [
            "make build-worker",
            f"make crawl CHAIN={','.join(chains_to_process)}",
            f"make import-data", # Pass chains to import-data
        ]
        print(f"\nInitiating crawl and import for chains: {', '.join(chains_to_process)}")
        for command in modified_job_commands:
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
                    delete_action = server_to_delete.delete()
                    wait_for_action(delete_action, timeout=120)
                else:
                    print(f"Server (ID: {server.id}) no longer exists.")
            except hcloud.APIException as e:
                if e.code == "not_found":
                    print(f"Server (ID: {server.id}) not found; likely already deleted.")
                else:
                    print(f"ERROR during server deletion (API): {e}")
            except Exception as e:
                print(f"ERROR during server deletion: {e}")
                print("You may need to check the server status manually in the Hetzner Cloud console.")

# --- Script Entry Point ---
if __name__ == "__main__":
    main()
