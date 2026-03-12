#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ProxMenux OCI Manager

Manages deployment and lifecycle of OCI container applications.
Supports both podman and docker runtimes.

Usage:
    - As library: import oci_manager; oci_manager.deploy_app(...)
    - As CLI: python oci_manager.py deploy --app-id secure-gateway --config '{...}'
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Logging
logger = logging.getLogger("proxmenux.oci")

# =================================================================
# Paths
# =================================================================
# Production paths - persistent data in /usr/local/share/proxmenux/oci
OCI_BASE_DIR = "/usr/local/share/proxmenux/oci"
CATALOG_FILE = os.path.join(OCI_BASE_DIR, "catalog.json")
INSTALLED_FILE = os.path.join(OCI_BASE_DIR, "installed.json")
INSTANCES_DIR = os.path.join(OCI_BASE_DIR, "instances")

# Source catalog from Scripts (bundled with ProxMenux)
SCRIPTS_CATALOG = "/usr/local/share/proxmenux/scripts/oci/catalog.json"

# For development/testing in v0 environment
DEV_SCRIPTS_CATALOG = os.path.join(os.path.dirname(__file__), "..", "..", "Scripts", "oci", "catalog.json")


def ensure_oci_directories():
    """
    Ensure OCI directories exist and catalog is available.
    Called on first use to initialize the OCI environment.
    """
    # Create base directories
    os.makedirs(OCI_BASE_DIR, exist_ok=True)
    os.makedirs(INSTANCES_DIR, exist_ok=True)
    
    # Copy catalog from Scripts if not present in OCI dir
    if not os.path.exists(CATALOG_FILE):
        # Try production path first
        if os.path.exists(SCRIPTS_CATALOG):
            shutil.copy2(SCRIPTS_CATALOG, CATALOG_FILE)
            logger.info(f"Copied catalog from {SCRIPTS_CATALOG}")
        # Try development path
        elif os.path.exists(DEV_SCRIPTS_CATALOG):
            shutil.copy2(DEV_SCRIPTS_CATALOG, CATALOG_FILE)
            logger.info(f"Copied catalog from {DEV_SCRIPTS_CATALOG}")
    
    # Create empty installed.json if not present
    if not os.path.exists(INSTALLED_FILE):
        with open(INSTALLED_FILE, 'w') as f:
            json.dump({"version": "1.0.0", "instances": {}}, f, indent=2)
        logger.info(f"Created empty installed.json")

# Container name prefix
CONTAINER_PREFIX = "proxmenux"


