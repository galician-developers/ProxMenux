"""
ProxMenux Notification Event Watchers
Detects Proxmox events from journald, PVE task log, and health monitor.

Architecture:
- JournalWatcher: Real-time stream of journald for critical events
- TaskWatcher: Real-time tail of /var/log/pve/tasks/index for VM/CT/backup events
- PollingCollector: Periodic poll of health_persistence pending notifications

All watchers put events into a shared Queue consumed by NotificationManager.

Author: MacRimi
"""

import os
import re
import json
import time
import hashlib
import socket
import sqlite3
import subprocess
import threading
from queue import Queue
from typing import Optional, Dict, Any, Tuple
from pathlib import Path


# ─── Event Object ─────────────────────────────────────────────────

class NotificationEvent:
    """Represents a detected event ready for notification dispatch.
    
    Fields:
        event_type:   Taxonomy key (e.g. 'vm_fail', 'auth_fail', 'split_brain')
        severity:     INFO | WARNING | CRITICAL
        data:         Payload dict with context (hostname, vmid, reason, etc.)
        source:       Origin: journal | tasks | health | proxmox_hook | cli | api | polling
        entity:       What is affected: node | vm | ct | storage | disk | network | cluster | user
        entity_id:    Specific identifier (vmid, IP, device, pool, interface, etc.)
        raw:          Original payload (webhook JSON or log line), optional
        fingerprint:  Stable dedup key: hostname:entity:entity_id:event_type
        event_id:     Short hash of fingerprint for correlation
        ts_epoch:     time.time() at creation
        ts_monotonic: time.monotonic() at creation (drift-safe for cooldown)
    """
    
    __slots__ = (
        'event_type', 'severity', 'data', 'timestamp', 'source',
        'entity', 'entity_id', 'raw',
        'fingerprint', 'event_id', 'ts_epoch', 'ts_monotonic',
    )
    
    def __init__(self, event_type: str, severity: str = 'INFO',
                 data: Optional[Dict[str, Any]] = None,
                 source: str = 'watcher',
                 entity: str = 'node', entity_id: str = '',
                 raw: Any = None):
        self.event_type = event_type
        self.severity = severity
        self.data = data or {}
        self.source = source
        self.entity = entity
        self.entity_id = entity_id
        self.raw = raw
        self.ts_epoch = time.time()
        self.ts_monotonic = time.monotonic()
        self.timestamp = self.ts_epoch  # backward compat
        
        # Build fingerprint for dedup/cooldown
        hostname = self.data.get('hostname', _hostname())
        if entity_id:
            fp_base = f"{hostname}:{entity}:{entity_id}:{event_type}"
        else:
            # When entity_id is empty, include a hash of title/body for uniqueness
            reason = self.data.get('reason', self.data.get('title', ''))
            stable_extra = hashlib.md5(reason.encode(errors='replace')).hexdigest()[:8] if reason else ''
            fp_base = f"{hostname}:{entity}:{event_type}:{stable_extra}"
        self.fingerprint = fp_base
        self.event_id = hashlib.md5(fp_base.encode()).hexdigest()[:12]
    
    def __repr__(self):
        return f"NotificationEvent({self.event_type}, {self.severity}, fp={self.fingerprint[:40]})"


def _hostname() -> str:
    try:
        return socket.gethostname().split('.')[0]
    except Exception:
        return 'proxmox'


# ─── Journal Watcher (Real-time) ─────────────────────────────────

