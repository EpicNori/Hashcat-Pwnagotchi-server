import json
import logging
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Any

import requests

from app.config import HASHCAT_WPA_CACHE_DIR

logger = logging.getLogger(__name__)

CLUSTER_CONFIG_PATH = HASHCAT_WPA_CACHE_DIR / "cluster_config.json"


class ClusterWorkload(Enum):
    LOCAL_ONLY = "local_only"
    DISTRIBUTED = "distributed"
    FALLBACK = "fallback"


def _load_cluster_config() -> dict:
    if not CLUSTER_CONFIG_PATH.exists():
        return {"cluster_enabled": False, "cluster_nodes": []}
    try:
        with open(CLUSTER_CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load cluster config: {e}")
        return {"cluster_enabled": False, "cluster_nodes": []}


def _save_cluster_config(config: dict) -> None:
    try:
        CLUSTER_CONFIG_PATH.parent.mkdir(exist_ok=True, parents=True)
        with open(CLUSTER_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save cluster config: {e}")


def is_cluster_enabled() -> bool:
    config = _load_cluster_config()
    return config.get("cluster_enabled", False)


def get_cluster_nodes() -> list[dict]:
    config = _load_cluster_config()
    nodes = config.get("cluster_nodes", [])
    return [node for node in nodes if node.get("enabled", True)]


def add_cluster_node(hostname: str, tailscale_ip: str, api_port: int = 9111, enabled: bool = True) -> bool:
    config = _load_cluster_config()
    for node in config.get("cluster_nodes", []):
        if node.get("tailscale_ip") == tailscale_ip:
            logger.warning(f"Node with Tailscale IP {tailscale_ip} already exists")
            return False
    node = {
        "hostname": hostname,
        "tailscale_ip": tailscale_ip,
        "api_port": api_port,
        "enabled": enabled
    }
    config.setdefault("cluster_nodes", []).append(node)
    _save_cluster_config(config)
    logger.info(f"Added cluster node: {hostname} ({tailscale_ip})")
    return True


def remove_cluster_node(tailscale_ip: str) -> bool:
    config = _load_cluster_config()
    original_count = len(config.get("cluster_nodes", []))
    config["cluster_nodes"] = [n for n in config.get("cluster_nodes", []) if n.get("tailscale_ip") != tailscale_ip]
    if len(config["cluster_nodes"]) < original_count:
        _save_cluster_config(config)
        logger.info(f"Removed cluster node: {tailscale_ip}")
        return True
    return False


def set_cluster_enabled(enabled: bool) -> None:
    config = _load_cluster_config()
    config["cluster_enabled"] = enabled
    _save_cluster_config(config)
    logger.info(f"Cluster enabled: {enabled}")


def check_tailscale_active() -> bool:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return "BackendState" in data and data.get("BackendState") == "Running"
        return False
    except FileNotFoundError:
        logger.warning("Tailscale command not found")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Tailscale status command timed out")
        return False
    except Exception as e:
        logger.error(f"Failed to check Tailscale status: {e}")
        return False


def get_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except FileNotFoundError:
        logger.warning("Tailscale command not found")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Tailscale ip command timed out")
        return None
    except Exception as e:
        logger.error(f"Failed to get Tailscale IP: {e}")
        return None


def send_file_to_node(node_tailscale_ip: str, file_path: str | Path, api_port: int = 9111) -> bool:
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return False
    try:
        url = f"http://{node_tailscale_ip}:{api_port}/api/cluster/receive"
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {"filename": file_path.name}
            response = requests.post(url, files=files, data=data, timeout=300)
            if response.status_code == 200:
                logger.info(f"Successfully sent {file_path.name} to {node_tailscale_ip}")
                return True
            else:
                logger.error(f"Failed to send file: {response.status_code} - {response.text}")
                return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send file to node {node_tailscale_ip}: {e}")
        return False


def request_file_from_node(node_tailscale_ip: str, file_path: str | Path, api_port: int = 9111) -> bool:
    file_path = Path(file_path)
    try:
        url = f"http://{node_tailscale_ip}:{api_port}/api/cluster/send"
        params = {"filename": file_path.name}
        response = requests.get(url, params=params, timeout=300)
        if response.status_code == 200:
            file_path.parent.mkdir(exist_ok=True, parents=True)
            with open(file_path, "wb") as f:
                f.write(response.content)
            logger.info(f"Successfully received {file_path.name} from {node_tailscale_ip}")
            return True
        else:
            logger.error(f"Failed to request file: {response.status_code} - {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to request file from node {node_tailscale_ip}: {e}")
        return False


def check_node_health(node_dict: dict) -> dict:
    result = {
        "status": "unknown",
        "latency_ms": None,
        "error": None
    }
    try:
        url = f"http://{node_dict['tailscale_ip']}:{node_dict.get('api_port', 9111)}/api/health"
        start_time = time.time()
        response = requests.get(url, timeout=5)
        latency_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency_ms
        if response.status_code == 200:
            result["status"] = "healthy"
        else:
            result["status"] = "unhealthy"
            result["error"] = f"HTTP {response.status_code}"
    except requests.exceptions.Timeout:
        result["status"] = "unreachable"
        result["error"] = "Timeout"
    except requests.exceptions.RequestException as e:
        result["status"] = "unreachable"
        result["error"] = str(e)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


def get_all_node_health() -> dict[str, dict]:
    nodes = get_cluster_nodes()
    health_map = {}
    for node in nodes:
        health = check_node_health(node)
        health_map[node["tailscale_ip"]] = health
        logger.debug(f"Node {node['hostname']} ({node['tailscale_ip']}): {health['status']}")
    return health_map


def distribute_capture_file(file_path: str | Path, target_nodes: list[dict]) -> dict[str, bool]:
    file_path = Path(file_path)
    results = {}
    for node in target_nodes:
        success = send_file_to_node(
            node["tailscale_ip"],
            file_path,
            node.get("api_port", 9111)
        )
        results[node["tailscale_ip"]] = success
    return results


def collect_results_from_nodes(target_nodes: list[dict], output_dir: str | Path = None) -> dict[str, bool]:
    output_dir = Path(output_dir) if output_dir else HASHCAT_WPA_CACHE_DIR / "captures"
    output_dir.mkdir(exist_ok=True, parents=True)
    results = {}
    for node in target_nodes:
        try:
            url = f"http://{node['tailscale_ip']}:{node.get('api_port', 9111)}/api/cluster/results"
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                for filename, content in data.get("results", {}).items():
                    output_path = output_dir / filename
                    output_path.write_text(content)
                    results[node["tailscale_ip"]] = True
                    logger.info(f"Collected result {filename} from {node['tailscale_ip']}")
            else:
                results[node["tailscale_ip"]] = False
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to collect results from {node['tailscale_ip']}: {e}")
            results[node["tailscale_ip"]] = False
    return results