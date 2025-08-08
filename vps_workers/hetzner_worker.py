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
import logging
import logging.config # Import logging.config
import structlog
import json # Import json for structlog's JSON renderer
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
SSH_KEY_NAME = "worker-key" # The name of your SSH key in Hetzner Console

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
    log.info("Executing Remote Step", description=description)
    if not sensitive:
        log.info("COMMAND", command=command)
    else:
        log.info("COMMAND", command="[Content is sensitive and not logged]")

    transport = ssh_client.get_transport()
    channel = transport.open_session()
    channel.exec_command(command)
    
    # Stream stdout and stderr to avoid deadlocks
    while not channel.exit_status_ready():
        if channel.recv_ready():
            log.info("remote_stdout", output=channel.recv(1024).decode('utf-8', 'ignore').strip())
        if channel.recv_stderr_ready():
            log.error("remote_stderr", output=channel.recv_stderr(1024).decode('utf-8', 'ignore').strip())
        time.sleep(0.1)

    exit_status = channel.recv_exit_status()
    if exit_status != 0:
        raise Exception(f"Remote step '{description}' failed with exit status {exit_status}")
    log.info("Remote Step completed successfully", description=description)

def wait_for_action(action: hcloud.actions.client.BoundAction, timeout: int = 180):
    """Waits for a Hetzner Cloud Action to complete by polling its status."""
    start_time = time.time()
    log.info("Waiting for action to complete", command=action.command, action_id=action.id)
    while action.status == "running":
        if time.time() - start_time > timeout:
            log.error("Action TIMEOUT", command=action.command, action_id=action.id)
            raise TimeoutError(f"Action '{action.command}' timed out after {timeout} seconds.")
        time.sleep(5)
        action.reload()

    if action.status == "success":
        log.info("Action SUCCESS", command=action.command, action_id=action.id)
    elif action.status == "error":
        log.error("Action FAILED", command=action.command, action_id=action.id)
        raise ActionFailedException(action=action)

