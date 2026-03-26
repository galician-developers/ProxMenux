"""
Centralized Startup Grace Period Management

This module provides a single source of truth for startup grace period logic.
During system boot, various transient issues occur (high latency, storage not ready,
QMP timeouts, etc.) that shouldn't trigger notifications or critical alerts.

Grace Periods:
- VM/CT aggregation: 3 minutes - Aggregate multiple VM/CT starts into one notification
- Health suppression: 5 minutes - Suppress transient health warnings/errors
- Shutdown suppression: 2 minutes - Suppress VM/CT stops during system shutdown

Categories suppressed during startup:
- storage: NFS/CIFS mounts may take time to become available
- vms: VMs may have QMP timeouts or startup delays
- network: Latency spikes during boot are normal
- services: PVE services may take time to fully initialize
"""

import time
import threading
from typing import Set, List, Tuple, Optional

# ─── Configuration ───────────────────────────────────────────────────────────

# Grace period durations (seconds)
STARTUP_VM_GRACE_SECONDS = 180      # 3 minutes for VM/CT start aggregation
STARTUP_HEALTH_GRACE_SECONDS = 300  # 5 minutes for health warning suppression
SHUTDOWN_GRACE_SECONDS = 120        # 2 minutes for VM/CT stop suppression

# Categories to suppress during startup grace period
# These categories typically have transient issues during boot
STARTUP_GRACE_CATEGORIES: Set[str] = {
    'storage',   # NFS/CIFS mounts may take time
    'vms',       # VMs may have QMP timeouts
    'network',   # Latency spikes during boot
    'services',  # PVE services initialization
}


# ─── Singleton State ─────────────────────────────────────────────────────────

class _StartupGraceState:
    """
    Thread-safe singleton managing all startup/shutdown grace period state.
    
    Initialized when the module loads (service start), which serves as the
    reference point for determining if we're still in the startup period.
    """
    
    _instance: Optional['_StartupGraceState'] = None
    _init_lock = threading.Lock()
    
    def __new__(cls) -> '_StartupGraceState':
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._lock = threading.Lock()
        
        # Startup time = when service started (module load time)
        self._startup_time: float = time.time()
        
        # Shutdown tracking
        self._shutdown_time: float = 0
        
        # VM/CT aggregation during startup
        self._startup_vms: List[Tuple[str, str, str]] = []  # [(vmid, vmname, 'vm'|'ct'), ...]
        self._startup_aggregated: bool = False
        
        self._initialized = True
    
    # ─── Startup Period Checks ───────────────────────────────────────────────
    
    def is_startup_vm_period(self) -> bool:
        """
        Check if we're within the VM/CT start aggregation period (3 min).
        
        During this period, individual VM/CT start notifications are collected
        and later sent as a single aggregated notification.
        """
        with self._lock:
            return (time.time() - self._startup_time) < STARTUP_VM_GRACE_SECONDS
    
    def is_startup_health_grace(self) -> bool:
        """
        Check if we're within the health suppression period (5 min).
        
        During this period:
        - Transient health warnings (latency, storage, etc.) are suppressed
        - CRITICAL/WARNING may be downgraded to INFO for certain categories
        - Health degradation notifications are skipped for grace categories
        """
        with self._lock:
            return (time.time() - self._startup_time) < STARTUP_HEALTH_GRACE_SECONDS
    
    def should_suppress_category(self, category: str) -> bool:
        """
        Check if notifications for a category should be suppressed.
        
        Args:
            category: Health category name (e.g., 'network', 'storage', 'vms')
        
        Returns:
            True if we're in grace period AND category is in STARTUP_GRACE_CATEGORIES
        """
        if category.lower() in STARTUP_GRACE_CATEGORIES:
            return self.is_startup_health_grace()
        return False
    
    def get_startup_elapsed(self) -> float:
        """Get seconds elapsed since service startup."""
        with self._lock:
            return time.time() - self._startup_time
    
    # ─── Shutdown Tracking ───────────────────────────────────────────────────
    
    def mark_shutdown(self):
        """
        Called when system_shutdown or system_reboot is detected.
        
        After this, VM/CT stop notifications will be suppressed for the
        shutdown grace period (expected stops during system shutdown).
        """
        with self._lock:
            self._shutdown_time = time.time()
    
    def is_host_shutting_down(self) -> bool:
        """
        Check if we're within the shutdown grace period.
        
        During this period, VM/CT stop events are expected and should not
        generate notifications.
        """
        with self._lock:
            if self._shutdown_time == 0:
                return False
            return (time.time() - self._shutdown_time) < SHUTDOWN_GRACE_SECONDS
    
    # ─── VM/CT Start Aggregation ─────────────────────────────────────────────
    
    def add_startup_vm(self, vmid: str, vmname: str, vm_type: str):
        """
        Record a VM/CT start during startup period for later aggregation.
        
        Args:
            vmid: VM/CT ID
            vmname: VM/CT name
            vm_type: 'vm' or 'ct'
        """
        with self._lock:
            self._startup_vms.append((vmid, vmname, vm_type))
    
    def get_and_clear_startup_vms(self) -> List[Tuple[str, str, str]]:
        """
        Get all recorded startup VMs and clear the list.
        
        Should be called once after the VM aggregation grace period ends
        to get all VMs that started during boot for a single notification.
        
        Returns:
            List of (vmid, vmname, vm_type) tuples
        """
        with self._lock:
            vms = self._startup_vms.copy()
            self._startup_vms = []
            self._startup_aggregated = True
            return vms
    
    def has_startup_vms(self) -> bool:
        """Check if there are any startup VMs recorded."""
        with self._lock:
            return len(self._startup_vms) > 0
    
    def was_startup_aggregated(self) -> bool:
        """Check if startup aggregation has already been processed."""
        with self._lock:
            return self._startup_aggregated


# ─── Module-level convenience functions ──────────────────────────────────────

# Global singleton instance
_state = _StartupGraceState()

def is_startup_vm_period() -> bool:
    """Check if we're within the VM/CT start aggregation period (3 min)."""
    return _state.is_startup_vm_period()

def is_startup_health_grace() -> bool:
    """Check if we're within the health suppression period (5 min)."""
    return _state.is_startup_health_grace()

def should_suppress_category(category: str) -> bool:
    """Check if notifications for a category should be suppressed during startup."""
    return _state.should_suppress_category(category)

def get_startup_elapsed() -> float:
    """Get seconds elapsed since service startup."""
    return _state.get_startup_elapsed()

def mark_shutdown():
    """Mark that system shutdown/reboot has been detected."""
    _state.mark_shutdown()

def is_host_shutting_down() -> bool:
    """Check if we're within the shutdown grace period."""
    return _state.is_host_shutting_down()

def add_startup_vm(vmid: str, vmname: str, vm_type: str):
    """Record a VM/CT start during startup period for aggregation."""
    _state.add_startup_vm(vmid, vmname, vm_type)

def get_and_clear_startup_vms() -> List[Tuple[str, str, str]]:
    """Get all recorded startup VMs and clear the list."""
    return _state.get_and_clear_startup_vms()

def has_startup_vms() -> bool:
    """Check if there are any startup VMs recorded."""
    return _state.has_startup_vms()

def was_startup_aggregated() -> bool:
    """Check if startup aggregation has already been processed."""
    return _state.was_startup_aggregated()


# ─── For backwards compatibility ─────────────────────────────────────────────

# Expose constants for external use
GRACE_CATEGORIES = STARTUP_GRACE_CATEGORIES