class JournalWatcher:
    """Watches journald in real-time for critical system events.
    
    Uses 'journalctl -f -o json' subprocess to stream entries.
    Detects: auth failures, kernel panics, OOM, service crashes,
    disk I/O errors, split-brain, node disconnect, system shutdown,
    fail2ban bans, firewall blocks, permission changes.
    """
    
    def __init__(self, event_queue: Queue):
        self._queue = event_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._hostname = _hostname()
        
        # Dedup: track recent events to avoid duplicates
        self._recent_events: Dict[str, float] = {}
        self._dedup_window = 30  # seconds
    
    def start(self):
        """Start the journal watcher thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True,
                                        name='journal-watcher')
        self._thread.start()
    
    def stop(self):
        """Stop the journal watcher."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
    
    def _watch_loop(self):
        """Main watch loop with auto-restart on failure."""
        while self._running:
            try:
                self._run_journalctl()
            except Exception as e:
                print(f"[JournalWatcher] Error: {e}")
            if self._running:
                time.sleep(5)  # Wait before restart
    
    def _run_journalctl(self):
        """Run journalctl -f and process output line by line."""
        cmd = ['journalctl', '-f', '-o', 'json', '--no-pager',
               '-n', '0']  # Start from now, don't replay history
        
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1
        )
        
        for line in self._process.stdout:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                self._process_entry(entry)
            except (json.JSONDecodeError, KeyError):
                # Try plain text matching as fallback
                self._process_plain(line)
        
        if self._process:
            self._process.wait()
    
    def _process_entry(self, entry: Dict):
        """Process a parsed journald JSON entry."""
        msg = entry.get('MESSAGE', '')
        if not msg or not isinstance(msg, str):
            return
        
        unit = entry.get('_SYSTEMD_UNIT', '')
        syslog_id = entry.get('SYSLOG_IDENTIFIER', '')
        priority = int(entry.get('PRIORITY', 6))
        
        self._check_auth_failure(msg, syslog_id, entry)
        self._check_fail2ban(msg, syslog_id)
        self._check_kernel_critical(msg, syslog_id, priority)
        self._check_service_failure(msg, unit)
        self._check_disk_io(msg, syslog_id, priority)
        self._check_cluster_events(msg, syslog_id)
        self._check_system_shutdown(msg, syslog_id)
        self._check_permission_change(msg, syslog_id)
        self._check_firewall(msg, syslog_id)
    
    def _process_plain(self, line: str):
        """Fallback: process a plain text log line."""
        self._check_auth_failure(line, '', {})
        self._check_fail2ban(line, '')
        self._check_kernel_critical(line, '', 6)
        self._check_cluster_events(line, '')
        self._check_system_shutdown(line, '')
    
    # ── Detection methods ──
    
    def _check_auth_failure(self, msg: str, syslog_id: str, entry: Dict):
        """Detect authentication failures (SSH, PAM, PVE)."""
        patterns = [
            (r'Failed password for (?:invalid user )?(\S+) from (\S+)', 'ssh'),
            (r'authentication failure.*rhost=(\S+).*user=(\S+)', 'pam'),
            (r'pvedaemon\[.*authentication failure.*rhost=(\S+)', 'pve'),
        ]
        
        for pattern, service in patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                groups = match.groups()
                if service == 'ssh':
                    username, source_ip = groups[0], groups[1]
                elif service == 'pam':
                    source_ip, username = groups[0], groups[1]
                else:
                    source_ip = groups[0]
                    username = 'unknown'
                
                self._emit('auth_fail', 'WARNING', {
                    'source_ip': source_ip,
                    'username': username,
                    'service': service,
                    'hostname': self._hostname,
                }, entity='user', entity_id=source_ip)
                return
    
    def _check_fail2ban(self, msg: str, syslog_id: str):
        """Detect Fail2Ban IP bans."""
        if 'fail2ban' not in msg.lower() and syslog_id != 'fail2ban-server':
            return
        
        # Ban detected
        ban_match = re.search(r'Ban\s+(\S+)', msg)
        if ban_match:
            ip = ban_match.group(1)
            jail_match = re.search(r'\[(\w+)\]', msg)
            jail = jail_match.group(1) if jail_match else 'unknown'
            
            self._emit('ip_block', 'INFO', {
                'source_ip': ip,
                'jail': jail,
                'failures': '',
                'hostname': self._hostname,
            }, entity='user', entity_id=ip)
    
    def _check_kernel_critical(self, msg: str, syslog_id: str, priority: int):
        """Detect kernel panics, OOM, segfaults, hardware errors."""
        # Only process messages from kernel or systemd (not app-level logs)
        if syslog_id and syslog_id not in ('kernel', 'systemd', 'systemd-coredump', ''):
            return
        
        # Filter out normal kernel messages that are NOT problems
        _KERNEL_NOISE = [
            r'vfio-pci\s+\S+:\s*reset',       # PCI passthrough resets (normal during VM start/stop)
            r'vfio-pci\s+\S+:\s*resetting',
            r'entered\s+(?:promiscuous|allmulticast)\s+mode',  # Network bridge ops
            r'entered\s+(?:blocking|forwarding|disabled)\s+state',  # Bridge STP
            r'tap\d+i\d+:',                     # TAP interface events
            r'vmbr\d+:.*port\s+\d+',            # Bridge port events
        ]
        for noise in _KERNEL_NOISE:
            if re.search(noise, msg, re.IGNORECASE):
                return
        
        critical_patterns = {
            r'kernel panic':       ('system_problem', 'CRITICAL', 'Kernel panic'),
            r'Out of memory':      ('system_problem', 'CRITICAL', 'Out of memory killer activated'),
            r'segfault':           ('system_problem', 'WARNING',  'Segmentation fault detected'),
            r'BUG:':               ('system_problem', 'CRITICAL', 'Kernel BUG detected'),
            r'Call Trace:':        ('system_problem', 'WARNING',  'Kernel call trace'),
            r'I/O error.*dev\s+(\S+)': ('disk_io_error', 'CRITICAL', 'Disk I/O error'),
            r'EXT4-fs error':      ('disk_io_error', 'CRITICAL', 'Filesystem error'),
            r'BTRFS error':        ('disk_io_error', 'CRITICAL', 'Filesystem error'),
            r'XFS.*error':         ('disk_io_error', 'CRITICAL', 'Filesystem error'),
            r'ZFS.*error':         ('disk_io_error', 'CRITICAL', 'ZFS pool error'),
            r'mce:.*Hardware Error': ('system_problem', 'CRITICAL', 'Hardware error (MCE)'),
        }
        
        for pattern, (event_type, severity, reason) in critical_patterns.items():
            if re.search(pattern, msg, re.IGNORECASE):
                entity = 'node'
                entity_id = ''
                
                # Build a context-rich reason from the journal message.
                # The raw msg contains process name, PID, addresses, library, etc.
                enriched = reason
                
                if 'segfault' in pattern:
                    # Kernel segfault: "process[PID]: segfault at ADDR ... in lib.so"
                    m = re.search(r'(\S+)\[(\d+)\].*segfault', msg)
                    proc_name = m.group(1) if m else ''
                    proc_pid = m.group(2) if m else ''
                    lib_match = re.search(r'\bin\s+(\S+)', msg)
                    lib_name = lib_match.group(1) if lib_match else ''
                    
                    parts = [reason]
                    if proc_name:
                        parts.append(f"Process: {proc_name}" + (f" (PID {proc_pid})" if proc_pid else ''))
                    if lib_name:
                        parts.append(f"Module: {lib_name}")
                    enriched = '\n'.join(parts)
                
                elif 'Out of memory' in pattern:
                    # OOM: "Out of memory: Killed process PID (name)"
                    m = re.search(r'Killed process\s+(\d+)\s+\(([^)]+)\)', msg)
                    if m:
                        enriched = f"{reason}\nKilled: {m.group(2)} (PID {m.group(1)})"
                    else:
                        enriched = f"{reason}\n{msg[:300]}"
                
                elif event_type == 'disk_io_error':
                    # Include device and raw message for disk/fs errors
                    dev_match = re.search(r'dev\s+(\S+)', msg)
                    if dev_match:
                        entity = 'disk'
                        entity_id = dev_match.group(1)
                        enriched = f"{reason}\nDevice: {dev_match.group(1)}"
                    else:
                        enriched = f"{reason}\n{msg[:300]}"
                
                else:
                    # Generic: include the raw journal message for context
                    enriched = f"{reason}\n{msg[:300]}"
                
                data = {'reason': enriched, 'hostname': self._hostname}
                if entity == 'disk':
                    data['device'] = entity_id
                
                self._emit(event_type, severity, data, entity=entity, entity_id=entity_id)
                return
    
    def _check_service_failure(self, msg: str, unit: str):
        """Detect critical service failures with enriched context."""
        # Filter out noise -- these are normal systemd transient units,
        # not real service failures worth alerting about.
        _NOISE_PATTERNS = [
            r'session-\d+\.scope',          # SSH/login sessions
            r'user@\d+\.service',           # Per-user service managers
            r'user-runtime-dir@\d+',        # User runtime dirs
            r'systemd-coredump@',           # Coredump handlers (transient)
            r'run-.*\.mount',               # Transient mounts
        ]
        for noise in _NOISE_PATTERNS:
            if re.search(noise, msg) or re.search(noise, unit):
                return
        
        service_patterns = [
            r'Failed to start (.+)',
            r'Unit (\S+) (?:entered failed state|failed)',
            r'(\S+)\.service: (?:Main process exited|Failed with result)',
        ]
        
        for pattern in service_patterns:
            match = re.search(pattern, msg)
            if match:
                service_name = match.group(1)
                data = {
                    'service_name': service_name,
                    'reason': msg[:300],
                    'hostname': self._hostname,
                }
                
                # Enrich PVE VM/CT services with guest name and context
                # pve-container@101 -> LXC container 101
                # qemu-server@100  -> QEMU VM 100
                pve_match = re.match(
                    r'(pve-container|qemu-server)@(\d+)', service_name)
                if pve_match:
                    svc_type = pve_match.group(1)
                    vmid = pve_match.group(2)
                    vm_name = self._resolve_vm_name(vmid)
                    
                    if svc_type == 'pve-container':
                        guest_type = 'LXC container'
                    else:
                        guest_type = 'QEMU VM'
                    
                    display = f"{guest_type} {vmid}"
                    if vm_name:
                        display = f"{guest_type} {vmid} ({vm_name})"
                    
                    data['service_name'] = service_name
                    data['vmid'] = vmid
                    data['vmname'] = vm_name
                    data['guest_type'] = guest_type
                    data['display_name'] = display
                    data['reason'] = (
                        f"{display} failed to start.\n{msg[:300]}"
                    )
                
                self._emit('service_fail', 'WARNING', data,
                           entity='node', entity_id=service_name)
                return
    
    def _resolve_vm_name(self, vmid: str) -> str:
        """Try to resolve VMID to a guest name from PVE config files."""
        if not vmid:
            return ''
        # Check QEMU configs
        for base in ['/etc/pve/qemu-server', '/etc/pve/lxc']:
            conf = os.path.join(base, f'{vmid}.conf')
            try:
                with open(conf) as f:
                    for line in f:
                        if line.startswith('hostname:') or line.startswith('name:'):
                            return line.split(':', 1)[1].strip()
            except (OSError, IOError):
                continue
        return ''
    
    def _check_disk_io(self, msg: str, syslog_id: str, priority: int):
        """Detect disk I/O errors from kernel messages."""
        if syslog_id != 'kernel' and priority > 3:
            return
        
        io_patterns = [
            r'blk_update_request: I/O error.*dev (\S+)',
            r'Buffer I/O error on device (\S+)',
            r'SCSI error.*sd(\w)',
            r'ata\d+.*error',
        ]
        
        for pattern in io_patterns:
            match = re.search(pattern, msg)
            if match:
                device = match.group(1) if match.lastindex else 'unknown'
                self._emit('disk_io_error', 'CRITICAL', {
                    'device': device,
                    'reason': msg[:200],
                    'hostname': self._hostname,
                }, entity='disk', entity_id=device)
                return
    
    def _check_cluster_events(self, msg: str, syslog_id: str):
        """Detect cluster split-brain and node disconnect."""
        msg_lower = msg.lower()
        
        # Split-brain
        if any(p in msg_lower for p in ['split-brain', 'split brain',
                                          'fencing required', 'cluster partition']):
            quorum = 'unknown'
            if 'quorum' in msg_lower:
                quorum = 'lost' if 'lost' in msg_lower else 'valid'
            
            self._emit('split_brain', 'CRITICAL', {
                'quorum': quorum,
                'reason': msg[:200],
                'hostname': self._hostname,
            }, entity='cluster', entity_id=self._hostname)
            return
        
        # Node disconnect
        if (('quorum' in msg_lower and 'lost' in msg_lower) or
            ('node' in msg_lower and any(w in msg_lower for w in ['left', 'offline', 'lost']))):
            
            node_match = re.search(r'[Nn]ode\s+(\S+)', msg)
            node_name = node_match.group(1) if node_match else 'unknown'
            
            self._emit('node_disconnect', 'CRITICAL', {
                'node_name': node_name,
                'hostname': self._hostname,
            }, entity='cluster', entity_id=node_name)
    
    def _check_system_shutdown(self, msg: str, syslog_id: str):
        """Detect system shutdown/reboot."""
        if 'systemd-journald' in syslog_id or 'systemd' in syslog_id:
            if 'Journal stopped' in msg or 'Stopping Journal Service' in msg:
                self._emit('system_shutdown', 'WARNING', {
                    'reason': 'System journal stopped',
                    'hostname': self._hostname,
                }, entity='node', entity_id='')
            elif 'Shutting down' in msg or 'System is rebooting' in msg:
                event = 'system_reboot' if 'reboot' in msg.lower() else 'system_shutdown'
                self._emit(event, 'WARNING', {
                    'reason': msg[:200],
                    'hostname': self._hostname,
                }, entity='node', entity_id='')
    
    def _check_permission_change(self, msg: str, syslog_id: str):
        """Detect user permission changes in PVE."""
        permission_patterns = [
            (r'set permissions.*user\s+(\S+)', 'Permission changed'),
            (r'user added to group.*?(\S+)', 'Added to group'),
            (r'user removed from group.*?(\S+)', 'Removed from group'),
            (r'ACL updated.*?(\S+)', 'ACL updated'),
            (r'Role assigned.*?(\S+)', 'Role assigned'),
        ]
        
        for pattern, action in permission_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                username = match.group(1)
                self._emit('user_permission_change', 'INFO', {
                    'username': username,
                    'change_details': action,
                    'hostname': self._hostname,
                }, entity='user', entity_id=username)
                return
    
    def _check_firewall(self, msg: str, syslog_id: str):
        """Detect firewall issues (not individual drops, but rule errors)."""
        if re.search(r'pve-firewall.*(?:error|failed|unable)', msg, re.IGNORECASE):
            self._emit('firewall_issue', 'WARNING', {
                'reason': msg[:200],
                'hostname': self._hostname,
            }, entity='network', entity_id='')
    
    # ── Emit helper ──
    
    def _emit(self, event_type: str, severity: str, data: Dict,
              entity: str = 'node', entity_id: str = ''):
        """Emit event to queue with short-term deduplication (30s window)."""
        event = NotificationEvent(
            event_type, severity, data, source='journal',
            entity=entity, entity_id=entity_id,
        )
        
        now = time.time()
        last = self._recent_events.get(event.fingerprint, 0)
        if now - last < self._dedup_window:
            return  # Skip duplicate within 30s window
        
        self._recent_events[event.fingerprint] = now
        
        # Cleanup old dedup entries periodically
        if len(self._recent_events) > 200:
            cutoff = now - self._dedup_window * 2
            self._recent_events = {
                k: v for k, v in self._recent_events.items() if v > cutoff
            }
        
        self._queue.put(event)