# =================================================================
# Runtime Installation
# =================================================================
def install_runtime(runtime: str = "podman") -> Dict[str, Any]:
    """
    Install container runtime (podman or docker).
    
    Args:
        runtime: Runtime to install ('podman' or 'docker')
    
    Returns:
        Dict with success status and message
    """
    result = {
        "success": False,
        "message": "",
        "runtime": runtime
    }
    
    logger.info(f"Installing container runtime: {runtime}")
    print(f"\n{'='*60}")
    print(f"  Installing {runtime.capitalize()}")
    print(f"{'='*60}\n")
    
    try:
        # Detect distribution
        distro = "debian"  # Default
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                content = f.read().lower()
                if "alpine" in content:
                    distro = "alpine"
                elif "arch" in content:
                    distro = "arch"
                elif "fedora" in content or "rhel" in content or "centos" in content:
                    distro = "rhel"
        
        # Install commands by distro
        install_commands = {
            "debian": {
                "podman": ["apt-get", "update", "&&", "apt-get", "install", "-y", "podman"],
                "docker": ["apt-get", "update", "&&", "apt-get", "install", "-y", "docker.io"]
            },
            "alpine": {
                "podman": ["apk", "add", "--no-cache", "podman"],
                "docker": ["apk", "add", "--no-cache", "docker"]
            },
            "arch": {
                "podman": ["pacman", "-Sy", "--noconfirm", "podman"],
                "docker": ["pacman", "-Sy", "--noconfirm", "docker"]
            },
            "rhel": {
                "podman": ["dnf", "install", "-y", "podman"],
                "docker": ["dnf", "install", "-y", "docker-ce"]
            }
        }
        
        # Get install command
        if distro == "debian":
            # Use shell for && syntax
            if runtime == "podman":
                cmd = "apt-get update && apt-get install -y podman"
            else:
                cmd = "apt-get update && apt-get install -y docker.io"
            
            print(f"[*] Running: {cmd}")
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=False,
                timeout=300
            )
        else:
            cmd = install_commands.get(distro, {}).get(runtime, [])
            if not cmd:
                result["message"] = f"Unsupported distro: {distro}"
                return result
            
            print(f"[*] Running: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                capture_output=False,
                timeout=300
            )
        
        if proc.returncode != 0:
            result["message"] = f"Failed to install {runtime}"
            return result
        
        # Configure podman registries if needed
        if runtime == "podman":
            registries_conf = "/etc/containers/registries.conf"
            if os.path.exists("/etc/containers") and not os.path.exists(registries_conf):
                try:
                    with open(registries_conf, 'w') as f:
                        f.write('unqualified-search-registries = ["docker.io", "quay.io", "ghcr.io"]\n')
                    print("[*] Configured container registries")
                except Exception as e:
                    logger.warning(f"Could not configure registries: {e}")
        
        # Verify installation
        verify_cmd = shutil.which(runtime)
        if verify_cmd:
            print(f"\n[OK] {runtime.capitalize()} installed successfully!")
            result["success"] = True
            result["message"] = f"{runtime.capitalize()} installed successfully"
            result["path"] = verify_cmd
        else:
            result["message"] = f"{runtime.capitalize()} installed but not found in PATH"
        
    except subprocess.TimeoutExpired:
        result["message"] = "Installation timed out"
    except Exception as e:
        logger.error(f"Failed to install runtime: {e}")
        result["message"] = str(e)
    
    return result


def ensure_runtime() -> Dict[str, Any]:
    """
    Ensure a container runtime is available, installing if necessary.
    
    Returns:
        Dict with runtime info (same as detect_runtime)
    """
    runtime_info = detect_runtime()
    
    if runtime_info["available"]:
        return runtime_info
    
    # No runtime available, install podman
    print("\n[!] No container runtime found. Installing Podman...")
    install_result = install_runtime("podman")
    
    if not install_result["success"]:
        return {
            "available": False,
            "runtime": None,
            "version": None,
            "path": None,
            "error": install_result["message"]
        }
    
    # Re-detect after installation
    return detect_runtime()