def get_active_chains(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetches active chains from the API."""
    url = f"{config['API_BASE_URL']}/chains/"
    log.debug("Fetching active chains from API", url=url)
    headers = {"X-API-Key": config["API_KEY"]}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return [chain for chain in data.get("chains", []) if chain.get("active")]
    except requests.exceptions.RequestException as e:
        log.error("Could not connect to API to fetch active chains", url=url, error=str(e))
        sys.exit(1)

def get_run_status(config: Dict[str, Any], run_type: str, chain_name: str, run_date: date) -> str:
    """Fetches the run status (crawl or import) for a given chain and date."""
    url = f"{config['API_BASE_URL']}/{run_type}/status/{chain_name}/{run_date.strftime('%Y-%m-%d')}"
    headers = {"X-API-Key": config["API_KEY"]}
    log.debug("Checking run status", run_type=run_type, chain_name=chain_name, date=run_date.strftime('%Y-%m-%d'), url=url)
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
    log.debug("Reporting crawl status to API", chain_name=chain_name, status=status)
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error("Failed to report crawl status", chain_name=chain_name, error=str(e))

# ==============================================================================
# --- REFACTORED WORKFLOW FUNCTIONS ---
# ==============================================================================

def validate_and_get_config() -> Dict[str, Any]:
    """Validates all required environment variables and returns them as a dict."""
    log.info("Step 1: Validating Environment Configuration")
    config = {
        "HCLOUD_TOKEN": os.getenv("HCLOUD_TOKEN"),
        "SSH_KEY_PATH": os.getenv("SSH_KEY_PATH"),
        "WORKER_PRIMARY_IP": os.getenv("WORKER_PRIMARY_IP"),
        "SERVER_IP": os.getenv("SERVER_IP"),
        "API_KEY": os.getenv("API_KEY"),
        "PRIVATE_NETWORK_NAME": os.getenv("PRIVATE_NETWORK_NAME"),
        "MAIN_SERVER_PRIVATE_IP": os.getenv("SERVER_PRIVATE_IP"),
    }

    # PROMETHEUS_PUSHGATEWAY_URL will be derived from SERVER_IP, so it's not a direct env var
    missing_vars = [key for key, value in config.items() if not value]
    if missing_vars:
        log.error("Missing required environment variables", missing_vars=missing_vars)
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}. Please check your .env file.")

    config["API_BASE_URL"] = f"http://{config['SERVER_IP']}:8000/v1"
    log.info("Configuration is valid.")
    return config

def check_for_pending_jobs(config: Dict[str, Any]) -> List[str]:
    """Checks the API for chains that need processing and pre-sets their status."""
    log.info("Step 2: Checking for Pending Jobs")
    active_chains = get_active_chains(config)
    if not active_chains:
        log.info("No active chains found. Exiting.")
        sys.exit(0)

    chains_to_process = []
    today = date.today()
    for chain in active_chains:
        chain_code = chain.get("code")
        if not chain_code: continue
        
        crawl_status = get_run_status(config, "crawler", chain_code, today)
        import_status = get_run_status(config, "importer", chain_code, today)

        if crawl_status != "success" or import_status != "success":
            log.info("Chain needs processing", chain_code=chain_code, crawl_status=crawl_status, import_status=import_status)
            chains_to_process.append(chain_code)
        else:
            log.info("Chain is already complete for today. Skipping.", chain_code=chain_code)

    if not chains_to_process:
        log.info("All active chains are up-to-date. No jobs to run. Exiting.")
        sys.exit(0)

    log.info("Jobs found. Marking as 'failed' pre-emptively.", chains_to_process=chains_to_process)
    for chain_code in chains_to_process:
        report_crawl_status_via_api(config, chain_code, today, "failed", "Crawl initiated by worker.")
    
    return chains_to_process

def prepare_remote_env_content(config: Dict[str, Any]) -> str:
    """Reads the local .env file and modifies the DB_DSN to use the private IP."""
    log.info("Step 3: Preparing Remote Environment Configuration")
    try:
        with open(".env", "r") as f:
            content = f.read()
    except FileNotFoundError:
        log.warning(".env file not found. Remote configuration may be incomplete.")
        return ""

    if "DB_DSN=" in content:
        lines = content.splitlines()
        
        # Resolve DB_DSN variables from the current environment
        postgres_user = os.getenv("POSTGRES_USER")
        postgres_password = os.getenv("POSTGRES_PASSWORD")
        postgres_db = os.getenv("POSTGRES_DB")
        main_server_private_ip = config['MAIN_SERVER_PRIVATE_IP']

        resolved_db_dsn = f"postgresql://{postgres_user}:{postgres_password}@{main_server_private_ip}:5432/{postgres_db}"

        found_db_dsn = False
        for i, line in enumerate(lines):
            if line.startswith("DB_DSN="):
                lines[i] = f"DB_DSN={resolved_db_dsn}"
                found_db_dsn = True
                break
        
        if not found_db_dsn:
            lines.append(f"DB_DSN={resolved_db_dsn}")
            log.info("Added DB_DSN to remote .env")

        # Dynamically set PROMETHEUS_PUSHGATEWAY_URL to the main server's private IP
        prometheus_pushgateway_url = f"http://{config['MAIN_SERVER_PRIVATE_IP']}:9091"
        
        # Check if PROMETHEUS_PUSHGATEWAY_URL already exists and replace it
        found_pushgateway_url = False
        for i, line in enumerate(lines):
            if line.startswith("PROMETHEUS_PUSHGATEWAY_URL="):
                lines[i] = f"PROMETHEUS_PUSHGATEWAY_URL={prometheus_pushgateway_url}"
                found_pushgateway_url = True
                log.info("Replaced PROMETHEUS_PUSHGATEWAY_URL in remote .env", prometheus_pushgateway_url=prometheus_pushgateway_url)
                break
        
        # If not found, append it
        if not found_pushgateway_url:
            lines.append(f"PROMETHEUS_PUSHGATEWAY_URL={prometheus_pushgateway_url}")
            log.info("Added PROMETHEUS_PUSHGATEWAY_URL to remote .env", prometheus_pushgateway_url=prometheus_pushgateway_url)

        return "\n".join(lines)
    return content

def provision_worker_server(client: hcloud.Client, config: Dict[str, Any]) -> BoundServer:
    """Gathers resources and creates a new Hetzner server in the private network."""
    log.info("Step 4: Provisioning Worker Server")
    
    # Gather resources
    ssh_key_obj = get_ssh_key_id(client, SSH_KEY_NAME)
    server_type_obj = client.server_types.get_by_name(SERVER_TYPE)
    image_obj = client.images.get_by_name(IMAGE_NAME)
    location_obj = client.locations.get_by_name(LOCATION)
    network_obj = client.networks.get_by_name(config["PRIVATE_NETWORK_NAME"])
    
    primary_ips_page = client.primary_ips.get_list(ip=config["WORKER_PRIMARY_IP"])
    if not primary_ips_page.primary_ips:
        log.error("Primary IP not found", ip=config['WORKER_PRIMARY_IP'])
        raise Exception(f"Primary IP '{config['WORKER_PRIMARY_IP']}' not found in your Hetzner project.")
    primary_ip_obj = primary_ips_page.primary_ips[0]
    
    if primary_ip_obj.assignee_id is not None: 
        log.error("Primary IP is already assigned", ip=config['WORKER_PRIMARY_IP'], assignee_id=primary_ip_obj.assignee_id)
        raise Exception(f"Primary IP '{config['WORKER_PRIMARY_IP']}' is already assigned.")

    if not network_obj: 
        log.error("Private Network not found", network_name=config['PRIVATE_NETWORK_NAME'])
        raise Exception(f"Private Network '{config['PRIVATE_NETWORK_NAME']}' not found.")
    
    log.info("All necessary Hetzner resources located.")

    # Create server
    create_result = client.servers.create(
        name=SERVER_NAME, server_type=server_type_obj, image=image_obj,
        location=location_obj, ssh_keys=[ssh_key_obj],
        public_net=ServerCreatePublicNetwork(ipv4=primary_ip_obj),
        networks=[network_obj], # Attach to the private network
        start_after_create=True,
    )
    
    wait_for_action(create_result.action, 300)
    server = create_result.server
    server.reload()
    
    private_ip = server.private_net[0].ip if server.private_net else "N/A"
    log.info("Server provisioned successfully", server_name=server.name, public_ip=server.public_net.ipv4.ip, private_ip=private_ip)
    
    return server

def connect_and_run_jobs(config: Dict[str, Any], chains_to_process: List[str], remote_env: str):
    """Connects to the server via SSH, sets it up, and runs the data ingestion jobs."""
    log.info("Step 5: Connecting and Running Jobs")
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    private_key = paramiko.Ed25519Key.from_private_key_file(config["SSH_KEY_PATH"])

    log.info("Attempting SSH connection", hostname=config['WORKER_PRIMARY_IP'])
    for i in range(15):
        try:
            ssh_client.connect(hostname=config['WORKER_PRIMARY_IP'], username="root", pkey=private_key, timeout=10)
            log.info("SSH connection established.")
            break
        except Exception as e:
            if i == 14: 
                log.error("Could not establish SSH connection after multiple retries.", error=str(e))
                raise Exception("Could not establish SSH connection after multiple retries.") from e
            log.warning("SSH connection failed. Retrying...", attempt=i+1, max_attempts=15, error=str(e))
            time.sleep(10)

    try:
        run_remote_command(ssh_client, "export DEBIAN_FRONTEND=noninteractive && apt-get update -q && apt-get install -y -q git", "Install Dependencies")
        run_remote_command(ssh_client, f"git clone https://github.com/dmiric/cijene-api.git {PROJECT_DIR_ON_VPS}", "Git Clone Project")
        run_remote_command(ssh_client, f"cat <<'EOF' > {PROJECT_DIR_ON_VPS}/.env\n{remote_env}\nEOF", "Write .env File", sensitive=True)
        
        job_commands = [
            {"name": "Cleanup Docker", "description": "Shut down and remove old worker containers", "command": "docker compose -f docker-compose.worker.yml down --remove-orphans"},
            {"name": "Build & Start Worker", "description": "Build and start the worker services", "command": "docker compose -f docker-compose.worker.yml up -d --build --force-recreate"},
            {"name": "Run Crawler", "description": "Execute the data crawling process", "command": f"docker compose -f docker-compose.worker.yml run --rm crawler python crawler/cli/crawl.py{(' --chain ' + ','.join(chains_to_process)) if chains_to_process else ''}"},
            {"name": "Import Data", "description": "Import crawled data into the database", "command": "docker compose -f docker-compose.worker.yml run --rm api python service/cli/import.py"},
            {"name": "Geocode Stores", "description": "Geocode store locations", "command": "docker compose -f docker-compose.worker.yml run --rm --env DEBUG=false api python -c \"import asyncio; from service.cli.geocode_stores import geocode_stores; asyncio.run(geocode_stores())\""},
        ]
        for job in job_commands:
            run_remote_command(ssh_client, f"cd {PROJECT_DIR_ON_VPS} && {job['command']}", job['description'])
    finally:
        ssh_client.close()
        log.info("SSH connection closed.")

def teardown_worker_server(client: hcloud.Client, server: BoundServer):
    """Deletes the specified worker server."""
    log.info("Teardown: Deleting server", server_name=server.name, server_id=server.id)
    try:
        delete_action = server.delete()
        wait_for_action(delete_action)
        log.info("Server successfully deleted", server_name=server.name)
    except hcloud.APIException as e:
        if e.code == "not_found": 
            log.warning("Server was already deleted.", server_name=server.name)
        else: 
            log.error("Hetzner API error during server deletion", error=str(e))
            raise
    except Exception as e:
        log.error("Error during server deletion", error=str(e))
        raise

# ==============================================================================
# --- MAIN ORCHESTRATOR ---
# ==============================================================================

def configure_logging():
    log_level = logging.INFO # Default to INFO for worker
    is_debug = False # Hardcode to False for worker

    # Configure structlog processors
    processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    # Configure structlog to use standard library logging
    structlog.configure(
        processors=processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Define logging configuration using dictConfig
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.processors.JSONRenderer(),
                "foreign_pre_chain": processors,
            },
            "console_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer(),
                "foreign_pre_chain": processors,
            },
        },
        "handlers": {
            "default": {
                "level": log_level,
                "class": "logging.StreamHandler",
                "formatter": "console_formatter" if is_debug else "json_formatter",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {  # root logger
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)

    # The log object initialized here is local to this function.
    # The global 'log' object will be initialized after this function is called.

# Call logging configuration at the module level
configure_logging()
log = structlog.get_logger() # Initialize global log object

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

        log.info("WORKER JOB COMPLETED SUCCESSFULLY")

    except (ValueError, hcloud.APIException, ActionFailedException, Exception) as e:
        log.error("SCRIPT FAILED", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        if server and not args.no_teardown:
            if client: # Ensure client was initialized
                teardown_worker_server(client, server)
            else:
                log.error("Client not initialized, cannot perform teardown. Please check HCLOUD_TOKEN.")
        elif args.no_teardown:
            log.info("Teardown skipped due to --no-teardown flag.")

if __name__ == "__main__":
    main()