# ─── Task Watcher (Real-time) ────────────────────────────────────

class TaskWatcher:
    """Watches /var/log/pve/tasks/index for VM/CT and backup events.
    
    The PVE task index file is appended when tasks start/finish.
    Format: UPID:node:pid:pstart:starttime:type:id:user:
    Final status is recorded when task completes.
    """
    
    TASK_LOG = '/var/log/pve/tasks/index'
    
    # Map PVE task types to our event types
    TASK_MAP = {
        'qmstart':    ('vm_start',    'INFO'),
        'qmstop':     ('vm_stop',     'INFO'),
        'qmshutdown': ('vm_shutdown', 'INFO'),
        'qmreboot':   ('vm_restart',  'INFO'),
        'qmreset':    ('vm_restart',  'INFO'),
        'vzstart':    ('ct_start',    'INFO'),
        'vzstop':     ('ct_stop',     'INFO'),
        'vzshutdown': ('ct_stop',     'INFO'),
        'vzdump':     ('backup_start', 'INFO'),
        'qmsnapshot': ('snapshot_complete', 'INFO'),
        'vzsnapshot': ('snapshot_complete', 'INFO'),
        'qmigrate':   ('migration_start', 'INFO'),
        'vzmigrate':  ('migration_start', 'INFO'),
    }
    
    def __init__(self, event_queue: Queue):
        self._queue = event_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._hostname = _hostname()
        self._last_position = 0
        # Track active vzdump jobs. While a vzdump is running, VM/CT
        # start/stop/shutdown events are backup-induced (mode=stop/snapshot)
        # and should NOT generate notifications.
        self._active_vzdump_ts: float = 0  # timestamp of last vzdump start
        self._VZDUMP_WINDOW = 14400  # 4h max backup window
    
    def start(self):
        if self._running:
            return
        self._running = True
        
        # Start at end of file
        if os.path.exists(self.TASK_LOG):
            try:
                self._last_position = os.path.getsize(self.TASK_LOG)
            except OSError:
                self._last_position = 0
        
        self._thread = threading.Thread(target=self._watch_loop, daemon=True,
                                        name='task-watcher')
        self._thread.start()
    
    def stop(self):
        self._running = False
    
    def _watch_loop(self):
        """Poll the task index file for new entries."""
        while self._running:
            try:
                if os.path.exists(self.TASK_LOG):
                    current_size = os.path.getsize(self.TASK_LOG)
                    
                    if current_size < self._last_position:
                        # File was truncated/rotated
                        self._last_position = 0
                    
                    if current_size > self._last_position:
                        with open(self.TASK_LOG, 'r') as f:
                            f.seek(self._last_position)
                            new_lines = f.readlines()
                            self._last_position = f.tell()
                        
                        for line in new_lines:
                            self._process_task_line(line.strip())
            except Exception as e:
                print(f"[TaskWatcher] Error reading task log: {e}")
            
            time.sleep(2)  # Check every 2 seconds
    
    def _process_task_line(self, line: str):
        """Process a single task index line.
        
        PVE task index format (space-separated):
        UPID endtime status
        Where UPID = UPID:node:pid:pstart:starttime:type:id:user:
        """
        if not line:
            return
        
        parts = line.split()
        if not parts:
            return
        
        upid = parts[0]
        status = parts[2] if len(parts) >= 3 else ''
        
        # Parse UPID
        upid_parts = upid.split(':')
        if len(upid_parts) < 8:
            return
        
        task_type = upid_parts[5]
        vmid = upid_parts[6]
        user = upid_parts[7]
        
        # Get VM/CT name
        vmname = self._get_vm_name(vmid) if vmid else ''
        
        # Map to event type
        event_info = self.TASK_MAP.get(task_type)
        if not event_info:
            return
        
        event_type, default_severity = event_info
        
        # ── Track active vzdump jobs ──
        # When a vzdump starts, record its timestamp. While active, we
        # suppress start/stop/shutdown of individual VMs -- those are just
        # the backup stopping and restarting guests (mode=stop).
        if task_type == 'vzdump':
            if not status:
                # vzdump just started
                self._active_vzdump_ts = time.time()
            else:
                # vzdump finished -- clear after a small grace period
                # (VMs may still be restarting)
                def _clear_vzdump():
                    time.sleep(30)
                    self._active_vzdump_ts = 0
                threading.Thread(target=_clear_vzdump, daemon=True,
                                 name='clear-vzdump').start()
        
        # Check if task failed
        is_error = status and status != 'OK' and status != ''
        
        if is_error:
            # Override to failure event
            if 'start' in event_type:
                event_type = event_type.replace('_start', '_fail')
            elif 'complete' in event_type:
                event_type = event_type.replace('_complete', '_fail')
            severity = 'CRITICAL'
        elif status == 'OK':
            # Task completed successfully
            if event_type == 'backup_start':
                event_type = 'backup_complete'
            elif event_type == 'migration_start':
                event_type = 'migration_complete'
            severity = 'INFO'
        else:
            # Task just started (no status yet)
            severity = default_severity
        
        data = {
            'vmid': vmid,
            'vmname': vmname or f'ID {vmid}',
            'hostname': self._hostname,
            'user': user,
            'reason': status if is_error else '',
            'target_node': '',
            'size': '',
            'snapshot_name': '',
        }
        
        # Determine entity type from task type
        entity = 'ct' if task_type.startswith('vz') else 'vm'
        
        # Backup and replication events are handled EXCLUSIVELY by the PVE
        # webhook, which delivers much richer data (full logs, sizes, durations,
        # filenames). TaskWatcher skips these entirely to avoid duplicates.
        _WEBHOOK_EXCLUSIVE = {'backup_complete', 'backup_fail', 'backup_start',
                              'replication_complete', 'replication_fail'}
        if event_type in _WEBHOOK_EXCLUSIVE:
            return
        
        # Suppress VM/CT start/stop/shutdown while a vzdump is active.
        # These are backup-induced operations (mode=stop), not user actions.
        # Exception: if a VM/CT FAILS to start after backup, that IS important.
        _BACKUP_NOISE = {'vm_start', 'vm_stop', 'vm_shutdown', 'vm_restart',
                         'ct_start', 'ct_stop'}
        vzdump_age = time.time() - self._active_vzdump_ts if self._active_vzdump_ts else float('inf')
        if event_type in _BACKUP_NOISE and vzdump_age < self._VZDUMP_WINDOW:
            # Allow through only if it's a FAILURE (e.g. VM failed to start)
            if not is_error:
                return
        
        self._queue.put(NotificationEvent(
            event_type, severity, data, source='tasks',
            entity=entity, entity_id=vmid,
        ))
    
    def _get_vm_name(self, vmid: str) -> str:
        """Try to resolve VMID to name via config files."""
        if not vmid:
            return ''
        
        # Try QEMU
        conf_path = f'/etc/pve/qemu-server/{vmid}.conf'
        name = self._read_name_from_conf(conf_path)
        if name:
            return name
        
        # Try LXC
        conf_path = f'/etc/pve/lxc/{vmid}.conf'
        name = self._read_name_from_conf(conf_path)
        if name:
            return name
        
        return ''
    
    @staticmethod
    def _read_name_from_conf(path: str) -> str:
        """Read 'name:' or 'hostname:' from PVE config file."""
        try:
            if not os.path.exists(path):
                return ''
            with open(path, 'r') as f:
                for line in f:
                    if line.startswith('name:'):
                        return line.split(':', 1)[1].strip()
                    if line.startswith('hostname:'):
                        return line.split(':', 1)[1].strip()
        except (IOError, PermissionError):
            pass
        return ''


