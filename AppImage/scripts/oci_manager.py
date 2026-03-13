#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ProxMenux OCI Manager

Manages deployment and lifecycle of OCI container applications using Proxmox VE 9.1+
native LXC from OCI images functionality.

Usage:
    - As library: import oci_manager; oci_manager.deploy_app(...)
    - As CLI: python oci_manager.py deploy --app-id secure-gateway --config '{...}'
"""

import base64
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Optional: cryptography for encryption
try:
    from cryptography.fernet import Fernet
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False

# Logging
logger = logging.getLogger("proxmenux.oci")

# =================================================================
# Paths
# =================================================================
OCI_BASE_DIR = "/usr/local/share/proxmenux/oci"
CATALOG_FILE = os.path.join(OCI_BASE_DIR, "catalog.json")
INSTALLED_FILE = os.path.join(OCI_BASE_DIR, "installed.json")
INSTANCES_DIR = os.path.join(OCI_BASE_DIR, "instances")

# Source catalog from Scripts (bundled with ProxMenux)
SCRIPTS_CATALOG = "/usr/local/share/proxmenux/scripts/oci/catalog.json"
DEV_SCRIPTS_CATALOG = os.path.join(os.path.dirname(__file__), "..", "..", "Scripts", "oci", "catalog.json")

# Encryption key file
ENCRYPTION_KEY_FILE = os.path.join(OCI_BASE_DIR, ".encryption_key")

# Default storage for templates
DEFAULT_STORAGE = "local"

# VMID range for OCI containers (9000-9999 to avoid conflicts)
OCI_VMID_START = 9000
OCI_VMID_END = 9999


# =================================================================
# Encryption Functions for Sensitive Data
# =================================================================
def _get_or_create_encryption_key() -> bytes:
    """Get or create the encryption key for sensitive data."""
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    
    if ENCRYPTION_AVAILABLE:
        key = Fernet.generate_key()
    else:
        key = secrets.token_bytes(32)
    
    os.makedirs(os.path.dirname(ENCRYPTION_KEY_FILE), exist_ok=True)
    with open(ENCRYPTION_KEY_FILE, 'wb') as f:
        f.write(key)
    os.chmod(ENCRYPTION_KEY_FILE, 0o600)
    
    return key


def encrypt_sensitive_value(value: str) -> str:
    """Encrypt a sensitive value. Returns base64-encoded string with 'ENC:' prefix."""
    if not value:
        return value
    
    key = _get_or_create_encryption_key()
    
    if ENCRYPTION_AVAILABLE:
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return "ENC:" + encrypted.decode()
    else:
        value_bytes = value.encode()
        encrypted = bytes(v ^ key[i % len(key)] for i, v in enumerate(value_bytes))
        return "ENC:" + base64.b64encode(encrypted).decode()


def decrypt_sensitive_value(encrypted: str) -> str:
    """Decrypt a sensitive value."""
    if not encrypted or not encrypted.startswith("ENC:"):
        return encrypted
    
    encrypted_data = encrypted[4:]
    key = _get_or_create_encryption_key()
    
    try:
        if ENCRYPTION_AVAILABLE:
            f = Fernet(key)
            decrypted = f.decrypt(encrypted_data.encode())
            return decrypted.decode()
        else:
            encrypted_bytes = base64.b64decode(encrypted_data)
            decrypted = bytes(v ^ key[i % len(key)] for i, v in enumerate(encrypted_bytes))
            return decrypted.decode()
    except Exception as e:
        logger.error(f"Failed to decrypt value: {e}")
        return encrypted


def encrypt_config_sensitive_fields(config: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt sensitive fields in config based on schema."""
    encrypted_config = config.copy()
    for field_name, field_schema in schema.items():
        if field_schema.get("sensitive") and field_name in encrypted_config:
            value = encrypted_config[field_name]
            if value and not str(value).startswith("ENC:"):
                encrypted_config[field_name] = encrypt_sensitive_value(str(value))
    return encrypted_config


