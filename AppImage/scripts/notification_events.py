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
import socket
import subprocess
import threading
from queue import Queue
from typing import Optional, Dict, Any
from pathlib import Path


# ─── Event Object ─────────────────────────────────────────────────

class NotificationEvent:
    """Represents a detected event ready for notification dispatch."""
    
    __slots__ = ('event_type', 'severity', 'data', 'timestamp', 'source')
    
    def __init__(self, event_type: str, severity: str = 'INFO',
                 data: Optional[Dict[str, Any]] = None,
                 source: str = 'watcher'):
        self.event_type = event_type
        self.severity = severity
        self.data = data or {}
        self.timestamp = time.time()
        self.source = source
    
    def __repr__(self):
        return f"NotificationEvent({self.event_type}, {self.severity})"


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
                })
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
            })
    
    def _check_kernel_critical(self, msg: str, syslog_id: str, priority: int):
        """Detect kernel panics, OOM, segfaults, hardware errors."""
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
                data = {'reason': reason, 'hostname': self._hostname}
                
                # Try to extract device for disk errors
                dev_match = re.search(r'dev\s+(\S+)', msg)
                if dev_match and event_type == 'disk_io_error':
                    data['device'] = dev_match.group(1)
                
                self._emit(event_type, severity, data)
                return
    
    def _check_service_failure(self, msg: str, unit: str):
        """Detect critical service failures."""
        service_patterns = [
            r'Failed to start (.+)',
            r'Unit (\S+) (?:entered failed state|failed)',
            r'(\S+)\.service: (?:Main process exited|Failed with result)',
        ]
        
        for pattern in service_patterns:
            match = re.search(pattern, msg)
            if match:
                service_name = match.group(1)
                self._emit('service_fail', 'WARNING', {
                    'service_name': service_name,
                    'reason': msg[:200],
                    'hostname': self._hostname,
                })
                return
    
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
                })
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
            })
            return
        
        # Node disconnect
        if (('quorum' in msg_lower and 'lost' in msg_lower) or
            ('node' in msg_lower and any(w in msg_lower for w in ['left', 'offline', 'lost']))):
            
            node_match = re.search(r'[Nn]ode\s+(\S+)', msg)
            node_name = node_match.group(1) if node_match else 'unknown'
            
            self._emit('node_disconnect', 'CRITICAL', {
                'node_name': node_name,
                'hostname': self._hostname,
            })
    
    def _check_system_shutdown(self, msg: str, syslog_id: str):
        """Detect system shutdown/reboot."""
        if 'systemd-journald' in syslog_id or 'systemd' in syslog_id:
            if 'Journal stopped' in msg or 'Stopping Journal Service' in msg:
                self._emit('system_shutdown', 'WARNING', {
                    'reason': 'System journal stopped',
                    'hostname': self._hostname,
                })
            elif 'Shutting down' in msg or 'System is rebooting' in msg:
                event = 'system_reboot' if 'reboot' in msg.lower() else 'system_shutdown'
                self._emit(event, 'WARNING', {
                    'reason': msg[:200],
                    'hostname': self._hostname,
                })
    
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
                })
                return
    
    def _check_firewall(self, msg: str, syslog_id: str):
        """Detect firewall issues (not individual drops, but rule errors)."""
        if re.search(r'pve-firewall.*(?:error|failed|unable)', msg, re.IGNORECASE):
            self._emit('firewall_issue', 'WARNING', {
                'reason': msg[:200],
                'hostname': self._hostname,
            })
    
    # ── Emit helper ──
    
    def _emit(self, event_type: str, severity: str, data: Dict):
        """Emit event to queue with deduplication."""
        dedup_key = f"{event_type}:{data.get('source_ip', '')}:{data.get('device', '')}:{data.get('service_name', '')}"
        
        now = time.time()
        last = self._recent_events.get(dedup_key, 0)
        if now - last < self._dedup_window:
            return  # Skip duplicate
        
        self._recent_events[dedup_key] = now
        
        # Cleanup old dedup entries periodically
        if len(self._recent_events) > 200:
            cutoff = now - self._dedup_window * 2
            self._recent_events = {
                k: v for k, v in self._recent_events.items() if v > cutoff
            }
        
        self._queue.put(NotificationEvent(event_type, severity, data, source='journal'))


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
        
        self._queue.put(NotificationEvent(event_type, severity, data, source='task'))
    
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
    """Periodic collector that reads Health Monitor pending notifications.
    
    Polls health_persistence for:
    - Pending notification events (state changes from Bloque A)
    - Unnotified errors
    - Update availability (every 24h)
    """
    
    def __init__(self, event_queue: Queue, poll_interval: int = 30):
        self._queue = event_queue
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = poll_interval
        self._hostname = _hostname()
        self._last_update_check = 0
        self._update_check_interval = 86400  # 24 hours
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name='polling-collector')
        self._thread.start()
    
    def stop(self):
        self._running = False
    
    def _poll_loop(self):
        """Main polling loop."""
        # Initial delay to let health monitor warm up
        for _ in range(10):
            if not self._running:
                return
            time.sleep(1)
        
        while self._running:
            try:
                self._collect_health_events()
                self._check_updates()
            except Exception as e:
                print(f"[PollingCollector] Error: {e}")
            
            # Sleep in small increments for responsive shutdown
            for _ in range(self._poll_interval):
                if not self._running:
                    return
                time.sleep(1)
    
    def _collect_health_events(self):
        """Collect pending notification events from health_persistence."""
        try:
            from health_persistence import health_persistence
            
            # Get pending notification events
            events = health_persistence.get_pending_notifications()
            for evt in events:
                data = json.loads(evt.get('data', '{}')) if isinstance(evt.get('data'), str) else evt.get('data', {})
                
                event_type = evt.get('event_type', 'state_change')
                severity = data.get('severity', 'WARNING')
                
                data['hostname'] = self._hostname
                data['error_key'] = evt.get('error_key', '')
                
                self._queue.put(NotificationEvent(
                    event_type, severity, data, source='health_monitor'
                ))
            
            # Mark events as notified
            if events:
                event_ids = [e['id'] for e in events if 'id' in e]
                if event_ids:
                    health_persistence.mark_events_notified(event_ids)
            
            # Also check unnotified errors
            unnotified = health_persistence.get_unnotified_errors()
            for error in unnotified:
                self._queue.put(NotificationEvent(
                    'new_error', error.get('severity', 'WARNING'), {
                        'category': error.get('category', ''),
                        'reason': error.get('reason', ''),
                        'hostname': self._hostname,
                        'error_key': error.get('error_key', ''),
                    },
                    source='health_monitor'
                ))
                # Mark as notified
                if 'id' in error:
                    health_persistence.mark_notified(error['id'])
                    
        except ImportError:
            pass  # health_persistence not available (CLI mode)
        except Exception as e:
            print(f"[PollingCollector] Health event collection error: {e}")
    
    def _check_updates(self):
        """Check for available system updates (every 24h)."""
        now = time.time()
        if now - self._last_update_check < self._update_check_interval:
            return
        
        self._last_update_check = now
        
        try:
            result = subprocess.run(
                ['apt-get', '-s', 'upgrade'],
                capture_output=True, text=True, timeout=60
            )
            
            if result.returncode == 0:
                # Count upgradeable packages
                lines = [l for l in result.stdout.split('\n')
                         if l.startswith('Inst ')]
                count = len(lines)
                
                if count > 0:
                    # Show first 5 package names
                    packages = [l.split()[1] for l in lines[:5]]
                    details = ', '.join(packages)
                    if count > 5:
                        details += f', ... and {count - 5} more'
                    
                    self._queue.put(NotificationEvent(
                        'update_available', 'INFO', {
                            'count': str(count),
                            'details': details,
                            'hostname': self._hostname,
                        },
                        source='polling'
                    ))
        except Exception:
            pass  # Non-critical, silently skip
