# This file will contain the Python script to provision a Hetzner VPS,
# run the data ingestion job, and then spin down the VPS.

import hcloud
from hcloud.servers.domain import ServerCreatePublicNetwork
from hcloud.servers.client import BoundServer
from hcloud.actions.domain import ActionFailedException
import paramiko
import time
import os
import sys
import argparse
from dotenv import load_dotenv
import requests
from datetime import date
from typing import Optional, List, Dict, Any

# ==============================================================================
# --- GLOBAL CONSTANTS ---
# ==============================================================================

# Load environment variables from .env file in the current directory
load_dotenv()

# Static configuration for the worker server
SERVER_NAME = "cijene-ingestion-worker"
SERVER_TYPE = "cpx31"
IMAGE_NAME = "docker-ce"
LOCATION = "fsn1"
PROJECT_DIR_ON_VPS = "/opt/cijene-api"
SSH_KEY_NAME = "pricemice-worker-key" # The name of your SSH key in Hetzner Console

# ==============================================================================
# --- HELPER & API FUNCTIONS ---
# ==============================================================================

def get_ssh_key_id(client: hcloud.Client, key_name: str) -> hcloud.ssh_keys.client.BoundSSHKey:
    """Retrieves the SSHKey object from Hetzner Cloud by its name."""
    ssh_keys = client.ssh_keys.get_all(name=key_name)
    if not ssh_keys:
        raise Exception(f"SSH key '{key_name}' not found in Hetzner Cloud. Please upload it.")
    return ssh_keys[0]

def run_remote_command(ssh_client: paramiko.SSHClient, command: str, description: str, sensitive: bool = False):
    """Executes a command on the remote VPS and prints its output."""
    print(f"--- Executing Remote Step: {description} ---")
    if not sensitive:
        print(f"COMMAND: {command}")
    else:
        print("COMMAND: [Content is sensitive and not logged]")

    transport = ssh_client.get_transport()
    channel = transport.open_session()
    channel.exec_command(command)
    
    # Stream stdout and stderr to avoid deadlocks
    while not channel.exit_status_ready():
        if channel.recv_ready():
            print(channel.recv(1024).decode('utf-8', 'ignore'), end="")
        if channel.recv_stderr_ready():
            print(channel.recv_stderr(1024).decode('utf-8', 'ignore'), end="", file=sys.stderr)
        time.sleep(0.1)

    exit_status = channel.recv_exit_status()
    if exit_status != 0:
        raise Exception(f"Remote step '{description}' failed with exit status {exit_status}")
    print(f"--- Remote Step '{description}' completed successfully ---\n")

def wait_for_action(action: hcloud.actions.client.BoundAction, timeout: int = 180):
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