def decrypt_config_sensitive_fields(config: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt sensitive fields in config based on schema."""
    decrypted_config = config.copy()
    for field_name, field_schema in schema.items():
        if field_schema.get("sensitive") and field_name in decrypted_config:
            value = decrypted_config[field_name]
            if value and str(value).startswith("ENC:"):
                decrypted_config[field_name] = decrypt_sensitive_value(str(value))
    return decrypted_config


# =================================================================
# Directory Management
# =================================================================
def ensure_oci_directories():
    """Ensure OCI directories exist and catalog is available."""
    os.makedirs(OCI_BASE_DIR, exist_ok=True)
    os.makedirs(INSTANCES_DIR, exist_ok=True)
    
    if not os.path.exists(CATALOG_FILE):
        if os.path.exists(SCRIPTS_CATALOG):
            shutil.copy2(SCRIPTS_CATALOG, CATALOG_FILE)
        elif os.path.exists(DEV_SCRIPTS_CATALOG):
            shutil.copy2(DEV_SCRIPTS_CATALOG, CATALOG_FILE)
    
    if not os.path.exists(INSTALLED_FILE):
        with open(INSTALLED_FILE, 'w') as f:
            json.dump({"version": "1.0.0", "instances": {}}, f, indent=2)


# =================================================================
# Proxmox VE Detection and Compatibility
# =================================================================
def check_proxmox_version() -> Dict[str, Any]:
    """Check Proxmox VE version and OCI support."""
    result = {
        "is_proxmox": False,
        "version": None,
        "oci_support": False,
        "error": None
    }
    
    try:
        # Check if pveversion exists
        if not shutil.which("pveversion"):
            result["error"] = "Not running on Proxmox VE"
            return result
        
        proc = subprocess.run(
            ["pveversion"],
            capture_output=True, text=True, timeout=5
        )
        
        if proc.returncode != 0:
            result["error"] = "Failed to get Proxmox version"
            return result
        
        # Parse version: "pve-manager/9.1-2/abc123..."
        version_str = proc.stdout.strip()
        result["is_proxmox"] = True
        
        match = re.search(r'pve-manager/(\d+)\.(\d+)', version_str)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            result["version"] = f"{major}.{minor}"
            # OCI support requires Proxmox VE 9.1+
            result["oci_support"] = (major > 9) or (major == 9 and minor >= 1)
        
        # Also check if skopeo is available (required for OCI)
        if result["oci_support"] and not shutil.which("skopeo"):
            result["oci_support"] = False
            result["error"] = "skopeo not found. Install with: apt install skopeo"
        
        if not result["oci_support"] and not result.get("error"):
            result["error"] = f"OCI support requires Proxmox VE 9.1+, found {result['version']}"
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


def _run_pve_cmd(cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    """Run a Proxmox VE command."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


# =================================================================
# VMID Management
# =================================================================
def _get_next_vmid() -> int:
    """Get next available VMID in OCI range (9000-9999)."""
    try:
        # Get list of all VMIDs in use
        rc, out, _ = _run_pve_cmd(["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"])
        
        if rc != 0:
            # Fallback: try pct list
            rc, out, _ = _run_pve_cmd(["pct", "list"])
            used_vmids = set()
            if rc == 0:
                for line in out.splitlines()[1:]:  # Skip header
                    parts = line.split()
                    if parts:
                        try:
                            used_vmids.add(int(parts[0]))
                        except ValueError:
                            pass
        else:
            resources = json.loads(out)
            used_vmids = {r.get("vmid") for r in resources if r.get("vmid")}
        
        # Find first available in OCI range
        for vmid in range(OCI_VMID_START, OCI_VMID_END + 1):
            if vmid not in used_vmids:
                return vmid
        
        raise RuntimeError("No available VMID in OCI range (9000-9999)")
        
    except Exception as e:
        logger.error(f"Failed to get next VMID: {e}")
        # Return a high number and hope for the best
        return OCI_VMID_START + int(time.time()) % 1000


def _get_vmid_for_app(app_id: str) -> Optional[int]:
    """Get the VMID for an installed app."""
    installed = _load_installed()
    instance = installed.get("instances", {}).get(app_id)
    return instance.get("vmid") if instance else None


# =================================================================
# OCI Image Management
# =================================================================
def pull_oci_image(image: str, tag: str = "latest", storage: str = DEFAULT_STORAGE) -> Dict[str, Any]:
    """
    Pull an OCI image from a registry and store as LXC template.
    Uses Proxmox's pvesh API to download OCI images (same as GUI).
    
    Args:
        image: Image name (e.g., "docker.io/tailscale/tailscale")
        tag: Image tag (e.g., "stable")
        storage: Proxmox storage to save template
    
    Returns:
        Dict with success status and template path
    """
    result = {
        "success": False,
        "message": "",
        "template": None
    }
    
    # Check Proxmox OCI support
    pve_info = check_proxmox_version()
    if not pve_info["oci_support"]:
        result["message"] = pve_info.get("error", "OCI not supported")
        return result
    
    # Normalize image name - ensure full registry path
    if not image.startswith(("docker.io/", "ghcr.io/", "quay.io/", "registry.")):
        image = f"docker.io/{image}"
    
    # For docker.io, library images need explicit library/ prefix
    parts = image.split("/")
    if parts[0] == "docker.io" and len(parts) == 2:
        image = f"docker.io/library/{parts[1]}"
    
    full_ref = f"{image}:{tag}"
    
    logger.info(f"Pulling OCI image: {full_ref}")
    print(f"[*] Pulling OCI image: {full_ref}")
    
    # Create a safe filename from the image reference
    # e.g., docker.io/tailscale/tailscale:stable -> tailscale-tailscale-stable.tar
    # Note: Use .tar extension (not .tar.zst) - skopeo creates uncompressed tar
    filename = image.replace("docker.io/", "").replace("ghcr.io/", "").replace("library/", "").replace("/", "-")
    filename = f"{filename}-{tag}.tar"
    
    # Get hostname for API
    hostname = os.uname().nodename
    
    # Use Proxmox's pvesh API to download the OCI image
    # This is exactly what the GUI does
    rc, out, err = _run_pve_cmd([
        "pvesh", "create", 
        f"/nodes/{hostname}/storage/{storage}/download-url",
        "--content", "vztmpl",
        "--filename", filename,
        "--url", f"docker://{full_ref}"
    ], timeout=600)
    
    if rc != 0:
        # Fallback: try direct skopeo if pvesh API fails
        logger.warning(f"pvesh download failed: {err}, trying skopeo fallback")
        
        if not shutil.which("skopeo"):
            result["message"] = f"Failed to pull image via API: {err}"
            return result
        
        # Get template directory
        template_dir = "/var/lib/vz/template/cache"
        rc2, out2, _ = _run_pve_cmd(["pvesm", "path", f"{storage}:vztmpl/test"])
        if rc2 == 0 and out2.strip():
            template_dir = os.path.dirname(out2.strip())
        
        template_path = os.path.join(template_dir, filename)
        
        # Use skopeo with oci-archive format (this is what works with Proxmox 9.1)
        try:
            proc = subprocess.run(
                ["skopeo", "copy", "--override-os", "linux", 
                 f"docker://{full_ref}", f"oci-archive:{template_path}"],
                capture_output=True,
                text=True,
                timeout=600
            )
            
            if proc.returncode != 0:
                result["message"] = f"Failed to pull image: {proc.stderr}"
                logger.error(f"skopeo copy failed: {proc.stderr}")
                return result
        except subprocess.TimeoutExpired:
            result["message"] = "Image pull timed out after 10 minutes"
            return result
        except Exception as e:
            result["message"] = f"Failed to pull image: {e}"
            logger.error(f"Pull failed: {e}")
            return result
    
    # Template was created via API or skopeo
    result["success"] = True
    result["template"] = f"{storage}:vztmpl/{filename}"
    result["message"] = "Image pulled successfully"
    print(f"[OK] Image pulled: {result['template']}")
    
    return result


# =================================================================
# Catalog Management
# =================================================================
def load_catalog() -> Dict[str, Any]:
    """Load the OCI app catalog."""
    ensure_oci_directories()
    
    for path in [CATALOG_FILE, SCRIPTS_CATALOG, DEV_SCRIPTS_CATALOG]:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load catalog from {path}: {e}")
    
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
    ensure_oci_directories()
    if not os.path.exists(INSTALLED_FILE):
        return {"version": "1.0.0", "instances": {}}
    try:
        with open(INSTALLED_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {"version": "1.0.0", "instances": {}}


def _save_installed(data: Dict[str, Any]) -> bool:
    """Save the installed apps registry."""
    try:
        with open(INSTALLED_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save installed: {e}")
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
        status = get_app_status(app_id)
        apps.append({
            "id": app_id,
            "instance_name": instance.get("instance_name", app_id),
            "vmid": instance.get("vmid"),
            "installed_at": instance.get("installed_at"),
            "installed_by": instance.get("installed_by", "unknown"),
            "status": status
        })
    return apps


def get_installed_app(app_id: str) -> Optional[Dict[str, Any]]:
    """Get details of an installed app."""
    installed = _load_installed()
    instance = installed.get("instances", {}).get(app_id)
    if not instance:
        return None
    instance["status"] = get_app_status(app_id)
    return instance


# =================================================================
# Container Status
# =================================================================
def get_app_status(app_id: str) -> Dict[str, Any]:
    """Get the current status of an app's LXC container."""
    result = {
        "state": "not_installed",
        "health": "unknown",
        "uptime_seconds": 0,
        "last_check": datetime.now().isoformat()
    }
    
    if not is_installed(app_id):
        return result
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["state"] = "error"
        result["health"] = "unknown"
        return result
    
    # Get LXC status using pct
    rc, out, _ = _run_pve_cmd(["pct", "status", str(vmid)])
    
    if rc != 0:
        result["state"] = "error"
        result["health"] = "unhealthy"
        return result
    
    # Parse status: "status: running" or "status: stopped"
    if "running" in out.lower():
        result["state"] = "running"
        result["health"] = "healthy"
        
        # Get uptime
        rc, out, _ = _run_pve_cmd(["pct", "exec", str(vmid), "--", "cat", "/proc/uptime"])
        if rc == 0:
            try:
                uptime = float(out.split()[0])
                result["uptime_seconds"] = int(uptime)
            except:
                pass
    elif "stopped" in out.lower():
        result["state"] = "stopped"
        result["health"] = "stopped"
    else:
        result["state"] = "unknown"
    
    return result


# =================================================================
# Deployment
# =================================================================
def deploy_app(app_id: str, config: Dict[str, Any], installed_by: str = "web") -> Dict[str, Any]:
    """
    Deploy an OCI app as a Proxmox LXC container.
    
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
        "app_id": app_id,
        "vmid": None
    }
    
    # Check Proxmox OCI support
    pve_info = check_proxmox_version()
    if not pve_info["oci_support"]:
        result["message"] = pve_info.get("error", "OCI containers require Proxmox VE 9.1+")
        return result
    
    # Get app definition
    app_def = get_app_definition(app_id)
    if not app_def:
        result["message"] = f"App '{app_id}' not found in catalog"
        return result
    
    # Check if already installed
    if is_installed(app_id):
        result["message"] = f"App '{app_id}' is already installed"
        return result
    
    container_def = app_def.get("container", {})
    image = container_def.get("image", "")
    
    if not image:
        result["message"] = "No container image specified in app definition"
        return result
    
    # Parse image and tag
    if ":" in image:
        image_name, tag = image.rsplit(":", 1)
    else:
        image_name, tag = image, "latest"
    
    # Get next available VMID
    vmid = _get_next_vmid()
    result["vmid"] = vmid
    
    hostname = config.get("hostname", f"proxmenux-{app_id}")
    
    logger.info(f"Deploying {app_id} as LXC {vmid}")
    print(f"[*] Deploying {app_id} as LXC container (VMID: {vmid})")
    
    # Step 1: Pull OCI image
    print(f"[*] Pulling OCI image: {image}")
    pull_result = pull_oci_image(image_name, tag)
    
    if not pull_result["success"]:
        result["message"] = pull_result["message"]
        return result
    
    template = pull_result["template"]
    
    # Step 2: Create LXC container
    print(f"[*] Creating LXC container...")
    
    # Build pct create command for OCI container
    # IMPORTANT: OCI containers in Proxmox 9.1 require:
    # - ostype MUST be "unmanaged" for OCI images (critical!)
    # - unprivileged is recommended for security
    pct_cmd = [
        "pct", "create", str(vmid), template,
        "--hostname", hostname,
        "--memory", str(container_def.get("memory", 512)),
        "--cores", str(container_def.get("cores", 1)),
        "--rootfs", f"local-lvm:{container_def.get('disk_size', 4)}",
        "--ostype", "unmanaged",
        "--unprivileged", "0" if container_def.get("privileged") else "1",
        "--onboot", "1"
    ]
    
    # Network configuration - use simple bridge with DHCP
    pct_cmd.extend(["--net0", "name=eth0,bridge=vmbr0,ip=dhcp"])
    
    # Run pct create
    rc, out, err = _run_pve_cmd(pct_cmd, timeout=120)
    
    if rc != 0:
        result["message"] = f"Failed to create container: {err}"
        logger.error(f"pct create failed: {err}")
        return result
    
    # Step 3: Apply extra LXC configuration (for unprivileged containers)
    lxc_config = container_def.get("lxc_config", [])
    if lxc_config:
        conf_file = f"/etc/pve/lxc/{vmid}.conf"
        try:
            with open(conf_file, 'a') as f:
                f.write("\n# ProxMenux OCI extra config\n")
                for config_line in lxc_config:
                    f.write(f"{config_line}\n")
            logger.info(f"Applied extra LXC config to {conf_file}")
        except Exception as e:
            logger.warning(f"Could not apply extra LXC config: {e}")
    
    # Step 4: Configure environment variables
    env_vars = []
    
    # Add static env vars from container definition
    for env in container_def.get("environment", []):
        env_name = env.get("name", "")
        env_value = env.get("value", "")
        
        # Substitute config values
        if env_value.startswith("$"):
            config_key = env_value[1:]
            env_value = config.get(config_key, env.get("default", ""))
        
        if env_name and env_value:
            env_vars.append(f"{env_name}={env_value}")
    
    # Set environment via pct set
    if env_vars:
        # Proxmox 9.1 supports environment variables for OCI containers
        for i, env in enumerate(env_vars):
            _run_pve_cmd(["pct", "set", str(vmid), f"--lxc.environment", env])
    
    # Step 5: Enable IP forwarding if needed (for VPN containers)
    if "tailscale" in image.lower() or container_def.get("requires_ip_forward"):
        _enable_host_ip_forwarding()
    
    # Step 6: Start the container
    print(f"[*] Starting container...")
    rc, _, err = _run_pve_cmd(["pct", "start", str(vmid)])
    
    if rc != 0:
        result["message"] = f"Container created but failed to start: {err}"
        logger.error(f"pct start failed: {err}")
        # Don't return - container exists, just not started
    
    # Step 6: Save instance data
    instance_dir = os.path.join(INSTANCES_DIR, app_id)
    os.makedirs(instance_dir, exist_ok=True)
    
    # Save config (encrypted)
    config_file = os.path.join(instance_dir, "config.json")
    config_schema = app_def.get("config_schema", {})
    encrypted_config = encrypt_config_sensitive_fields(config, config_schema)
    
    with open(config_file, 'w') as f:
        json.dump({
            "app_id": app_id,
            "vmid": vmid,
            "created_at": datetime.now().isoformat(),
            "values": encrypted_config
        }, f, indent=2)
    os.chmod(config_file, 0o600)
    
    # Update installed registry
    installed = _load_installed()
    installed["instances"][app_id] = {
        "app_id": app_id,
        "vmid": vmid,
        "instance_name": hostname,
        "installed_at": datetime.now().isoformat(),
        "installed_by": installed_by,
        "image": image,
        "template": template
    }
    _save_installed(installed)
    
    result["success"] = True
    result["message"] = f"App deployed successfully as LXC {vmid}"
    print(f"[OK] Container {vmid} ({hostname}) deployed successfully!")
    
    return result


def _enable_host_ip_forwarding() -> bool:
    """Enable IP forwarding on the Proxmox host."""
    logger.info("Enabling IP forwarding on host")
    print("[*] Enabling IP forwarding...")
    
    try:
        # Enable IPv4 forwarding
        with open("/proc/sys/net/ipv4/ip_forward", 'w') as f:
            f.write('1')
        
        # Enable IPv6 forwarding
        ipv6_path = "/proc/sys/net/ipv6/conf/all/forwarding"
        if os.path.exists(ipv6_path):
            with open(ipv6_path, 'w') as f:
                f.write('1')
        
        # Make persistent
        sysctl_d = "/etc/sysctl.d/99-proxmenux-ip-forward.conf"
        with open(sysctl_d, 'w') as f:
            f.write("net.ipv4.ip_forward = 1\n")
            f.write("net.ipv6.conf.all.forwarding = 1\n")
        
        subprocess.run(["sysctl", "-p", sysctl_d], capture_output=True)
        return True
        
    except Exception as e:
        logger.warning(f"Could not enable IP forwarding: {e}")
        return False


# =================================================================
# Container Control
# =================================================================
def start_app(app_id: str) -> Dict[str, Any]:
    """Start an app's LXC container."""
    result = {"success": False, "message": ""}
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["message"] = f"App '{app_id}' not found"
        return result
    
    rc, _, err = _run_pve_cmd(["pct", "start", str(vmid)])
    
    if rc == 0:
        result["success"] = True
        result["message"] = f"Container {vmid} started"
    else:
        result["message"] = f"Failed to start: {err}"
    
    return result


def stop_app(app_id: str) -> Dict[str, Any]:
    """Stop an app's LXC container."""
    result = {"success": False, "message": ""}
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["message"] = f"App '{app_id}' not found"
        return result
    
    rc, _, err = _run_pve_cmd(["pct", "stop", str(vmid)])
    
    if rc == 0:
        result["success"] = True
        result["message"] = f"Container {vmid} stopped"
    else:
        result["message"] = f"Failed to stop: {err}"
    
    return result


def restart_app(app_id: str) -> Dict[str, Any]:
    """Restart an app's LXC container."""
    result = {"success": False, "message": ""}
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["message"] = f"App '{app_id}' not found"
        return result
    
    rc, _, err = _run_pve_cmd(["pct", "reboot", str(vmid)])
    
    if rc == 0:
        result["success"] = True
        result["message"] = f"Container {vmid} restarted"
    else:
        result["message"] = f"Failed to restart: {err}"
    
    return result


def remove_app(app_id: str, remove_data: bool = True) -> Dict[str, Any]:
    """Remove an app's LXC container."""
    result = {"success": False, "message": ""}
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["message"] = f"App '{app_id}' not found"
        return result
    
    logger.info(f"Removing app {app_id} (VMID: {vmid})")
    
    # Stop if running
    _run_pve_cmd(["pct", "stop", str(vmid)])
    time.sleep(2)
    
    # Destroy container
    rc, _, err = _run_pve_cmd(["pct", "destroy", str(vmid), "--purge"])
    
    if rc != 0:
        result["message"] = f"Failed to destroy container: {err}"
        return result
    
    # Remove from installed registry
    installed = _load_installed()
    if app_id in installed.get("instances", {}):
        del installed["instances"][app_id]
        _save_installed(installed)
    
    # Remove instance data
    if remove_data:
        instance_dir = os.path.join(INSTANCES_DIR, app_id)
        if os.path.exists(instance_dir):
            shutil.rmtree(instance_dir)
    
    result["success"] = True
    result["message"] = f"App {app_id} removed successfully"
    
    return result


def get_app_logs(app_id: str, lines: int = 100) -> Dict[str, Any]:
    """Get logs from an app's LXC container."""
    result = {"success": False, "message": "", "logs": ""}
    
    vmid = _get_vmid_for_app(app_id)
    if not vmid:
        result["message"] = f"App '{app_id}' not found"
        return result
    
    # Try to get logs from inside container
    rc, out, err = _run_pve_cmd([
        "pct", "exec", str(vmid), "--",
        "tail", "-n", str(lines), "/var/log/messages"
    ])
    
    if rc != 0:
        # Try journalctl
        rc, out, err = _run_pve_cmd([
            "pct", "exec", str(vmid), "--",
            "journalctl", "-n", str(lines), "--no-pager"
        ])
    
    if rc == 0:
        result["success"] = True
        result["logs"] = out
    else:
        result["message"] = f"Failed to get logs: {err}"
        result["logs"] = err
    
    return result


# =================================================================
# Network Detection
# =================================================================
def detect_host_networks() -> List[Dict[str, Any]]:
    """Detect available networks on the Proxmox host."""
    networks = []
    
    try:
        # Get bridges from Proxmox
        rc, out, _ = _run_pve_cmd(["pvesh", "get", "/nodes/localhost/network", "--output-format", "json"])
        
        if rc == 0:
            ifaces = json.loads(out)
            for iface in ifaces:
                if iface.get("type") == "bridge":
                    networks.append({
                        "interface": iface.get("iface", ""),
                        "type": "bridge",
                        "address": iface.get("address", ""),
                        "cidr": iface.get("cidr", ""),
                        "recommended": True
                    })
        
    except Exception as e:
        logger.error(f"Failed to detect networks: {e}")
    
    return networks


# =================================================================
# Network Detection
# =================================================================
def detect_networks() -> List[Dict[str, str]]:
    """
    Detect available network interfaces and their subnets.
    Used for suggesting routes to advertise via Tailscale.
    
    Returns:
        List of dicts with interface name and subnet.
    """
    networks = []
    
    try:
        # Use ip command to get interfaces and their addresses
        proc = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True,
            text=True
        )
        
        if proc.returncode == 0:
            interfaces = json.loads(proc.stdout)
            
            for iface in interfaces:
                name = iface.get("ifname", "")
                
                # Skip loopback and virtual interfaces
                if name in ("lo", "docker0") or name.startswith(("veth", "br-", "tap", "fwbr", "fwpr")):
                    continue
                
                # Get IPv4 addresses
                for addr_info in iface.get("addr_info", []):
                    if addr_info.get("family") == "inet":
                        ip = addr_info.get("local", "")
                        prefix = addr_info.get("prefixlen", 24)
                        if ip and not ip.startswith("127."):
                            # Calculate network address
                            ip_parts = ip.split(".")
                            if len(ip_parts) == 4:
                                # Simple network calculation
                                if prefix >= 24:
                                    network = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0/{prefix}"
                                elif prefix >= 16:
                                    network = f"{ip_parts[0]}.{ip_parts[1]}.0.0/{prefix}"
                                else:
                                    network = f"{ip_parts[0]}.0.0.0/{prefix}"
                                networks.append({
                                    "interface": name,
                                    "subnet": network,
                                    "ip": ip
                                })
    except Exception as e:
        logger.error(f"Failed to detect networks: {e}")
    
    return networks


# =================================================================
# Runtime Detection (for backward compatibility)
# =================================================================
def detect_runtime() -> Dict[str, Any]:
    """Check if Proxmox OCI support is available."""
    pve_info = check_proxmox_version()
    
    return {
        "available": pve_info["oci_support"],
        "runtime": "proxmox-lxc" if pve_info["oci_support"] else None,
        "version": pve_info.get("version"),
        "path": shutil.which("pct"),
        "error": pve_info.get("error")
    }


# =================================================================
# CLI Interface
# =================================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ProxMenux OCI Manager")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy an app")
    deploy_parser.add_argument("--app-id", required=True, help="App ID")
    deploy_parser.add_argument("--config", default="{}", help="JSON config")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Get app status")
    status_parser.add_argument("--app-id", required=True, help="App ID")
    
    # Control commands
    start_parser = subparsers.add_parser("start", help="Start app")
    start_parser.add_argument("--app-id", required=True)
    
    stop_parser = subparsers.add_parser("stop", help="Stop app")
    stop_parser.add_argument("--app-id", required=True)
    
    remove_parser = subparsers.add_parser("remove", help="Remove app")
    remove_parser.add_argument("--app-id", required=True)
    
    # List commands
    subparsers.add_parser("list", help="List available apps")
    subparsers.add_parser("installed", help="List installed apps")
    subparsers.add_parser("runtime", help="Check runtime")
    
    args = parser.parse_args()
    
    if args.command == "deploy":
        config = json.loads(args.config)
        result = deploy_app(args.app_id, config, "cli")
        print(json.dumps(result, indent=2))
    elif args.command == "status":
        print(json.dumps(get_app_status(args.app_id), indent=2))
    elif args.command == "start":
        print(json.dumps(start_app(args.app_id), indent=2))
    elif args.command == "stop":
        print(json.dumps(stop_app(args.app_id), indent=2))
    elif args.command == "remove":
        print(json.dumps(remove_app(args.app_id), indent=2))
    elif args.command == "list":
        print(json.dumps(list_available_apps(), indent=2))
    elif args.command == "installed":
        print(json.dumps(list_installed_apps(), indent=2))
    elif args.command == "runtime":
        print(json.dumps(detect_runtime(), indent=2))
    else:
        parser.print_help()