# ─── Polling Collector ────────────────────────────────────────────

class PollingCollector:
    """Periodic collector that polls health state independently.
    
    Architecture:
    - Completely independent from Health Monitor's suppression system.
      Suppression Duration only affects the UI health badge; it NEVER blocks
      notifications.
    - Reads ``get_active_errors()`` (ALL active errors, even suppressed ones)
      and decides when to notify based on its own 24-hour cycle.
    - For *new* errors (first_seen within the last poll interval), notifies
      immediately.
    - For *persistent* errors (already known), re-notifies once every 24 h.
    - Update checks run on their own 24-h timer and include security counts.
    
    Tracking is stored in ``notification_last_sent`` (same DB).
    """
    
    DIGEST_INTERVAL = 86400       # 24 h between re-notifications
    UPDATE_CHECK_INTERVAL = 86400 # 24 h between update scans
    NEW_ERROR_WINDOW = 120        # seconds – errors younger than this are "new"
    
    _ENTITY_MAP = {
        'cpu': ('node', ''), 'memory': ('node', ''), 'temperature': ('node', ''),
        'disk': ('storage', ''), 'network': ('network', ''),
        'pve_services': ('node', ''), 'security': ('user', ''),
        'updates': ('node', ''), 'storage': ('storage', ''),
    }
    
    # Map health-persistence category names to our TEMPLATES event types.
    # These must match keys in notification_templates.TEMPLATES exactly.
    _CATEGORY_TO_EVENT_TYPE = {
        'cpu': 'cpu_high',
        'memory': 'ram_high',
        'load': 'load_high',
        'temperature': 'temp_high',
        'disk': 'disk_space_low',
        'storage': 'storage_unavailable',
        'network': 'network_down',
        'pve_services': 'service_fail',
        'security': 'auth_fail',
        'updates': 'update_available',
        'zfs': 'disk_io_error',
        'smart': 'disk_io_error',
        'disks': 'disk_io_error',
        'logs': 'system_problem',
        'vms': 'system_problem',
    }
    
    def __init__(self, event_queue: Queue, poll_interval: int = 60):
        self._queue = event_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = poll_interval
        self._hostname = _hostname()
        self._last_update_check = 0
        # In-memory cache: error_key -> last notification timestamp
        self._last_notified: Dict[str, float] = {}
        # Track known error keys so we can detect truly new ones
        self._known_errors: set = set()
        self._first_poll_done = False
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._load_last_notified()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name='polling-collector')
        self._thread.start()
    
    def stop(self):
        self._running = False
    
    # ── Main loop ──────────────────────────────────────────────
    
    def _poll_loop(self):
        """Main polling loop."""
        # Initial delay to let health monitor warm up
        for _ in range(15):
            if not self._running:
                return
            time.sleep(1)
        
        while self._running:
            try:
                self._check_persistent_health()
                self._check_updates()
            except Exception as e:
                print(f"[PollingCollector] Error: {e}")
            
            for _ in range(self._poll_interval):
                if not self._running:
                    return
                time.sleep(1)
    
    # ── Health errors (independent of suppression) ─────────────
    
    def _check_persistent_health(self):
        """Read ALL active errors from health_persistence and decide
        whether each one warrants a notification right now.
        
        Rules:
        - A *new* error (not in _known_errors) -> notify immediately
        - A *persistent* error already notified -> re-notify after 24 h
        - Uses its own tracking, NOT the health monitor's needs_notification flag
        """
        try:
            from health_persistence import health_persistence
            errors = health_persistence.get_active_errors()
        except ImportError:
            return
        except Exception as e:
            print(f"[PollingCollector] get_active_errors failed: {e}")
            return
        
        now = time.time()
        current_keys = set()
        
        for error in errors:
            error_key = error.get('error_key', '')
            if not error_key:
                continue
            
            current_keys.add(error_key)
            category = error.get('category', '')
            severity = error.get('severity', 'WARNING')
            reason = error.get('reason', '')
            
            # Determine if we should notify
            is_new = error_key not in self._known_errors and self._first_poll_done
            last_sent = self._last_notified.get(error_key, 0)
            is_due = (now - last_sent) >= self.DIGEST_INTERVAL
            
            if not is_new and not is_due:
                continue
            
            # Map to our event type
            event_type = self._CATEGORY_TO_EVENT_TYPE.get(category, 'system_problem')
            entity, eid = self._ENTITY_MAP.get(category, ('node', ''))
            
            data = {
                'hostname': self._hostname,
                'category': category,
                'reason': reason,
                'error_key': error_key,
                'severity': severity,
                'first_seen': error.get('first_seen', ''),
                'last_seen': error.get('last_seen', ''),
                'is_persistent': not is_new,
            }
            
            # Include extra details if present
            details = error.get('details')
            if isinstance(details, dict):
                data.update(details)
            elif isinstance(details, str):
                try:
                    data.update(json.loads(details))
                except (json.JSONDecodeError, TypeError):
                    pass
            
            self._queue.put(NotificationEvent(
                event_type, severity, data, source='health',
                entity=entity, entity_id=eid or error_key,
            ))
            
            # Track that we notified
            self._last_notified[error_key] = now
            self._persist_last_notified(error_key, now)
        
        # Remove tracking for errors that resolved
        resolved = self._known_errors - current_keys
        for key in resolved:
            self._last_notified.pop(key, None)
        
        self._known_errors = current_keys
        self._first_poll_done = True
    
    # ── Update check (enriched) ────────────────────────────────
    
    def _check_updates(self):
        """Check for available system updates every 24 h.
        
        Enriched output: total count, security updates, PVE version hint,
        and top package names.
        """
        now = time.time()
        if now - self._last_update_check < self.UPDATE_CHECK_INTERVAL:
            return
        
        self._last_update_check = now
        
        try:
            result = subprocess.run(
                ['apt-get', '-s', 'upgrade'],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return
            
            lines = [l for l in result.stdout.split('\n') if l.startswith('Inst ')]
            total = len(lines)
            if total == 0:
                return
            
            packages = [l.split()[1] for l in lines]
            security = [p for p in packages if any(
                kw in p.lower() for kw in ('security', 'cve', 'openssl', 'libssl')
            )]
            
            # Also detect security updates via apt changelog / Debian-Security origin
            sec_result = subprocess.run(
                ['apt-get', '-s', 'upgrade', '-o', 'Dir::Etc::SourceList=/dev/null',
                 '-o', 'Dir::Etc::SourceParts=/dev/null'],
                capture_output=True, text=True, timeout=30,
            )
            # Count lines from security repo (rough heuristic)
            sec_count = max(len(security), 0)
            try:
                sec_output = subprocess.run(
                    ['apt-get', '-s', '--only-upgrade', 'install'] + packages[:50],
                    capture_output=True, text=True, timeout=30,
                )
                for line in sec_output.stdout.split('\n'):
                    if 'security' in line.lower() and 'Inst ' in line:
                        sec_count += 1
            except Exception:
                pass
            
            # Check for PVE version upgrade
            pve_packages = [p for p in packages if 'pve-' in p.lower() or 'proxmox-' in p.lower()]
            
            # Build display details
            top_pkgs = packages[:8]
            details = ', '.join(top_pkgs)
            if total > 8:
                details += f', ... +{total - 8} more'
            
            data = {
                'hostname': self._hostname,
                'count': str(total),
                'security_count': str(sec_count),
                'details': details,
                'packages': ', '.join(packages[:20]),
            }
            if pve_packages:
                data['pve_packages'] = ', '.join(pve_packages)
            
            self._queue.put(NotificationEvent(
                'update_available', 'INFO', data,
                source='polling', entity='node', entity_id='',
            ))
        except Exception:
            pass
    
    # ── Persistence helpers ────────────────────────────────────
    
    def _load_last_notified(self):
        """Load per-error notification timestamps from DB on startup."""
        try:
            db_path = Path('/usr/local/share/proxmenux/health_monitor.db')
            if not db_path.exists():
                return
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fingerprint, last_sent_ts FROM notification_last_sent "
                "WHERE fingerprint LIKE 'health_%'"
            )
            for fp, ts in cursor.fetchall():
                error_key = fp.replace('health_', '', 1)
                self._last_notified[error_key] = ts
                self._known_errors.add(error_key)
            conn.close()
        except Exception as e:
            print(f"[PollingCollector] Failed to load last_notified: {e}")
    
    def _persist_last_notified(self, error_key: str, ts: float):
        """Save per-error notification timestamp to DB."""
        try:
            db_path = Path('/usr/local/share/proxmenux/health_monitor.db')
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            fp = f'health_{error_key}'
            conn.execute('''
                INSERT OR REPLACE INTO notification_last_sent (fingerprint, last_sent_ts, count)
                VALUES (?, ?, COALESCE(
                    (SELECT count + 1 FROM notification_last_sent WHERE fingerprint = ?), 1
                ))
            ''', (fp, int(ts), fp))
            conn.commit()
            conn.close()
        except Exception:
            pass