def get_active_chains(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetches active chains from the API."""
    url = f"{config['API_BASE_URL']}/chains/"
    print(f"DEBUG: Fetching active chains from: {url}")
    headers = {"X-API-Key": config["API_KEY"]}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return [chain for chain in data.get("chains", []) if chain.get("active")]
    except requests.exceptions.RequestException as e:
        print(f"\nERROR: Could not connect to the API at {url}. Details: {e}")
        sys.exit(1)

def get_run_status(config: Dict[str, Any], run_type: str, chain_name: str, run_date: date) -> str:
    """Fetches the run status (crawl or import) for a given chain and date."""
    url = f"{config['API_BASE_URL']}/{run_type}/status/{chain_name}/{run_date.strftime('%Y-%m-%d')}"
    headers = {"X-API-Key": config["API_KEY"]}
    print(f"DEBUG: Checking {run_type} status at: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("status", "error")
    except requests.exceptions.HTTPError as e:
        return "not_found" if e.response.status_code == 404 else "error"
    except requests.exceptions.RequestException:
        return "error"

def report_crawl_status_via_api(config: Dict[str, Any], chain_name: str, crawl_date: date, status: str, error_message: Optional[str] = None):
    """Reports the crawl status to the API."""
    url = f"{config['API_BASE_URL']}/crawler/status"
    headers = {"X-API-Key": config["API_KEY"], "Content-Type": "application/json"}
    payload = {
        "chain_name": chain_name, "crawl_date": crawl_date.strftime("%Y-%m-%d"),
        "status": status, "error_message": error_message, "n_stores": 0,
        "n_products": 0, "n_prices": 0, "elapsed_time": 0.0,
    }
    print(f"DEBUG: Reporting crawl status '{status}' for {chain_name} to API.")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to report crawl status for {chain_name}: {e}")

# ==============================================================================
# --- REFACTORED WORKFLOW FUNCTIONS ---
# ==============================================================================

def validate_and_get_config() -> Dict[str, Any]:
    """Validates all required environment variables and returns them as a dict."""
    print("--- Step 1: Validating Environment Configuration ---")
    config = {
        "HCLOUD_TOKEN": os.getenv("HCLOUD_TOKEN"),
        "SSH_KEY_PATH": os.getenv("SSH_KEY_PATH"),
        "WORKER_PRIMARY_IP": os.getenv("WORKER_PRIMARY_IP"),
        "SERVER_IP": os.getenv("SERVER_IP"),
        "API_KEY": os.getenv("API_KEY"),
        "PRIVATE_NETWORK_NAME": os.getenv("PRIVATE_NETWORK_NAME"),
        "MAIN_SERVER_PRIVATE_IP": os.getenv("SERVER_PRIVATE_IP"),
    }

    missing_vars = [key for key, value in config.items() if not value]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}. Please check your .env file.")

    config["API_BASE_URL"] = f"http://{config['SERVER_IP']}:8000/v1"
    print("Configuration is valid.")
    return config

def check_for_pending_jobs(config: Dict[str, Any]) -> List[str]:
    """Checks the API for chains that need processing and pre-sets their status."""
    print("\n--- Step 2: Checking for Pending Jobs ---")
    active_chains = get_active_chains(config)
    if not active_chains:
        print("No active chains found. Exiting.")
        sys.exit(0)

    chains_to_process = []
    today = date.today()
    for chain in active_chains:
        chain_code = chain.get("code")
        if not chain_code: continue
        
        crawl_status = get_run_status(config, "crawler", chain_code, today)
        import_status = get_run_status(config, "importer", chain_code, today)

        if crawl_status != "success" or import_status != "success":
            print(f"Chain '{chain_code}' needs processing (Crawl: {crawl_status}, Import: {import_status}).")
            chains_to_process.append(chain_code)
        else:
            print(f"Chain '{chain_code}' is already complete for today. Skipping.")

    if not chains_to_process:
        print("\nAll active chains are up-to-date. No jobs to run. Exiting.")
        sys.exit(0)

    print(f"\nJobs found for: {', '.join(chains_to_process)}. Marking as 'failed' pre-emptively.")
    for chain_code in chains_to_process:
        report_crawl_status_via_api(config, chain_code, today, "failed", "Crawl initiated by worker.")
    
    return chains_to_process

def prepare_remote_env_content(config: Dict[str, Any]) -> str:
    """Reads the local .env file and modifies the DB_DSN to use the private IP."""
    print("\n--- Step 3: Preparing Remote Environment Configuration ---")
    try:
        with open(".env", "r") as f:
            content = f.read()
    except FileNotFoundError:
        print("Warning: .env file not found. Remote configuration may be incomplete.")
        return ""

    if "DB_DSN=" in content:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("DB_DSN="):
                lines[i] = line.replace("@db:", f"@{config['MAIN_SERVER_PRIVATE_IP']}:")
                print(f"Modified DB_DSN to use private IP: {config['MAIN_SERVER_PRIVATE_IP']}")
        return "\n".join(lines)
    return content

def provision_worker_server(client: hcloud.Client, config: Dict[str, Any]) -> BoundServer:
    """Gathers resources and creates a new Hetzner server in the private network."""
    print("\n--- Step 4: Provisioning Worker Server ---")
    
    # Gather resources
    ssh_key_obj = get_ssh_key_id(client, SSH_KEY_NAME)
    server_type_obj = client.server_types.get_by_name(SERVER_TYPE)
    image_obj = client.images.get_by_name(IMAGE_NAME)
    location_obj = client.locations.get_by_name(LOCATION)
    network_obj = client.networks.get_by_name(config["PRIVATE_NETWORK_NAME"])
    
    primary_ips_page = client.primary_ips.get_list(ip=config["WORKER_PRIMARY_IP"])
    if not primary_ips_page.primary_ips:
        raise Exception(f"Primary IP '{config['WORKER_PRIMARY_IP']}' not found in your Hetzner project.")
    primary_ip_obj = primary_ips_page.primary_ips[0]
    
    # --- CORRECTED LOGIC FOR CHECKING IP ASSIGNMENT ---
    if primary_ip_obj.assignee_id is not None: 
        raise Exception(f"Primary IP '{config['WORKER_PRIMARY_IP']}' is already assigned.")
    # --- END OF CORRECTION ---

    if not network_obj: raise Exception(f"Private Network '{config['PRIVATE_NETWORK_NAME']}' not found.")
    
    print("All necessary Hetzner resources located.")

    # Create server
    create_result = client.servers.create(
        name=SERVER_NAME, server_type=server_type_obj, image=image_obj,
        location=location_obj, ssh_keys=[ssh_key_obj],
        public_net=ServerCreatePublicNetwork(ipv4=primary_ip_obj),
        networks=[network_obj], # Attach to the private network
        start_after_create=True,
    )
    
    wait_for_action(create_result.action)
    server = create_result.server
    server.reload()
    
    private_ip = server.private_net[0].ip if server.private_net else "N/A"
    print(f"Server '{server.name}' provisioned successfully.")
    print(f"-> Public IP: {server.public_net.ipv4.ip}, Private IP: {private_ip}")
    
    return server

def connect_and_run_jobs(config: Dict[str, Any], chains_to_process: List[str], remote_env: str):
    """Connects to the server via SSH, sets it up, and runs the data ingestion jobs."""
    print("\n--- Step 5: Connecting and Running Jobs ---")
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    private_key = paramiko.Ed25519Key.from_private_key_file(config["SSH_KEY_PATH"])

    print(f"Attempting SSH connection to {config['WORKER_PRIMARY_IP']}...")
    for i in range(15):
        try:
            ssh_client.connect(hostname=config['WORKER_PRIMARY_IP'], username="root", pkey=private_key, timeout=10)
            print("SSH connection established.")
            break
        except Exception as e:
            if i == 14: raise Exception("Could not establish SSH connection after multiple retries.") from e
            print(f"SSH connection failed ({i+1}/15): {e}. Retrying in 10 seconds...")
            time.sleep(10)

    try:
        run_remote_command(ssh_client, "export DEBIAN_FRONTEND=noninteractive && apt-get update -q && apt-get install -y -q git make", "Install Dependencies")
        run_remote_command(ssh_client, f"git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}", "Git Clone Project")
        run_remote_command(ssh_client, f"cat <<'EOF' > {PROJECT_DIR_ON_VPS}/.env\n{remote_env}\nEOF", "Write .env File", sensitive=True)
        
        job_commands = [
            "make build-worker",
            f"make crawl CHAIN={','.join(chains_to_process)}",
            f"make import-data DATE={date.today().strftime('%Y-%m-%d')}",
            "make enrich-data", "make geocode-stores",
        ]
        for command in job_commands:
            run_remote_command(ssh_client, f"cd {PROJECT_DIR_ON_VPS} && {command}", f"Job: {command}")
    finally:
        ssh_client.close()
        print("SSH connection closed.")

def teardown_worker_server(client: hcloud.Client, server: BoundServer):
    """Deletes the specified worker server."""
    print(f"\n--- Teardown: Deleting server '{server.name}' (ID: {server.id}) ---")
    try:
        delete_action = server.delete()
        wait_for_action(delete_action)
        print(f"Server '{server.name}' successfully deleted.")
    except hcloud.APIException as e:
        if e.code == "not_found": print(f"Server '{server.name}' was already deleted.")
        else: raise
    except Exception as e:
        print(f"ERROR during server deletion: {e}")
        raise

# ==============================================================================
# --- MAIN ORCHESTRATOR ---
# ==============================================================================

def main():
    """High-level orchestrator for the data ingestion worker."""
    parser = argparse.ArgumentParser(description="Hetzner VPS worker for data ingestion.")
    parser.add_argument("--no-teardown", action="store_true", help="Do not tear down the server after job completion.")
    args = parser.parse_args()

    server: Optional[BoundServer] = None
    client: Optional[hcloud.Client] = None
    try:
        # Step 1: Validate config and initialize client
        config = validate_and_get_config()
        client = hcloud.Client(token=config["HCLOUD_TOKEN"])

        # Step 2: Check if any work needs to be done
        chains_to_process = check_for_pending_jobs(config)

        # Step 3: Prepare remote configuration
        remote_env = prepare_remote_env_content(config)

        # Step 4: Provision the server
        server = provision_worker_server(client, config)

        # Step 5: Connect, setup, and run the actual jobs
        connect_and_run_jobs(config, chains_to_process, remote_env)

        print("\n*** WORKER JOB COMPLETED SUCCESSFULLY ***\n")

    except (ValueError, hcloud.APIException, ActionFailedException, Exception) as e:
        print(f"\n\n--- SCRIPT FAILED ---")
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if server and not args.no_teardown:
            if client: # Ensure client was initialized
                teardown_worker_server(client, server)
            else:
                print("Client not initialized, cannot perform teardown. Please check HCLOUD_TOKEN.")
        elif args.no_teardown:
            print("\n--- Teardown skipped due to --no-teardown flag. ---")

if __name__ == "__main__":
    main()