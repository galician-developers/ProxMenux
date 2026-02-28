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
        
        # NOTE: Disk I/O errors (ATA, SCSI, blk_update_request) are NOT handled
        # here. They are detected exclusively by HealthMonitor._check_disks_optimized
        # which records to health_persistence -> PollingCollector -> notification.
        # This avoids duplicate notifications and ensures the health dashboard
        # stays in sync with notifications.
        # Filesystem errors (EXT4/BTRFS/XFS/ZFS) ARE handled here because they
        # indicate corruption, not just hardware I/O problems.
        
        critical_patterns = {
            r'kernel panic':       ('system_problem', 'CRITICAL', 'Kernel panic'),
            r'Out of memory':      ('system_problem', 'CRITICAL', 'Out of memory killer activated'),
            r'segfault':           ('system_problem', 'WARNING',  'Segmentation fault detected'),
            r'BUG:':               ('system_problem', 'CRITICAL', 'Kernel BUG detected'),
            r'Call Trace:':        ('system_problem', 'WARNING',  'Kernel call trace'),
            r'EXT4-fs error':      ('system_problem', 'CRITICAL', 'Filesystem error'),
            r'BTRFS error':        ('system_problem', 'CRITICAL', 'Filesystem error'),
            r'XFS.*error':         ('system_problem', 'CRITICAL', 'Filesystem error'),
            r'ZFS.*error':         ('system_problem', 'CRITICAL', 'ZFS pool error'),
            r'mce:.*Hardware Error': ('system_problem', 'CRITICAL', 'Hardware error (MCE)'),
        }
        
        for pattern, (event_type, severity, reason) in critical_patterns.items():
            if re.search(pattern, msg, re.IGNORECASE):
                entity = 'node'
                entity_id = ''
                
                # Build a context-rich reason from the journal message.
                enriched = reason
                
                if 'segfault' in pattern:
                    m = re.search(r'(\S+)\[(\d+)\].*segfault', msg)
                    proc_name = m.group(1) if m else ''
                    proc_pid = m.group(2) if m else ''
                    lib_match = re.search(r'\bin\s+(\S+)', msg)
                    lib_name = lib_match.group(1) if lib_match else ''
                    
                    # Dedup by process name so repeated segfaults don't spam
                    if proc_name:
                        entity_id = f'segfault_{proc_name}'
                    
                    parts = [reason]
                    if proc_name:
                        parts.append(f"Process: {proc_name}" + (f" (PID {proc_pid})" if proc_pid else ''))
                    if lib_name:
                        parts.append(f"Module: {lib_name}")
                    enriched = '\n'.join(parts)
                
                elif 'Out of memory' in pattern:
                    m = re.search(r'Killed process\s+(\d+)\s+\(([^)]+)\)', msg)
                    if m:
                        enriched = f"{reason}\nKilled: {m.group(2)} (PID {m.group(1)})"
                        entity_id = f'oom_{m.group(2)}'  # Dedup by killed process
                    else:
                        enriched = f"{reason}\n{msg[:300]}"
                
                elif re.search(r'EXT4-fs error|BTRFS error|XFS.*error|ZFS.*error', msg, re.IGNORECASE):
                    # Filesystem errors: extract device, function and human-readable explanation
                    fs_type = 'EXT4'
                    for fs in ['EXT4', 'BTRFS', 'XFS', 'ZFS']:
                        if fs.lower() in msg.lower():
                            fs_type = fs
                            break
                    
                    dev_match = re.search(r'device\s+(\S+?)\)?:', msg)
                    device = dev_match.group(1).rstrip(')') if dev_match else 'unknown'
                    
                    # Dedup by device: all EXT4 errors on sdb1 share ONE notification
                    entity = 'disk'
                    entity_id = f'fs_{device}'
                    
                    # Identify what this device is (model, type, mountpoint)
                    device_info = self._identify_block_device(device)
                    
                    func_match = re.search(r':\s+(\w+:\d+):', msg)
                    func_info = func_match.group(1) if func_match else ''
                    
                    inode_match = re.search(r'inode\s+#?(\d+)', msg)
                    inode = inode_match.group(1) if inode_match else ''
                    
                    parts = [f'{fs_type} filesystem corruption on /dev/{device}']
                    # Add device identification so the user knows what this device is
                    if device_info:
                        parts.append(f'Device: {device_info}')
                    else:
                        parts.append(f'Device: /dev/{device} (not currently detected -- may be a disconnected USB or temporary device)')
                    if func_info:
                        parts.append(f'Error: {self._translate_fs_function(func_info)}')
                    if inode:
                        inode_hint = 'root directory' if inode == '2' else f'inode #{inode}'
                        parts.append(f'Affected: {inode_hint}')
                    parts.append(f'Action: Run "fsck /dev/{device}" (unmount first) or check backup integrity')
                    enriched = '\n'.join(parts)
                
                else:
                    # Generic: include the raw journal message for context
                    enriched = f"{reason}\n{msg[:300]}"
                
                data = {'reason': enriched, 'hostname': self._hostname}
                
                self._emit(event_type, severity, data, entity=entity, entity_id=entity_id)
                return
    
    def _identify_block_device(self, device: str) -> str:
        """
        Identify a block device by querying lsblk.
        Returns a human-readable string like:
          "KINGSTON SA400S37960G (SSD, 894.3G) mounted at /mnt/data"
          "ST8000VN004-3CP101 (HDD, 7.3T) -- not mounted"
        Returns empty string if the device is not found.
        """
        if not device or device == 'unknown':
            return ''
        try:
            # Try the device as-is first, then the base disk (sdb1 -> sdb)
            candidates = [device]
            base = re.sub(r'\d+$', '', device) if not ('nvme' in device or 'mmcblk' in device) else device
            if base != device:
                candidates.append(base)
            
            for dev in candidates:
                dev_path = f'/dev/{dev}' if not dev.startswith('/') else dev
                result = subprocess.run(
                    ['lsblk', '-ndo', 'NAME,MODEL,SIZE,TRAN,MOUNTPOINT,ROTA', dev_path],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0 and result.stdout.strip():
                    fields = result.stdout.strip().split(None, 5)
                    name = fields[0] if len(fields) > 0 else dev
                    model = fields[1] if len(fields) > 1 and fields[1] else 'Unknown model'
                    size = fields[2] if len(fields) > 2 else '?'
                    tran = (fields[3] if len(fields) > 3 else '').upper()  # sata, usb, nvme
                    mountpoint = fields[4] if len(fields) > 4 and fields[4] else ''
                    rota = fields[5].strip() if len(fields) > 5 else '1'
                    
                    # Determine disk type
                    if tran == 'USB':
                        disk_type = 'USB'
                    elif tran == 'NVME' or 'nvme' in name:
                        disk_type = 'NVMe'
                    elif rota == '0':
                        disk_type = 'SSD'
                    else:
                        disk_type = 'HDD'
                    
                    info = f'{model} ({disk_type}, {size})'
                    if mountpoint:
                        info += f' mounted at {mountpoint}'
                    elif dev != device:
                        # Check partition mountpoint
                        part_result = subprocess.run(
                            ['lsblk', '-ndo', 'MOUNTPOINT', f'/dev/{device}'],
                            capture_output=True, text=True, timeout=2
                        )
                        part_mount = part_result.stdout.strip() if part_result.returncode == 0 else ''
                        if part_mount:
                            info += f' partition {device} mounted at {part_mount}'
                        else:
                            info += ' -- not mounted'
                    else:
                        info += ' -- not mounted'
                    
                    return info
            
            return ''
        except Exception:
            return ''
    
    @staticmethod
    def _translate_fs_function(func_info: str) -> str:
        """Translate EXT4/filesystem function names to plain language."""
        func_name = func_info.split(':')[0] if ':' in func_info else func_info
        translations = {
            'ext4_find_entry': 'directory lookup failed (possible directory corruption)',
            'ext4_lookup': 'file lookup failed (possible metadata corruption)',
            'ext4_journal_start': 'journal transaction failed (journal corruption)',
            'ext4_readdir': 'directory read failed (directory data corrupted)',
            'ext4_get_inode_loc': 'inode location failed (inode table corruption)',
            '__ext4_get_inode_loc': 'inode location failed (inode table corruption)',
            'ext4_xattr_get': 'extended attributes read failed',
            'ext4_iget': 'inode read failed (possible inode corruption)',
            'ext4_mb_generate_buddy': 'block allocator error',
            'ext4_validate_block_bitmap': 'block bitmap corrupted',
            'ext4_validate_inode_bitmap': 'inode bitmap corrupted',
            'htree_dirblock_to_tree': 'directory index tree corrupted',
        }
        desc = translations.get(func_name, func_name)
        return desc
    
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
        """
        Detect disk I/O errors from kernel messages.
        
        Cross-references SMART health before notifying:
        - SMART PASSED -> no notification (transient controller event)
        - SMART FAILED/UNKNOWN -> notify with enriched context
        
        Resolves ATA controller names to physical devices and identifies
        the disk model/type/mountpoint for the user.
        """
        if syslog_id != 'kernel' and priority > 3:
            return
        
        io_patterns = [
            r'blk_update_request: I/O error.*dev (\S+)',
            r'Buffer I/O error on device (\S+)',
            r'SCSI error.*sd(\w)',
            r'(ata\d+)[\.\d]*:.*error',
        ]
        
        for pattern in io_patterns:
            match = re.search(pattern, msg)
            if match:
                raw_device = match.group(1) if match.lastindex else 'unknown'
                
                # Resolve ATA port to physical disk name
                if raw_device.startswith('ata'):
                    resolved = self._resolve_ata_to_disk(raw_device)
                else:
                    # Strip partition number (sdb1 -> sdb)
                    resolved = re.sub(r'\d+$', '', raw_device) if raw_device.startswith('sd') else raw_device
                
                # Check SMART health -- if disk is healthy, this is transient noise
                smart_health = self._quick_smart_health(resolved)
                if smart_health == 'PASSED':
                    # SMART says disk is fine, don't notify for transient ATA/SCSI events
                    return
                
                # SMART is FAILED or UNKNOWN -- this may be a real problem
                device_info = self._identify_block_device(resolved)
                
                # Build a clear, informative reason
                parts = []
                if smart_health == 'FAILED':
                    parts.append(f'Disk /dev/{resolved}: I/O errors detected (SMART: FAILED)')
                else:
                    parts.append(f'Disk /dev/{resolved}: I/O errors detected (SMART: unable to verify)')
                
                if device_info:
                    parts.append(f'Device: {device_info}')
                elif resolved.startswith('ata'):
                    parts.append(f'Device: ATA controller {raw_device} (could not resolve to physical disk)')
                else:
                    parts.append(f'Device: /dev/{resolved} (not currently detected -- may be disconnected or temporary)')
                
                # Extract useful detail from the raw kernel message
                detail = self._translate_ata_error(msg)
                if detail:
                    parts.append(f'Detail: {detail}')
                
                parts.append('Action: Check disk health with "smartctl -a /dev/{}" and consider replacement if SMART reports failures'.format(resolved))
                
                enriched = '\n'.join(parts)
                
                dev_display = resolved if resolved.startswith('/dev/') else f'/dev/{resolved}'
                self._emit('disk_io_error', 'CRITICAL', {
                    'device': dev_display,
                    'reason': enriched,
                    'hostname': self._hostname,
                }, entity='disk', entity_id=resolved)
                return
    
    def _resolve_ata_to_disk(self, ata_port: str) -> str:
        """Resolve an ATA port name (ata8) to a physical disk name (sda)."""
        try:
            port_num = re.search(r'ata(\d+)', ata_port)
            if not port_num:
                return ata_port
            num = port_num.group(1)
            # Check /sys/class/ata_port for the mapping
            import glob as _glob
            for path in _glob.glob(f'/sys/class/ata_port/ata{num}/../../host*/target*/*/block/*'):
                disk_name = os.path.basename(path)
                if disk_name.startswith('sd') or disk_name.startswith('nvme'):
                    return disk_name
            # Fallback: try scsi_host mapping
            for path in _glob.glob(f'/sys/class/ata_port/ata{num}/../../host*/scsi_host/host*/../../target*/*/block/*'):
                disk_name = os.path.basename(path)
                if disk_name.startswith('sd'):
                    return disk_name
            return ata_port
        except Exception:
            return ata_port
    
    def _quick_smart_health(self, disk_name: str) -> str:
        """Quick SMART health check. Returns 'PASSED', 'FAILED', or 'UNKNOWN'."""
        if not disk_name or disk_name.startswith('ata') or disk_name.startswith('zram'):
            return 'UNKNOWN'
        try:
            dev_path = f'/dev/{disk_name}' if not disk_name.startswith('/') else disk_name
            result = subprocess.run(
                ['smartctl', '--health', '-j', dev_path],
                capture_output=True, text=True, timeout=5
            )
            import json as _json
            data = _json.loads(result.stdout)
            passed = data.get('smart_status', {}).get('passed', None)
            if passed is True:
                return 'PASSED'
            elif passed is False:
                return 'FAILED'
            return 'UNKNOWN'
        except Exception:
            return 'UNKNOWN'
    
    @staticmethod
    def _translate_ata_error(msg: str) -> str:
        """Translate common ATA/SCSI error codes to human-readable descriptions."""
        error_codes = {
            'IDNF': 'sector address not found (possible bad sector or cable issue)',
            'UNC': 'uncorrectable read error (bad sector)',
            'ABRT': 'command aborted by drive',
            'AMNF': 'address mark not found (surface damage)',
            'TK0NF': 'track 0 not found (drive hardware failure)',
            'BBK': 'bad block detected',
            'ICRC': 'interface CRC error (cable or connector issue)',
            'MC': 'media changed',
            'MCR': 'media change requested',
            'WP': 'write protected',
        }
        
        parts = []
        for code, description in error_codes.items():
            if code in msg:
                parts.append(description)
        
        if parts:
            return '; '.join(parts)
        
        # Try to extract the Emask/SErr/action codes
        emask = re.search(r'Emask\s+(0x[0-9a-f]+)', msg)
        serr = re.search(r'SErr\s+(0x[0-9a-f]+)', msg)
        action = re.search(r'action\s+(0x[0-9a-f]+)', msg)
        
        if emask or serr:
            info = []
            if emask:
                info.append(f'Error mask: {emask.group(1)}')
            if serr:
                info.append(f'SATA error: {serr.group(1)}')
            if action and action.group(1) == '0x0':
                info.append('auto-recovered')
            return ', '.join(info)
        
        return ''
    
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
        """Detect system shutdown/reboot.
        
        Matches multiple systemd signals that indicate the node is going down:
          - "Shutting down."  (systemd PID 1)
          - "System is powering off."  / "System is rebooting."
          - "Reached target Shutdown." / "Reached target Reboot."
          - "Journal stopped"  (very late in shutdown)
          - "The system will reboot now!"  / "The system will power off now!"
        """
        msg_lower = msg.lower()
        
        # Only process systemd / logind messages
        if not any(s in syslog_id for s in ('systemd', 'logind', '')):
            if 'systemd' not in msg_lower:
                return
        
        is_reboot = False
        is_shutdown = False
        
        # Detect reboot signals
        reboot_signals = [
            'system is rebooting',
            'reached target reboot',
            'the system will reboot now',
            'starting reboot',
        ]
        for sig in reboot_signals:
            if sig in msg_lower:
                is_reboot = True
                break
        
        # Detect shutdown/poweroff signals
        if not is_reboot:
            shutdown_signals = [
                'system is powering off',
                'system is halting',
                'shutting down',
                'reached target shutdown',
                'reached target halt',
                'the system will power off now',
                'starting power-off',
                'journal stopped',
                'stopping journal service',
            ]
            for sig in shutdown_signals:
                if sig in msg_lower:
                    is_shutdown = True
                    break
        
        if is_reboot:
            self._emit('system_reboot', 'CRITICAL', {
                'reason': msg[:200],
                'hostname': self._hostname,
            }, entity='node', entity_id='')
        elif is_shutdown:
            self._emit('system_shutdown', 'CRITICAL', {
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
        'vzshutdown': ('ct_shutdown', 'INFO'),
        'vzreboot':   ('ct_restart',  'INFO'),
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
        # Cache for active vzdump detection
        self._vzdump_active_cache: float = 0  # timestamp of last positive check
        self._vzdump_cache_ttl = 5  # cache result for 5s
        # Internal tracking: when we see a vzdump task without an end status,
        # we mark the timestamp. When we see it complete (status=OK/ERROR),
        # we clear it. This supplements the /var/log/pve/tasks/active check
        # to avoid timing gaps.
        self._vzdump_running_since: float = 0  # 0 = no vzdump tracked
        self._vzdump_grace_period = 120  # seconds after vzdump ends to still suppress
        # Track active-file UPIDs we've already seen, to avoid duplicate backup_start
        self._seen_active_upids: set = set()
    
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
        
        # Pre-load active UPIDs so we don't fire backup_start for already-running jobs
        if os.path.exists(self.TASK_ACTIVE):
            try:
                with open(self.TASK_ACTIVE, 'r') as f:
                    for line in f:
                        upid = line.strip().split()[0] if line.strip() else ''
                        if upid:
                            self._seen_active_upids.add(upid)
            except Exception:
                pass
        
        self._thread = threading.Thread(target=self._watch_loop, daemon=True,
                                        name='task-watcher')
        self._thread.start()
    
    def stop(self):
        self._running = False
    
    def _is_vzdump_active(self) -> bool:
        """Check if a vzdump (backup) job is currently running or recently finished.
        
        Two-layer detection:
        1. Internal tracking: TaskWatcher marks vzdump start/end with a grace period
           (covers the case where the VM restart arrives milliseconds after vzdump ends)
        2. /var/log/pve/tasks/active: reads the active task file and verifies PID
        
        This combination eliminates timing gaps that caused false VM notifications.
        """
        now = time.time()
        
        # Layer 1: Internal tracking (most reliable, no file I/O)
        if self._vzdump_running_since > 0:
            elapsed = now - self._vzdump_running_since
            if elapsed < self._vzdump_grace_period:
                return True
            else:
                # Grace period expired -- clear the tracking
                self._vzdump_running_since = 0
        
        # Layer 2: /var/log/pve/tasks/active (catches vzdump started by other nodes or cron)
        # Negative cache: if we recently confirmed NO vzdump, skip the check
        if hasattr(self, '_vzdump_negative_cache') and \
           now - self._vzdump_negative_cache < self._vzdump_cache_ttl:
            return False
        # Positive cache
        if now - self._vzdump_active_cache < self._vzdump_cache_ttl:
            return True
        
        active_file = '/var/log/pve/tasks/active'
        try:
            with open(active_file, 'r') as f:
                for line in f:
                    # UPID format: UPID:node:pid:pstart:starttime:type:id:user:
                    if ':vzdump:' in line:
                        # Verify the PID is still alive
                        parts = line.strip().split(':')
                        if len(parts) >= 3:
                            try:
                                pid = int(parts[2])
                                os.kill(pid, 0)  # Signal 0 = just check existence
                                self._vzdump_active_cache = now
                                return True
                            except (ValueError, ProcessLookupError, PermissionError):
                                pass  # PID not found or not a number -- stale entry
        except (OSError, IOError):
            pass
        
        self._vzdump_negative_cache = now
        return False
    
    TASK_ACTIVE = '/var/log/pve/tasks/active'
    
    def _watch_loop(self):
        """Poll task index for completions AND active file for new starts."""
        while self._running:
            try:
                # 1. Check index for completed tasks
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
                
                # 2. Check active file for newly started tasks (backup start)
                self._check_active_tasks()
                
            except Exception as e:
                print(f"[TaskWatcher] Error reading task log: {e}")
            
            time.sleep(2)  # Check every 2 seconds
    
    def _check_active_tasks(self):
        """Scan /var/log/pve/tasks/active for newly started vzdump tasks.
        
        The 'active' file lists UPIDs of currently running PVE tasks.
        Format: UPID:node:pid:pstart:starttime:type:id:user:
        Example: UPID:amd:0018088D:020C7A6E:69A33A76:vzdump:101:root@pam:
        
        We track seen UPIDs to emit backup_start only once per task,
        and clean up stale entries when they disappear from the file.
        """
        if not os.path.exists(self.TASK_ACTIVE):
            return
        
        try:
            current_upids = set()
            with open(self.TASK_ACTIVE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Active file format: just the UPID per line
                    upid = line.split()[0] if line.split() else line
                    current_upids.add(upid)
                    
                    # Only care about vzdump (backup) tasks
                    if ':vzdump:' not in upid:
                        continue
                    
                    # Already seen this task?
                    if upid in self._seen_active_upids:
                        continue
                    
                    self._seen_active_upids.add(upid)
                    
                    # Parse UPID: UPID:node:pid:pstart:starttime:type:id:user:
                    upid_parts = upid.split(':')
                    # Index:  0    1    2   3      4        5    6  7
                    if len(upid_parts) < 8:
                        continue
                    
                    vmid = upid_parts[6]  # The guest ID being backed up
                    user = upid_parts[7]
                    vmname = self._get_vm_name(vmid) if vmid else ''
                    
                    # Track vzdump internally for VM suppression
                    self._vzdump_running_since = time.time()
                    
                    # Emit backup_start notification
                    guest_label = vmname if vmname else f'ID {vmid}'
                    data = {
                        'vmid': vmid,
                        'vmname': guest_label,
                        'hostname': self._hostname,
                        'user': user,
                        'reason': f'Backup started for {guest_label} ({vmid})',
                        'target_node': '',
                        'size': '',
                        'snapshot_name': '',
                    }
                    
                    self._queue.put(NotificationEvent(
                        'backup_start', 'INFO', data,
                        source='tasks',
                        entity='vm' if vmid.isdigit() and int(vmid) >= 100 else 'ct',
                        entity_id=vmid,
                    ))
            
            # Cleanup: remove UPIDs that are no longer in the active file
            stale = self._seen_active_upids - current_upids
            self._seen_active_upids -= stale
            
        except Exception as e:
            print(f"[TaskWatcher] Error reading active tasks: {e}")
    
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
        
        # Track vzdump (backup) tasks internally for VM suppression.
        # When a vzdump starts (no status yet), mark it.  When it completes
        # (status = OK or ERROR), keep a grace period for the post-backup
        # VM restart that follows shortly after.
        if task_type == 'vzdump':
            if not status:
                # Backup just started -- track it
                self._vzdump_running_since = time.time()
            else:
                # Backup just finished -- start grace period for VM restarts
                self._vzdump_running_since = time.time()  # will expire via grace_period
        
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
        
        # Backup completion/failure and replication events are handled
        # EXCLUSIVELY by the PVE webhook, which delivers richer data (full
        # logs, sizes, durations, filenames).  TaskWatcher skips these to
        # avoid duplicates.
        # NOTE: backup_start is NOT in this set -- PVE's webhook only fires
        # when a backup FINISHES, so TaskWatcher is the only source for
        # the "backup started" notification.
        _WEBHOOK_EXCLUSIVE = {'backup_complete', 'backup_fail',
                              'replication_complete', 'replication_fail'}
        if event_type in _WEBHOOK_EXCLUSIVE:
            return
        
        # Suppress VM/CT start/stop/shutdown while a vzdump is active.
        # These are backup-induced operations (mode=stop), not user actions.
        # Exception: if a VM/CT FAILS to start after backup, that IS important.
        _BACKUP_NOISE = {'vm_start', 'vm_stop', 'vm_shutdown', 'vm_restart',
                         'ct_start', 'ct_stop', 'ct_shutdown', 'ct_restart'}
        if event_type in _BACKUP_NOISE and not is_error:
            if self._is_vzdump_active():
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
            
            # Never notify for INFO/OK severity -- these are informational only
            if severity in ('INFO', 'OK'):
                continue
            
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