# ─── Proxmox Webhook Receiver ───────────────────────────────────

class ProxmoxHookWatcher:
    """Receives native Proxmox VE notifications via local webhook endpoint.
    
    Configured automatically via /etc/pve/notifications.cfg (endpoint +
    matcher blocks). The setup-webhook API writes these blocks on first
    enable. See flask_notification_routes.py for details.
    
    Payload varies by source (storage, replication, cluster, PBS, apt).
    This class normalizes them into NotificationEvent objects.
    """
    
    def __init__(self, event_queue: Queue):
        self._queue = event_queue
        self._hostname = _hostname()
    
    def process_webhook(self, payload: dict) -> dict:
        """Process an incoming Proxmox webhook payload.
        
        The PVE webhook is the PRIMARY source for vzdump, replication,
        fencing, package-updates and system-mail events.  PVE sends rich
        detail (full logs, sizes, durations) that TaskWatcher cannot match.
        
        Body template delivers:
          {title, message, severity, timestamp, fields: {type, hostname, job-id}}
        
        Returns: {'accepted': bool, 'event_type': str, 'event_id': str}
        """
        if not payload:
            return {'accepted': False, 'error': 'Empty payload'}
        
        # ── Extract structured PVE fields ──
        fields = payload.get('fields') or {}
        if isinstance(fields, str):
            # Edge case: {{ json fields }} rendered as string instead of dict
            try:
                import json
                fields = json.loads(fields)
            except (json.JSONDecodeError, ValueError):
                fields = {}
        
        pve_type = fields.get('type', '').lower().strip()
        pve_hostname = fields.get('hostname', self._hostname)
        pve_job_id = fields.get('job-id', '')
        
        title = payload.get('title', '')
        message = payload.get('message', payload.get('body', ''))
        severity_raw = payload.get('severity', 'info').lower().strip()
        timestamp = payload.get('timestamp', '')
        
        # ── Classify by PVE type (direct, no heuristics needed) ──
        import re
        event_type, entity, entity_id = self._classify_pve(
            pve_type, severity_raw, title, message
        )
        
        # Discard meta-events
        if event_type == '_skip':
            return {'accepted': False, 'skipped': True, 'reason': 'Meta-event filtered'}
        
        severity = self._map_severity(severity_raw)
        
        # ── Build rich data dict ──
        # For webhook events, PVE's `message` IS the notification body.
        # It contains full vzdump logs, package lists, error details, etc.
        # We pass it as 'pve_message' so templates can use it directly.
        data = {
            'hostname': pve_hostname,
            'pve_type': pve_type,
            'pve_message': message,
            'pve_title': title,
            'title': title,
            'job_id': pve_job_id,
        }
        
        # Extract VMID and VM name from message for vzdump events
        if pve_type == 'vzdump' and message:
            # PVE vzdump messages contain lines like:
            #   "INFO: Starting Backup of VM 100 (qemu)"
            #   "VMID       Name   Status   Time   Size   Filename"
            #   "100   arch-linux    OK    00:05:30   1.2G   /path/to/file"
            vmids = re.findall(r'(?:VM|CT)\s+(\d+)', message, re.IGNORECASE)
            if vmids:
                data['vmid'] = vmids[0]
                entity_id = vmids[0]
            # Try to extract VM name from the table line
            name_m = re.search(r'(\d+)\s+(\S+)\s+(?:OK|ERROR|WARNINGS)', message)
            if name_m:
                data['vmname'] = name_m.group(2)
            # Extract size from "Total size: X"
            size_m = re.search(r'Total size:\s*(.+?)(?:\n|$)', message)
            if size_m:
                data['size'] = size_m.group(1).strip()
            # Extract duration from "Total running time: X"
            dur_m = re.search(r'Total running time:\s*(.+?)(?:\n|$)', message)
            if dur_m:
                data['duration'] = dur_m.group(1).strip()
        
        event = NotificationEvent(
            event_type=event_type,
            severity=severity,
            data=data,
            source='proxmox_hook',
            entity=entity,
            entity_id=entity_id,
            raw=payload,
        )
        
        self._queue.put(event)
        return {'accepted': True, 'event_type': event_type, 'event_id': event.event_id}
    
    def _classify_pve(self, pve_type: str, severity: str,
                      title: str, message: str) -> tuple:
        """Classify using PVE's structured fields.type.
        
        Returns (event_type, entity, entity_id).
        """
        title_lower = (title or '').lower()
        
        # Skip overall/updates status change meta-events
        if 'overall' in title_lower and ('changed' in title_lower or 'status' in title_lower):
            return '_skip', '', ''
        if 'updates' in title_lower and ('changed' in title_lower or 'status' in title_lower):
            return '_skip', '', ''
        
        # ── Direct classification by PVE type ──
        if pve_type == 'vzdump':
            if severity in ('error', 'err'):
                return 'backup_fail', 'vm', ''
            return 'backup_complete', 'vm', ''
        
        if pve_type == 'fencing':
            return 'split_brain', 'node', ''
        
        if pve_type == 'replication':
            return 'replication_fail', 'vm', ''
        
        if pve_type == 'package-updates':
            return 'update_available', 'node', ''
        
        if pve_type == 'system-mail':
            return 'system_mail', 'node', ''
        
        # ── Fallback for unknown/empty pve_type ──
        # (e.g. test notifications, future PVE event types)
        msg_lower = (message or '').lower()
        text = f"{title_lower} {msg_lower}"
        
        if 'vzdump' in text or 'backup' in text:
            import re
            m = re.search(r'(?:vm|ct)\s+(\d+)', text, re.IGNORECASE)
            vmid = m.group(1) if m else ''
            if any(w in text for w in ('fail', 'error')):
                return 'backup_fail', 'vm', vmid
            return 'backup_complete', 'vm', vmid
        
        if 'replication' in text:
            return 'replication_fail', 'vm', ''
        
        # Generic fallback
        return 'system_problem', 'node', ''
    
    # Old _classify removed -- replaced by _classify_pve above.
    
    @staticmethod
    def _map_severity(raw: str) -> str:
        raw_l = str(raw).lower()
        if raw_l in ('critical', 'emergency', 'alert', 'crit', 'err', 'error'):
            return 'CRITICAL'
        if raw_l in ('warning', 'warn', 'notice'):
            return 'WARNING'
        return 'INFO'