# =================================================================
# Runtime Detection
# =================================================================
def detect_runtime() -> Dict[str, Any]:
    """
    Detect available container runtime (podman or docker).
    Returns dict with runtime info.
    """
    result = {
        "available": False,
        "runtime": None,
        "version": None,
        "path": None,
        "error": None
    }
    
    # Try podman first (preferred for Proxmox)
    podman_path = shutil.which("podman")
    if podman_path:
        try:
            proc = subprocess.run(
                ["podman", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                version = proc.stdout.strip().replace("podman version ", "")
                result.update({
                    "available": True,
                    "runtime": "podman",
                    "version": version,
                    "path": podman_path
                })
                return result
        except Exception as e:
            logger.warning(f"Podman found but failed to get version: {e}")
    
    # Try docker as fallback
    docker_path = shutil.which("docker")
    if docker_path:
        try:
            proc = subprocess.run(
                ["docker", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                # Parse "Docker version 24.0.5, build abc123"
                version = proc.stdout.strip()
                if "version" in version.lower():
                    version = version.split("version")[1].split(",")[0].strip()
                result.update({
                    "available": True,
                    "runtime": "docker",
                    "version": version,
                    "path": docker_path
                })
                return result
        except Exception as e:
            logger.warning(f"Docker found but failed to get version: {e}")
    
    result["error"] = "No container runtime found. Install podman or docker."
    return result


def _get_runtime() -> Optional[str]:
    """Get the runtime command (podman or docker) or None if unavailable."""
    info = detect_runtime()
    return info["runtime"] if info["available"] else None


def _run_container_cmd(args: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    """Run a container command with the detected runtime."""
    runtime = _get_runtime()
    if not runtime:
        return -1, "", "No container runtime available"
    
    cmd = [runtime] + args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


# =================================================================
# Catalog Management
# =================================================================
def load_catalog() -> Dict[str, Any]:
    """Load the OCI app catalog."""
    # Ensure directories and files exist on first call
    ensure_oci_directories()
    
    # Try to load from standard location
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load catalog from {CATALOG_FILE}: {e}")
    
    # Try alternative locations
    alternative_paths = [
        SCRIPTS_CATALOG,
        DEV_SCRIPTS_CATALOG,
        "/usr/local/share/proxmenux/scripts/oci/catalog.json",
        os.path.join(os.path.dirname(__file__), "oci", "catalog.json"),
    ]
    
    for alt_path in alternative_paths:
        if os.path.exists(alt_path):
            try:
                with open(alt_path, 'r') as f:
                    catalog = json.load(f)
                    logger.info(f"Loaded catalog from alternative path: {alt_path}")
                    # Copy to standard location for next time
                    try:
                        os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
                        with open(CATALOG_FILE, 'w') as out:
                            json.dump(catalog, out, indent=2)
                    except Exception:
                        pass
                    return catalog
            except Exception as e:
                logger.error(f"Failed to load catalog from {alt_path}: {e}")
    
    logger.error(f"No catalog found. Checked: {CATALOG_FILE}, {alternative_paths}")
    return {"version": "1.0.0", "apps": {}}


def get_app_definition(app_id: str) -> Optional[Dict[str, Any]]:
    """Get the definition for a specific app."""
    catalog = load_catalog()
    return catalog.get("apps", {}).get(app_id)


def list_available_apps() -> List[Dict[str, Any]]:
    """List all available apps from the catalog."""
    catalog = load_catalog()
    apps = []
    for app_id, app_def in catalog.get("apps", {}).items():
        apps.append({
            "id": app_id,
            "name": app_def.get("name", app_id),
            "short_name": app_def.get("short_name", app_def.get("name", app_id)),
            "category": app_def.get("category", "uncategorized"),
            "subcategory": app_def.get("subcategory", ""),
            "icon": app_def.get("icon", "box"),
            "color": app_def.get("color", "#6366F1"),
            "summary": app_def.get("summary", ""),
            "installed": is_installed(app_id)
        })
    return apps


# =================================================================
# Installed Apps Management
# =================================================================
def _load_installed() -> Dict[str, Any]:
    """Load the installed apps registry."""
    # Ensure directories exist
    ensure_oci_directories()
    
    if not os.path.exists(INSTALLED_FILE):
        return {"version": "1.0.0", "instances": {}}
    
    try:
        with open(INSTALLED_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load installed registry: {e}")
        return {"version": "1.0.0", "instances": {}}


def _save_installed(data: Dict[str, Any]) -> bool:
    """Save the installed apps registry."""
    try:
        os.makedirs(os.path.dirname(INSTALLED_FILE), exist_ok=True)
        with open(INSTALLED_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save installed registry: {e}")
        return False


def is_installed(app_id: str) -> bool:
    """Check if an app is installed."""
    installed = _load_installed()
    return app_id in installed.get("instances", {})


def list_installed_apps() -> List[Dict[str, Any]]:
    """List all installed apps with their status."""
    installed = _load_installed()
    apps = []
    
    for app_id, instance in installed.get("instances", {}).items():
        # Get current container status
        status = get_app_status(app_id)
        
        apps.append({
            "id": app_id,
            "instance_name": instance.get("instance_name", app_id),
            "installed_at": instance.get("installed_at"),
            "installed_by": instance.get("installed_by", "unknown"),
            "container": instance.get("container", {}),
            "status": status
        })
    
    return apps


def get_installed_app(app_id: str) -> Optional[Dict[str, Any]]:
    """Get details of an installed app."""
    installed = _load_installed()
    instance = installed.get("instances", {}).get(app_id)
    
    if not instance:
        return None
    
    # Enrich with current status
    instance["status"] = get_app_status(app_id)
    
    return instance


# =================================================================
# Container Status
# =================================================================
def get_app_status(app_id: str) -> Dict[str, Any]:
    """Get the current status of an app's container."""
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    
    result = {
        "state": "not_installed",
        "health": "unknown",
        "uptime_seconds": 0,
        "last_check": datetime.now().isoformat()
    }
    
    if not is_installed(app_id):
        return result
    
    # Check container status
    rc, out, _ = _run_container_cmd([
        "inspect", container_name,
        "--format", "{{.State.Status}}|{{.State.Running}}|{{.State.StartedAt}}"
    ])
    
    if rc != 0:
        result["state"] = "error"
        result["health"] = "unhealthy"
        return result
    
    try:
        parts = out.split("|")
        status = parts[0] if len(parts) > 0 else "unknown"
        running = parts[1].lower() == "true" if len(parts) > 1 else False
        started_at = parts[2] if len(parts) > 2 else ""
        
        result["state"] = "running" if running else status
        result["health"] = "healthy" if running else "stopped"
        
        # Calculate uptime
        if running and started_at:
            try:
                # Parse ISO timestamp
                started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                result["uptime_seconds"] = int((datetime.now(started.tzinfo) - started).total_seconds())
            except:
                pass
    except Exception as e:
        logger.error(f"Failed to parse container status: {e}")
        result["state"] = "error"
    
    return result


# =================================================================
# Network Detection
# =================================================================
def detect_networks() -> List[Dict[str, Any]]:
    """Detect available networks for VPN routing."""
    networks = []
    
    # Excluded interface prefixes
    excluded_prefixes = ('lo', 'docker', 'br-', 'veth', 'tailscale', 'wg', 'tun', 'tap')
    
    try:
        # Use ip command to get interfaces and addresses
        proc = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=5
        )
        
        if proc.returncode != 0:
            return networks
        
        interfaces = json.loads(proc.stdout)
        
        for iface in interfaces:
            name = iface.get("ifname", "")
            
            # Skip excluded interfaces
            if any(name.startswith(p) for p in excluded_prefixes):
                continue
            
            # Get IPv4 addresses
            for addr_info in iface.get("addr_info", []):
                if addr_info.get("family") != "inet":
                    continue
                
                local = addr_info.get("local", "")
                prefixlen = addr_info.get("prefixlen", 24)
                
                if not local:
                    continue
                
                # Calculate network address
                import ipaddress
                try:
                    network = ipaddress.IPv4Network(f"{local}/{prefixlen}", strict=False)
                    
                    # Determine interface type
                    iface_type = "physical"
                    if name.startswith("vmbr"):
                        iface_type = "bridge"
                    elif name.startswith("bond"):
                        iface_type = "bond"
                    elif "." in name:
                        iface_type = "vlan"
                    
                    networks.append({
                        "interface": name,
                        "type": iface_type,
                        "address": local,
                        "subnet": str(network),
                        "prefixlen": prefixlen,
                        "recommended": iface_type in ("bridge", "physical")
                    })
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Failed to detect networks: {e}")
    
    return networks


# =================================================================
# Deployment
# =================================================================
def deploy_app(app_id: str, config: Dict[str, Any], installed_by: str = "web") -> Dict[str, Any]:
    """
    Deploy an OCI app with the given configuration.
    
    Args:
        app_id: ID of the app from the catalog
        config: User configuration values
        installed_by: Source of installation ('web' or 'cli')
    
    Returns:
        Dict with success status and details
    """
    result = {
        "success": False,
        "message": "",
        "app_id": app_id
    }
    
    # Ensure runtime is available (install if necessary)
    runtime_info = ensure_runtime()
    if not runtime_info["available"]:
        error_msg = runtime_info.get("error", "Unknown error")
        result["message"] = f"Container runtime not available. {error_msg}. Please install Podman or Docker manually: apt install podman"
        return result
    
    runtime = runtime_info["runtime"]
    logger.info(f"Using runtime: {runtime}")
    
    # Get app definition
    app_def = get_app_definition(app_id)
    if not app_def:
        # Log detailed info for debugging
        logger.error(f"App '{app_id}' not found. Catalog file: {CATALOG_FILE}, exists: {os.path.exists(CATALOG_FILE)}")
        catalog = load_catalog()
        logger.error(f"Available apps: {list(catalog.get('apps', {}).keys())}")
        result["message"] = f"App '{app_id}' not found in catalog. Make sure the catalog file exists at {CATALOG_FILE}"
        return result
    
    # Check if already installed
    if is_installed(app_id):
        result["message"] = f"App '{app_id}' is already installed"
        return result
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    container_def = app_def.get("container", {})
    image = container_def.get("image")
    
    if not image:
        result["message"] = "App definition missing container image"
        return result
    
    # Create instance directory
    instance_dir = os.path.join(INSTANCES_DIR, app_id)
    state_dir = os.path.join(instance_dir, "state")
    
    try:
        os.makedirs(instance_dir, exist_ok=True)
        os.makedirs(state_dir, exist_ok=True)
    except Exception as e:
        result["message"] = f"Failed to create instance directory: {e}"
        return result
    
    # Save user config
    config_file = os.path.join(instance_dir, "config.json")
    try:
        with open(config_file, 'w') as f:
            json.dump({
                "app_id": app_id,
                "created_at": datetime.now().isoformat(),
                "values": config
            }, f, indent=2)
    except Exception as e:
        result["message"] = f"Failed to save config: {e}"
        return result
    
    # Build container run command
    cmd = ["run", "-d", "--name", container_name]
    
    # Network mode
    network_mode = container_def.get("network_mode")
    if network_mode:
        cmd.extend(["--network", network_mode])
    
    # Restart policy
    restart_policy = container_def.get("restart_policy", "unless-stopped")
    cmd.extend(["--restart", restart_policy])
    
    # Capabilities
    for cap in container_def.get("capabilities", []):
        cmd.extend(["--cap-add", cap])
    
    # Devices
    for device in container_def.get("devices", []):
        cmd.extend(["--device", device])
    
    # Volumes
    for vol_name, vol_def in app_def.get("volumes", {}).items():
        container_path = vol_def.get("container_path", "")
        if container_path:
            host_path = os.path.join(state_dir, vol_name)
            os.makedirs(host_path, exist_ok=True)
            cmd.extend(["-v", f"{host_path}:{container_path}"])
    
    # Static environment variables
    for key, value in app_def.get("environment", {}).items():
        cmd.extend(["-e", f"{key}={value}"])
    
    # Dynamic environment variables from config
    config_schema = app_def.get("config_schema", {})
    for field_name, field_def in config_schema.items():
        env_var = field_def.get("env_var")
        if not env_var:
            continue
        
        value = config.get(field_name)
        if value is None:
            value = field_def.get("default", "")
        
        # Handle special formats
        env_format = field_def.get("env_format")
        if env_format == "csv" and isinstance(value, list):
            value = ",".join(str(v) for v in value)
        
        if value:
            cmd.extend(["-e", f"{env_var}={value}"])
    
    # Build extra args from flags
    extra_args = []
    for field_name, field_def in config_schema.items():
        flag = field_def.get("flag")
        if not flag:
            continue
        
        value = config.get(field_name)
        if value is True:
            extra_args.append(flag)
    
    # For Tailscale, set TS_EXTRA_ARGS
    if extra_args and "tailscale" in image.lower():
        # Also add routes if specified
        routes = config.get("advertise_routes", [])
        if routes:
            extra_args.append(f"--advertise-routes={','.join(routes)}")
        
        cmd.extend(["-e", f"TS_EXTRA_ARGS={' '.join(extra_args)}"])
    
    # Add image
    cmd.append(image)
    
    # Pull image first if needed
    pull_policy = container_def.get("pull_policy", "if_not_present")
    if pull_policy != "never":
        logger.info(f"Pulling image: {image}")
        print(f"[*] Pulling image: {image}")
        pull_rc, pull_out, pull_err = _run_container_cmd(["pull", image], timeout=300)
        logger.info(f"Pull result: rc={pull_rc}, out={pull_out[:100] if pull_out else ''}, err={pull_err[:200] if pull_err else ''}")
        if pull_rc != 0 and pull_policy == "always":
            result["message"] = f"Failed to pull image: {pull_err}"
            return result
    
    # Run container
    logger.info(f"Starting container with cmd: {cmd}")
    print(f"[*] Starting container: {container_name}")
    rc, out, err = _run_container_cmd(cmd, timeout=60)
    logger.info(f"Run result: rc={rc}, out={out[:100] if out else ''}, err={err[:200] if err else ''}")
    
    if rc != 0:
        result["message"] = f"Failed to start container: {err}"
        # Cleanup on failure
        _run_container_cmd(["rm", "-f", container_name])
        return result
    
    container_id = out[:12] if out else ""
    
    # Get image ID
    img_rc, img_out, _ = _run_container_cmd(["inspect", image, "--format", "{{.Id}}"])
    image_id = img_out[:12] if img_rc == 0 and img_out else ""
    
    # Save to installed registry
    installed = _load_installed()
    installed["instances"][app_id] = {
        "app_id": app_id,
        "instance_name": app_id,
        "installed_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "installed_by": installed_by,
        "installed_version": app_def.get("version", "1.0.0"),
        "container": {
            "runtime": runtime,
            "container_id": container_id,
            "container_name": container_name,
            "image_id": image_id,
            "image_tag": image
        },
        "paths": {
            "config": config_file,
            "runtime": os.path.join(instance_dir, "runtime.json"),
            "state": state_dir
        }
    }
    
    if not _save_installed(installed):
        result["message"] = "Container started but failed to save registry"
        return result
    
    result["success"] = True
    result["message"] = f"App '{app_id}' deployed successfully"
    result["container_id"] = container_id
    
    return result


# =================================================================
# Lifecycle Actions
# =================================================================
def start_app(app_id: str) -> Dict[str, Any]:
    """Start an installed app's container."""
    if not is_installed(app_id):
        return {"success": False, "message": f"App '{app_id}' is not installed"}
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    rc, _, err = _run_container_cmd(["start", container_name])
    
    if rc != 0:
        return {"success": False, "message": f"Failed to start: {err}"}
    
    return {"success": True, "message": f"App '{app_id}' started"}


def stop_app(app_id: str) -> Dict[str, Any]:
    """Stop an installed app's container."""
    if not is_installed(app_id):
        return {"success": False, "message": f"App '{app_id}' is not installed"}
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    rc, _, err = _run_container_cmd(["stop", container_name], timeout=30)
    
    if rc != 0:
        return {"success": False, "message": f"Failed to stop: {err}"}
    
    return {"success": True, "message": f"App '{app_id}' stopped"}


def restart_app(app_id: str) -> Dict[str, Any]:
    """Restart an installed app's container."""
    if not is_installed(app_id):
        return {"success": False, "message": f"App '{app_id}' is not installed"}
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    rc, _, err = _run_container_cmd(["restart", container_name], timeout=60)
    
    if rc != 0:
        return {"success": False, "message": f"Failed to restart: {err}"}
    
    return {"success": True, "message": f"App '{app_id}' restarted"}


def remove_app(app_id: str, remove_data: bool = False) -> Dict[str, Any]:
    """Remove an installed app."""
    if not is_installed(app_id):
        return {"success": False, "message": f"App '{app_id}' is not installed"}
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    
    # Stop and remove container
    _run_container_cmd(["stop", container_name], timeout=30)
    rc, _, err = _run_container_cmd(["rm", "-f", container_name])
    
    if rc != 0:
        return {"success": False, "message": f"Failed to remove container: {err}"}
    
    # Remove from registry
    installed = _load_installed()
    if app_id in installed.get("instances", {}):
        del installed["instances"][app_id]
        _save_installed(installed)
    
    # Optionally remove data
    if remove_data:
        instance_dir = os.path.join(INSTANCES_DIR, app_id)
        if os.path.exists(instance_dir):
            shutil.rmtree(instance_dir, ignore_errors=True)
    
    return {"success": True, "message": f"App '{app_id}' removed"}


# =================================================================
# Logs
# =================================================================
def get_app_logs(app_id: str, lines: int = 100) -> Dict[str, Any]:
    """Get recent logs from an app's container."""
    if not is_installed(app_id):
        return {"success": False, "logs": "", "message": "App not installed"}
    
    container_name = f"{CONTAINER_PREFIX}-{app_id}"
    rc, out, err = _run_container_cmd(["logs", "--tail", str(lines), container_name], timeout=10)
    
    if rc != 0:
        return {"success": False, "logs": "", "message": f"Failed to get logs: {err}"}
    
    # Combine stdout and stderr (logs go to both)
    logs = out if out else err
    
    return {"success": True, "logs": logs}


# =================================================================
# Configuration Update
# =================================================================
def update_app_config(app_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Update an app's configuration and recreate the container."""
    if not is_installed(app_id):
        return {"success": False, "message": f"App '{app_id}' is not installed"}
    
    # Get current installation info
    installed = _load_installed()
    instance = installed.get("instances", {}).get(app_id, {})
    installed_by = instance.get("installed_by", "web")
    
    # Remove the app (but keep data)
    remove_result = remove_app(app_id, remove_data=False)
    if not remove_result["success"]:
        return remove_result
    
    # Redeploy with new config
    return deploy_app(app_id, config, installed_by=installed_by)


# =================================================================
# CLI Interface
# =================================================================
def main():
    """CLI entry point for use from bash scripts."""
    import argparse
    
    parser = argparse.ArgumentParser(description="ProxMenux OCI Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # deploy
    deploy_parser = subparsers.add_parser("deploy", help="Deploy an app")
    deploy_parser.add_argument("--app-id", required=True, help="App ID from catalog")
    deploy_parser.add_argument("--config", required=True, help="JSON config string")
    deploy_parser.add_argument("--source", default="cli", help="Installation source")
    
    # start
    start_parser = subparsers.add_parser("start", help="Start an app")
    start_parser.add_argument("--app-id", required=True)
    
    # stop
    stop_parser = subparsers.add_parser("stop", help="Stop an app")
    stop_parser.add_argument("--app-id", required=True)
    
    # restart
    restart_parser = subparsers.add_parser("restart", help="Restart an app")
    restart_parser.add_argument("--app-id", required=True)
    
    # remove
    remove_parser = subparsers.add_parser("remove", help="Remove an app")
    remove_parser.add_argument("--app-id", required=True)
    remove_parser.add_argument("--remove-data", action="store_true")
    
    # status
    status_parser = subparsers.add_parser("status", help="Get app status")
    status_parser.add_argument("--app-id", required=True)
    
    # list
    subparsers.add_parser("list", help="List installed apps")
    
    # catalog
    subparsers.add_parser("catalog", help="List available apps")
    
    # networks
    subparsers.add_parser("networks", help="Detect available networks")
    
    # runtime
    subparsers.add_parser("runtime", help="Detect container runtime")
    
    args = parser.parse_args()
    
    if args.command == "deploy":
        config = json.loads(args.config)
        result = deploy_app(args.app_id, config, installed_by=args.source)
    elif args.command == "start":
        result = start_app(args.app_id)
    elif args.command == "stop":
        result = stop_app(args.app_id)
    elif args.command == "restart":
        result = restart_app(args.app_id)
    elif args.command == "remove":
        result = remove_app(args.app_id, remove_data=args.remove_data)
    elif args.command == "status":
        result = get_app_status(args.app_id)
    elif args.command == "list":
        result = list_installed_apps()
    elif args.command == "catalog":
        result = list_available_apps()
    elif args.command == "networks":
        result = detect_networks()
    elif args.command == "runtime":
        result = detect_runtime()
    else:
        parser.print_help()
        return
    
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
