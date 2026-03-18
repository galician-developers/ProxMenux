"""
ProxMenux Notification Templates
Message templates for all event types with per-channel formatting.

Templates use Python str.format() variables:
  {hostname}, {severity}, {category}, {reason}, {summary},
  {previous}, {current}, {vmid}, {vmname}, {timestamp}, etc.

Optional AI enhancement enriches messages with context/suggestions.

Author: MacRimi
"""

import json
import re
import socket
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List


# ─── vzdump message parser ───────────────────────────────────────

def _parse_vzdump_message(message: str) -> Optional[Dict[str, Any]]:
    """Parse a PVE vzdump notification message into structured data.
    
    Supports two formats:
    1. Local storage: table with columns VMID Name Status Time Size Filename
    2. PBS storage: log-style output with 'Finished Backup of VM NNN (HH:MM:SS)'
       and sizes in lines like 'root.pxar: had to backup X of Y' or 'transferred X'
    
    Returns dict with 'vms' list, 'total_time', 'total_size', or None.
    """
    if not message:
        return None
    
    vms: List[Dict[str, str]] = []
    total_time = ''
    total_size = ''
    
    lines = message.split('\n')
    
    # ── Strategy 1: classic table (local/NFS/CIFS storage) ──
    header_idx = -1
    for i, line in enumerate(lines):
        if re.match(r'\s*VMID\s+Name\s+Status', line, re.IGNORECASE):
            header_idx = i
            break
    
    if header_idx >= 0:
        # Use column positions from the header to slice each row.
        # Header: "VMID    Name           Status    Time      Size          Filename"
        header = lines[header_idx]
        col_starts = []
        for col_name in ['VMID', 'Name', 'Status', 'Time', 'Size', 'Filename']:
            idx = header.find(col_name)
            if idx >= 0:
                col_starts.append(idx)
        
        if len(col_starts) == 6:
            for line in lines[header_idx + 1:]:
                stripped = line.strip()
                if not stripped or stripped.startswith('Total') or stripped.startswith('Logs') or stripped.startswith('='):
                    break
                # Pad line to avoid index errors
                padded = line.ljust(col_starts[-1] + 50)
                vmid = padded[col_starts[0]:col_starts[1]].strip()
                name = padded[col_starts[1]:col_starts[2]].strip()
                status = padded[col_starts[2]:col_starts[3]].strip()
                time_val = padded[col_starts[3]:col_starts[4]].strip()
                size = padded[col_starts[4]:col_starts[5]].strip()
                filename = padded[col_starts[5]:].strip()
                
                if vmid and vmid.isdigit():
                    # Infer type from filename (vzdump-lxc-NNN or vzdump-qemu-NNN)
                    vm_type = ''
                    if 'lxc' in filename:
                        vm_type = 'lxc'
                    elif 'qemu' in filename:
                        vm_type = 'qemu'
                    vms.append({
                        'vmid': vmid,
                        'name': name,
                        'status': status,
                        'time': time_val,
                        'size': size,
                        'filename': filename,
                        'type': vm_type,
                    })
    
    # ── Strategy 2: log-style (PBS / Proxmox Backup Server) ──
    # Parse from the full vzdump log lines.
    # Look for patterns:
    #   "Starting Backup of VM NNN (lxc/qemu)"  -> detect guest
    #   "CT Name: xxx" or "VM Name: xxx"         -> guest name
    #   "Finished Backup of VM NNN (HH:MM:SS)"   -> duration + status=ok
    #   "root.pxar: had to backup X of Y"         -> size (CT)
    #   "transferred X in N seconds"              -> size (QEMU)
    #   "creating ... archive 'ct/100/2026-..'"   -> archive name for PBS
    #   "TASK ERROR:" or "ERROR:"                 -> status=error
    if not vms:
        current_vm: Optional[Dict[str, str]] = None
        
        for line in lines:
            # Remove "INFO: " prefix that PVE adds
            clean = re.sub(r'^(?:INFO|WARNING|ERROR):\s*', '', line.strip())
            
            # Start of a new VM backup
            m_start = re.match(
                r'Starting Backup of VM (\d+)\s+\((lxc|qemu)\)', clean)
            if m_start:
                if current_vm:
                    vms.append(current_vm)
                current_vm = {
                    'vmid': m_start.group(1),
                    'name': '',
                    'status': 'ok',
                    'time': '',
                    'size': '',
                    'filename': '',
                    'type': m_start.group(2),
                }
                continue
            
            if current_vm:
                # Guest name
                m_name = re.match(r'(?:CT|VM) Name:\s*(.+)', clean)
                if m_name:
                    current_vm['name'] = m_name.group(1).strip()
                    continue
                
                # PBS archive path -> extract as filename
                m_archive = re.search(
                    r"creating .+ archive '([^']+)'", clean)
                if m_archive:
                    current_vm['filename'] = m_archive.group(1)
                    continue
                
                # Size for containers (pxar)
                m_pxar = re.search(
                    r'root\.pxar:.*?of\s+([\d.]+\s+\S+)', clean)
                if m_pxar:
                    current_vm['size'] = m_pxar.group(1)
                    continue
                
                # Size for QEMU (transferred)
                m_transfer = re.search(
                    r'transferred\s+([\d.]+\s+\S+)', clean)
                if m_transfer:
                    current_vm['size'] = m_transfer.group(1)
                    continue
                
                # Finished -> duration
                m_finish = re.match(
                    r'Finished Backup of VM (\d+)\s+\(([^)]+)\)', clean)
                if m_finish:
                    current_vm['time'] = m_finish.group(2)
                    current_vm['status'] = 'ok'
                    vms.append(current_vm)
                    current_vm = None
                    continue
                
                # Error
                if clean.startswith('ERROR:') or clean.startswith('TASK ERROR'):
                    if current_vm:
                        current_vm['status'] = 'error'
        
        # Don't forget the last VM if it wasn't finished
        if current_vm:
            vms.append(current_vm)
    
    # ── Extract totals ──
    for line in lines:
        m_time = re.search(r'Total running time:\s*(.+)', line)
        if m_time:
            total_time = m_time.group(1).strip()
        m_size = re.search(r'Total size:\s*(.+)', line)
        if m_size:
            total_size = m_size.group(1).strip()
    
    # For PBS: calculate total size if not explicitly stated
    if not total_size and vms:
        # Sum individual sizes if they share units
        sizes_gib = 0.0
        for vm in vms:
            s = vm.get('size', '')
            m = re.match(r'([\d.]+)\s+(.*)', s)
            if m:
                val = float(m.group(1))
                unit = m.group(2).strip().upper()
                if 'GIB' in unit or 'GB' in unit:
                    sizes_gib += val
                elif 'MIB' in unit or 'MB' in unit:
                    sizes_gib += val / 1024
                elif 'TIB' in unit or 'TB' in unit:
                    sizes_gib += val * 1024
        if sizes_gib > 0:
            if sizes_gib >= 1024:
                total_size = f"{sizes_gib / 1024:.3f} TiB"
            elif sizes_gib >= 1:
                total_size = f"{sizes_gib:.3f} GiB"
            else:
                total_size = f"{sizes_gib * 1024:.3f} MiB"
    
    # For PBS: calculate total time if not stated
    if not total_time and vms:
        total_secs = 0
        for vm in vms:
            t = vm.get('time', '')
            # Parse HH:MM:SS format
            m = re.match(r'(\d+):(\d+):(\d+)', t)
            if m:
                total_secs += int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if total_secs > 0:
            hours = total_secs // 3600
            mins = (total_secs % 3600) // 60
            secs = total_secs % 60
            if hours:
                total_time = f"{hours}h {mins}m {secs}s"
            elif mins:
                total_time = f"{mins}m {secs}s"
            else:
                total_time = f"{secs}s"
    
    if not vms and not total_size:
        return None
    
    return {
        'vms': vms,
        'total_time': total_time,
        'total_size': total_size,
        'vm_count': len(vms),
    }


def _format_vzdump_body(parsed: Dict[str, Any], is_success: bool) -> str:
    """Format parsed vzdump data into a clean Telegram-friendly message."""
    parts = []
    
    for vm in parsed.get('vms', []):
        status = vm.get('status', '').lower()
        icon = '\u2705' if status == 'ok' else '\u274C'
        
        # Determine VM/CT type prefix
        vm_type = vm.get('type', '')
        if vm_type == 'lxc':
            prefix = 'CT'
        elif vm_type == 'qemu':
            prefix = 'VM'
        else:
            # Try to infer from filename (vzdump-lxc-NNN or vzdump-qemu-NNN)
            fname = vm.get('filename', '')
            if 'lxc' in fname or fname.startswith('ct/'):
                prefix = 'CT'
            elif 'qemu' in fname or fname.startswith('vm/'):
                prefix = 'VM'
            else:
                prefix = ''
        
        # Format: "VM Name (ID)" or "CT Name (ID)" -- name first
        name = vm.get('name', '')
        vmid = vm.get('vmid', '')
        if prefix and name:
            parts.append(f"{icon} {prefix} {name} ({vmid})")
        elif name:
            parts.append(f"{icon} {name} ({vmid})")
        else:
            parts.append(f"{icon} ID {vmid}")
        
        # Size and Duration on same line with icons
        detail_line = []
        if vm.get('size'):
            detail_line.append(f"\U0001F4CF Size: {vm['size']}")
        if vm.get('time'):
            detail_line.append(f"\u23F1\uFE0F Duration: {vm['time']}")
        if detail_line:
            parts.append(' | '.join(detail_line))
        
        # PBS/File on separate line with icon
        if vm.get('filename'):
            fname = vm['filename']
            if re.match(r'^(?:ct|vm)/\d+/', fname):
                parts.append(f"\U0001F5C4\uFE0F PBS: {fname}")
            else:
                parts.append(f"\U0001F4C1 File: {fname}")
        
        # Error reason if failed
        if status != 'ok' and vm.get('error'):
            parts.append(f"\u26A0\uFE0F {vm['error']}")
        
        parts.append('')  # blank line between VMs
    
    # Summary line with icons
    vm_count = parsed.get('vm_count', 0)
    if vm_count > 0 or parsed.get('total_size'):
        ok_count = sum(1 for v in parsed.get('vms', [])
                       if v.get('status', '').lower() == 'ok')
        fail_count = vm_count - ok_count
        
        summary_parts = []
        if vm_count:
            summary_parts.append(f"\U0001F4CA {vm_count} backups")
        if fail_count:
            summary_parts.append(f"\u274C {fail_count} failed")
        if parsed.get('total_size'):
            summary_parts.append(f"\U0001F4E6 Total: {parsed['total_size']}")
        if parsed.get('total_time'):
            summary_parts.append(f"\u23F1\uFE0F Time: {parsed['total_time']}")
        
        if summary_parts:
            parts.append(' | '.join(summary_parts))
    
    return '\n'.join(parts)


# ─── Severity Icons ──────────────────────────────────────────────

SEVERITY_ICONS = {
    'CRITICAL': '\U0001F534',
    'WARNING':  '\U0001F7E1',
    'INFO':     '\U0001F535',
    'OK':       '\U0001F7E2',
    'UNKNOWN':  '\u26AA',
}

SEVERITY_ICONS_DISCORD = {
    'CRITICAL': ':red_circle:',
    'WARNING':  ':yellow_circle:',
    'INFO':     ':blue_circle:',
    'OK':       ':green_circle:',
    'UNKNOWN':  ':white_circle:',
}


# ─── Event Templates ─────────────────────────────────────────────
# Each template has a 'title' and 'body' with {variable} placeholders.
# 'group' is used for UI event filter grouping.
# 'default_enabled' controls initial state in settings.

TEMPLATES = {
    # ── Health Monitor state changes ──
    # NOTE: state_change is disabled by default -- it fires on every
    # status oscillation (OK->WARNING->OK) which creates noise.
    # The health_persistent and new_error templates cover this better.
    'state_change': {
        'title': '{hostname}: {category} changed to {current}',
        'body': '{category} status changed from {previous} to {current}.\n{reason}',
        'label': 'Health state changed',
        'group': 'health',
        'default_enabled': False,
    },
    'new_error': {
        'title': '{hostname}: New {severity} - {category}',
        'body': '{reason}',
        'label': 'New health issue',
        'group': 'health',
        'default_enabled': True,
    },
    'error_resolved': {
        'title': '{hostname}: Resolved - {category}',
        'body': 'The {category} issue has been resolved.\n{reason}\n\U0001F6A6 Previous severity: {original_severity}\n\u23F1\uFE0F Duration: {duration}',
        'label': 'Recovery notification',
        'group': 'health',
        'default_enabled': True,
    },
    'error_escalated': {
        'title': '{hostname}: Escalated to {severity} - {category}',
        'body': '{reason}',
        'label': 'Health issue escalated',
        'group': 'health',
        'default_enabled': True,
    },
    'health_degraded': {
        'title': '{hostname}: Health check degraded',
        'body': '{reason}',
        'label': 'Health check degraded',
        'group': 'health',
        'default_enabled': True,
    },
    
    # ── VM / CT events ──
    'vm_start': {
        'title': '{hostname}: VM {vmname} ({vmid}) started',
        'body': 'Virtual machine {vmname} (ID: {vmid}) is now running.',
        'label': 'VM started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_stop': {
        'title': '{hostname}: VM {vmname} ({vmid}) stopped',
        'body': 'Virtual machine {vmname} (ID: {vmid}) has been stopped.',
        'label': 'VM stopped',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_shutdown': {
        'title': '{hostname}: VM {vmname} ({vmid}) shut down',
        'body': 'Virtual machine {vmname} (ID: {vmid}) has been cleanly shut down.',
        'label': 'VM shutdown',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_fail': {
        'title': '{hostname}: VM {vmname} ({vmid}) FAILED',
        'body': 'Virtual machine {vmname} (ID: {vmid}) has crashed or failed to start.\nReason: {reason}',
        'label': 'VM FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_restart': {
        'title': '{hostname}: VM {vmname} ({vmid}) restarted',
        'body': 'Virtual machine {vmname} (ID: {vmid}) has been restarted.',
        'label': 'VM restarted',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_start': {
        'title': '{hostname}: CT {vmname} ({vmid}) started',
        'body': 'Container {vmname} (ID: {vmid}) is now running.',
        'label': 'CT started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'ct_stop': {
        'title': '{hostname}: CT {vmname} ({vmid}) stopped',
        'body': 'Container {vmname} (ID: {vmid}) has been stopped.',
        'label': 'CT stopped',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_shutdown': {
        'title': '{hostname}: CT {vmname} ({vmid}) shut down',
        'body': 'Container {vmname} (ID: {vmid}) has been cleanly shut down.',
        'label': 'CT shutdown',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_restart': {
        'title': '{hostname}: CT {vmname} ({vmid}) restarted',
        'body': 'Container {vmname} (ID: {vmid}) has been restarted.',
        'label': 'CT restarted',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_fail': {
        'title': '{hostname}: CT {vmname} ({vmid}) FAILED',
        'body': 'Container {vmname} (ID: {vmid}) has crashed or failed to start.\nReason: {reason}',
        'label': 'CT FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_start': {
        'title': '{hostname}: Migration started — {vmname} ({vmid})',
        'body': 'Live migration of {vmname} (ID: {vmid}) to node {target_node} has started.',
        'label': 'Migration started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_complete': {
        'title': '{hostname}: Migration complete — {vmname} ({vmid})',
        'body': '{vmname} (ID: {vmid}) successfully migrated to node {target_node}.',
        'label': 'Migration complete',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_fail': {
        'title': '{hostname}: Migration FAILED — {vmname} ({vmid})',
        'body': 'Migration of {vmname} (ID: {vmid}) to node {target_node} failed.\nReason: {reason}',
        'label': 'Migration FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'replication_fail': {
        'title': '{hostname}: Replication FAILED — {vmname} ({vmid})',
        'body': 'Replication of {vmname} (ID: {vmid}) failed.\nReason: {reason}',
        'label': 'Replication FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'replication_complete': {
        'title': '{hostname}: Replication complete — {vmname} ({vmid})',
        'body': 'Replication of {vmname} (ID: {vmid}) completed successfully.',
        'label': 'Replication complete',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    
    # ── Backup / Snapshot events ──
    'backup_start': {
        'title': '{hostname}: Backup started on {storage}',
        'body': 'Backup job started on storage {storage}.\n{reason}',
        'label': 'Backup started',
        'group': 'backup',
        'default_enabled': False,
    },
    'backup_complete': {
        'title': '{hostname}: Backup complete — {vmname} ({vmid})',
        'body': 'Backup of {vmname} (ID: {vmid}) completed successfully.\nSize: {size}',
        'label': 'Backup complete',
        'group': 'backup',
        'default_enabled': True,
    },
    'backup_fail': {
        'title': '{hostname}: Backup FAILED — {vmname} ({vmid})',
        'body': 'Backup of {vmname} (ID: {vmid}) failed.\nReason: {reason}',
        'label': 'Backup FAILED',
        'group': 'backup',
        'default_enabled': True,
    },
    'snapshot_complete': {
        'title': '{hostname}: Snapshot created — {vmname} ({vmid})',
        'body': 'Snapshot "{snapshot_name}" created for {vmname} (ID: {vmid}).',
        'label': 'Snapshot created',
        'group': 'backup',
        'default_enabled': False,
    },
    'snapshot_fail': {
        'title': '{hostname}: Snapshot FAILED — {vmname} ({vmid})',
        'body': 'Snapshot creation for {vmname} (ID: {vmid}) failed.\nReason: {reason}',
        'label': 'Snapshot FAILED',
        'group': 'backup',
        'default_enabled': True,
    },
    
    # ── Resource events (from Health Monitor) ──
    'cpu_high': {
        'title': '{hostname}: High CPU usage — {value}%',
        'body': 'CPU usage has reached {value}% on {cores} cores.\n{details}',
        'label': 'High CPU usage',
        'group': 'resources',
        'default_enabled': True,
    },
    'ram_high': {
        'title': '{hostname}: High memory usage — {value}%',
        'body': 'Memory usage: {used} / {total} ({value}%).\n{details}',
        'label': 'High memory usage',
        'group': 'resources',
        'default_enabled': True,
    },
    'temp_high': {
        'title': '{hostname}: High CPU temperature — {value}°C',
        'body': 'CPU temperature has reached {value}°C (threshold: {threshold}°C).\n{details}',
        'label': 'High temperature',
        'group': 'resources',
        'default_enabled': True,
    },
    'disk_space_low': {
        'title': '{hostname}: Low disk space on {mount}',
        'body': 'Filesystem {mount}: {used}% used ({available} available).\nFree up disk space to avoid service disruption.',
        'label': 'Low disk space',
        'group': 'storage',
        'default_enabled': True,
    },
    'disk_io_error': {
        'title': '{hostname}: Disk failure detected — {device}',
        'body': 'I/O error or disk failure detected on device {device}.\n{reason}',
        'label': 'Disk failure / I/O error',
        'group': 'storage',
        'default_enabled': True,
    },
    'storage_unavailable': {
        'title': '{hostname}: Storage unavailable — {storage_name}',
        'body': 'PVE storage "{storage_name}" (type: {storage_type}) is not accessible.\nReason: {reason}',
        'label': 'Storage unavailable',
        'group': 'storage',
        'default_enabled': True,
    },
    'load_high': {
        'title': '{hostname}: High system load — {value}',
        'body': 'System load average is {value} on {cores} cores.\n{details}',
        'label': 'High system load',
        'group': 'resources',
        'default_enabled': True,
    },
    
    # ── Network events ──
    'network_down': {
        'title': '{hostname}: Network connectivity lost',
        'body': 'The node has lost network connectivity.\nReason: {reason}',
        'label': 'Network connectivity lost',
        'group': 'network',
        'default_enabled': True,
    },
    'network_latency': {
        'title': '{hostname}: High network latency — {value}ms',
        'body': 'Latency to gateway: {value}ms (threshold: {threshold}ms).\nThis may indicate network congestion or hardware issues.',
        'label': 'High network latency',
        'group': 'network',
        'default_enabled': False,
    },
    
    # ── Security events ──
    'auth_fail': {
        'title': '{hostname}: Authentication failure',
        'body': 'Failed login attempt detected.\nSource IP: {source_ip}\nUser: {username}\nService: {service}',
        'label': 'Authentication failure',
        'group': 'security',
        'default_enabled': True,
    },
    'ip_block': {
        'title': '{hostname}: IP blocked by Fail2Ban',
        'body': 'IP address {source_ip} has been banned.\nJail: {jail}\nFailed attempts: {failures}',
        'label': 'IP blocked by Fail2Ban',
        'group': 'security',
        'default_enabled': True,
    },
    'firewall_issue': {
        'title': '{hostname}: Firewall issue detected',
        'body': 'A firewall configuration issue has been detected.\nReason: {reason}',
        'label': 'Firewall issue detected',
        'group': 'security',
        'default_enabled': True,
    },
    'user_permission_change': {
        'title': '{hostname}: User permission changed',
        'body': 'User: {username}\nChange: {change_details}',
        'label': 'User permission changed',
        'group': 'security',
        'default_enabled': True,
    },
    
    # ── Cluster events ──
    'split_brain': {
        'title': '{hostname}: SPLIT-BRAIN detected',
        'body': 'A cluster split-brain condition has been detected. Quorum may be lost.\nQuorum status: {quorum}',
        'label': 'SPLIT-BRAIN detected',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_disconnect': {
        'title': '{hostname}: Node {node_name} disconnected',
        'body': 'Node {node_name} has disconnected from the cluster.',
        'label': 'Node disconnected',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_reconnect': {
        'title': '{hostname}: Node {node_name} reconnected',
        'body': 'Node {node_name} has rejoined the cluster successfully.',
        'label': 'Node reconnected',
        'group': 'cluster',
        'default_enabled': True,
    },
    
    # ── Services events ──
    'system_shutdown': {
        'title': '{hostname}: System shutting down',
        'body': 'The node is shutting down.\n{reason}',
        'label': 'System shutting down',
        'group': 'services',
        'default_enabled': True,
    },
    'system_reboot': {
        'title': '{hostname}: System rebooting',
        'body': 'The node is rebooting.\n{reason}',
        'label': 'System rebooting',
        'group': 'services',
        'default_enabled': True,
    },
    'system_problem': {
        'title': '{hostname}: System problem detected',
        'body': 'A system-level problem has been detected.\nReason: {reason}',
        'label': 'System problem detected',
        'group': 'services',
        'default_enabled': True,
    },
    'service_fail': {
        'title': '{hostname}: Service failed — {service_name}',
        'body': 'System service "{service_name}" has failed.\nReason: {reason}',
        'label': 'Service failed',
        'group': 'services',
        'default_enabled': True,
    },
    'oom_kill': {
        'title': '{hostname}: OOM Kill — {process}',
        'body': 'Process "{process}" was killed by the Out-of-Memory manager.\n{reason}',
        'label': 'Out of memory kill',
        'group': 'services',
        'default_enabled': True,
    },
    
    # ── Hidden internal templates (not shown in UI) ──
    'service_fail_batch': {
        'title': '{hostname}: {service_count} services failed',
        'body': '{reason}',
        'label': 'Service fail batch',
        'group': 'services',
        'default_enabled': True,
        'hidden': True,
    },
    'system_mail': {
        'title': '{hostname}: {pve_title}',
        'body': '{reason}',
        'label': 'PVE system mail',
        'group': 'other',
        'default_enabled': True,
        'hidden': True,
    },
    'webhook_test': {
        'title': '{hostname}: Webhook test received',
        'body': 'PVE webhook connectivity test successful.\n{reason}',
        'label': 'Webhook test',
        'group': 'other',
        'default_enabled': True,
        'hidden': True,
    },
    'update_available': {
        'title': '{hostname}: Updates available',
        'body': 'Total updates: {total_count}\nSecurity: {security_count}\nProxmox: {pve_count}\nKernel: {kernel_count}\nImportant packages:\n{important_list}',
        'label': 'Updates available (legacy)',
        'group': 'updates',
        'default_enabled': False,
        'hidden': True,
    },
    'unknown_persistent': {
        'title': '{hostname}: Check unavailable - {category}',
        'body': 'Health check for {category} has been unavailable for 3+ cycles.\n{reason}',
        'label': 'Check unavailable',
        'group': 'health',
        'default_enabled': False,
        'hidden': True,
    },
    
    # ── Health Monitor events ──
    'health_persistent': {
        'title': '{hostname}: {count} active health issue(s)',
        'body': 'The following health issues remain unresolved:\n{issue_list}\n\nThis digest is sent once every 24 hours while issues persist.',
        'label': 'Active health issues (daily)',
        'group': 'health',
        'default_enabled': True,
    },
    'health_issue_new': {
        'title': '{hostname}: New health issue — {category}',
        'body': 'New {severity} issue detected in: {category}\nDetails: {reason}',
        'label': 'New health issue',
        'group': 'health',
        'default_enabled': True,
    },
    'health_issue_resolved': {
        'title': '{hostname}: Resolved - {category}',
        'body': '{category} issue has been resolved.\n{reason}\nDuration: {duration}',
        'label': 'Health issue resolved',
        'group': 'health',
        'default_enabled': True,
        'hidden': True,  # Use error_resolved instead (avoids duplicate in UI)
    },
    
    # ── Update notifications ──
    'update_summary': {
        'title': '{hostname}: Updates available',
        'body': (
            'Total updates: {total_count}\n'
            'Security updates: {security_count}\n'
            'Proxmox-related updates: {pve_count}\n'
            'Kernel updates: {kernel_count}\n'
            'Important packages:\n{important_list}'
        ),
        'label': 'Updates available',
        'group': 'updates',
        'default_enabled': True,
    },
    'pve_update': {
        'title': '{hostname}: Proxmox VE {new_version} available',
        'body': 'A new Proxmox VE release is available.\nCurrent: {current_version}\nNew: {new_version}\n{details}',
        'label': 'Proxmox VE update available',
        'group': 'updates',
        'default_enabled': True,
    },
    'update_complete': {
        'title': '{hostname}: System update completed',
        'body': 'System packages have been successfully updated.\n{details}',
        'label': 'Update completed',
        'group': 'updates',
        'default_enabled': False,
    },
    
    # ── Burst aggregation summaries (hidden -- auto-generated by BurstAggregator) ──
    # These inherit enabled state from their parent event type at dispatch time.
    'burst_auth_fail': {
        'title': '{hostname}: {count} auth failures in {window}',
        'body': '{count} authentication failures detected in {window}.\nSources: {entity_list}',
        'label': 'Auth failures burst',
        'group': 'security',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_ip_block': {
        'title': '{hostname}: Fail2Ban banned {count} IPs in {window}',
        'body': '{count} IPs banned by Fail2Ban in {window}.\nIPs: {entity_list}',
        'label': 'IP block burst',
        'group': 'security',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_disk_io': {
        'title': '{hostname}: {count} disk I/O errors on {entity_list}',
        'body': '{count} I/O errors detected in {window}.\nDevices: {entity_list}',
        'label': 'Disk I/O burst',
        'group': 'storage',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_cluster': {
        'title': '{hostname}: Cluster flapping detected ({count} changes)',
        'body': 'Cluster state changed {count} times in {window}.\nNodes: {entity_list}',
        'label': 'Cluster flapping burst',
        'group': 'cluster',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_service_fail': {
        'title': '{hostname}: {count} services failed in {window}',
        'body': '{count} service failures detected in {window}.\nThis typically indicates a node reboot or PVE service restart.\n\nAdditional failures:\n{details}',
        'label': 'Service fail burst',
        'group': 'services',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_system': {
        'title': '{hostname}: {count} system problems in {window}',
        'body': '{count} system problems detected in {window}.\n\nAdditional issues:\n{details}',
        'label': 'System problems burst',
        'group': 'services',
        'default_enabled': True,
        'hidden': True,
    },
    'burst_generic': {
        'title': '{hostname}: {count} {event_type} events in {window}',
        'body': '{count} events of type {event_type} in {window}.\n\nAdditional events:\n{details}',
        'label': 'Generic burst',
        'group': 'other',
        'default_enabled': True,
        'hidden': True,
    },
}

# ─── Event Groups (for UI filtering) ─────────────────────────────

EVENT_GROUPS = {
    'vm_ct':     {'label': 'VM / CT',         'description': 'Start, stop, crash, migration'},
    'backup':    {'label': 'Backups',         'description': 'Backup start, complete, fail'},
    'resources': {'label': 'Resources',       'description': 'CPU, memory, temperature'},
    'storage':   {'label': 'Storage',         'description': 'Disk space, I/O, SMART'},
    'network':   {'label': 'Network',         'description': 'Connectivity, bond, latency'},
    'security':  {'label': 'Security',        'description': 'Auth failures, Fail2Ban, firewall'},
    'cluster':   {'label': 'Cluster',         'description': 'Quorum, split-brain, HA fencing'},
    'services':  {'label': 'Services',        'description': 'System services, shutdown, reboot'},
    'health':    {'label': 'Health Monitor',  'description': 'Health checks, degradation, recovery'},
    'updates':   {'label': 'Updates',         'description': 'System and PVE updates'},
    'other':     {'label': 'Other',           'description': 'Uncategorized notifications'},
}


# ─── Template Renderer ───────────────────────────────────────────

def _get_hostname() -> str:
    """Get short hostname for message titles."""
    try:
        return socket.gethostname().split('.')[0]
    except Exception:
        return 'proxmox'


def render_template(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Render a template into a structured notification object.
    
    Returns structured output usable by all channels:
        title, body (text), body_text, body_html (escaped), fields, tags, severity, group
    """
    import html as html_mod
    
    template = TEMPLATES.get(event_type)
    if not template:
        # Catch-all: unknown event types always get delivered (group 'other')
        # so no Proxmox notification is ever silently dropped.
        fallback_body = data.get('message', data.get('reason', str(data)))
        severity = data.get('severity', 'INFO')
        return {
            'title': f"{_get_hostname()}: {event_type}",
            'body': fallback_body, 'body_text': fallback_body,
            'body_html': f'<p>{html_mod.escape(str(fallback_body))}</p>',
            'fields': [], 'tags': [severity, 'other', event_type],
            'severity': severity, 'group': 'other',
        }
    
    # Ensure hostname is always available
    variables = {
        'hostname': _get_hostname(),
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'severity': data.get('severity', 'INFO'),
        # Burst event variables
        'window': '', 'entity_list': '',
        # Common defaults
        'vmid': '', 'vmname': '', 'reason': '', 'summary': '',
        'details': '', 'category': '', 'previous': '', 'current': '',
        'duration': '', 'value': '', 'threshold': '',
        'source_ip': '', 'username': '', 'service': '', 'service_name': '',
        'node_name': '', 'target_node': '', 'mount': '', 'device': '',
        'used': '', 'total': '', 'available': '', 'cores': '',
        'count': '', 'size': '', 'snapshot_name': '', 'jail': '',
        'failures': '', 'quorum': '', 'change_details': '', 'message': '',
        'security_count': '0', 'total_count': '0', 'package_list': '',
        'packages': '', 'pve_packages': '', 'version': '',
        'issue_list': '', 'error_key': '',
        'storage_name': '', 'storage_type': '',
        'important_list': 'none',
    }
    variables.update(data)
    
    # Ensure important_list is never blank (fallback to 'none')
    if not variables.get('important_list', '').strip():
        variables['important_list'] = 'none'
    
    try:
        title = template['title'].format(**variables)
    except (KeyError, ValueError):
        title = template['title']
    
    # ── PVE vzdump special formatting ──
    # When the event came from PVE webhook with a full vzdump message,
    # parse the table/logs and format a rich body instead of the sparse template.
    pve_message = data.get('pve_message', '')
    pve_title = data.get('pve_title', '')
    
    if event_type in ('backup_complete', 'backup_fail') and pve_message:
        parsed = _parse_vzdump_message(pve_message)
        if parsed:
            is_success = (event_type == 'backup_complete')
            body_text = _format_vzdump_body(parsed, is_success)
            # Use PVE's own title if available (contains hostname and status)
            if pve_title:
                title = pve_title
        else:
            # Couldn't parse -- use PVE raw message as body
            body_text = pve_message.strip()
    elif event_type == 'system_mail' and pve_message:
        # System mail -- use PVE message directly (mail bounce, cron, smartd)
        body_text = pve_message.strip()[:1000]
    else:
        try:
            body_text = template['body'].format(**variables)
        except (KeyError, ValueError):
            body_text = template['body']
    
    # Clean up: collapse runs of 3+ blank lines into 1, remove trailing whitespace
    import re as _re
    body_text = _re.sub(r'\n{3,}', '\n\n', body_text.strip())
    
    severity = variables.get('severity', 'INFO')
    group = template.get('group', 'system')
    
    # Build structured fields for Discord embeds / rich notifications
    fields = []
    field_map = [
        ('vmid', 'VM/CT'), ('vmname', 'Name'), ('device', 'Device'),
        ('source_ip', 'Source IP'), ('node_name', 'Node'), ('category', 'Category'),
        ('service_name', 'Service'), ('jail', 'Jail'), ('username', 'User'),
        ('count', 'Count'), ('window', 'Window'), ('entity_list', 'Affected'),
    ]
    for key, label in field_map:
        val = variables.get(key, '')
        if val:
            fields.append((label, str(val)))
    
    # Build HTML body with escaped content
    body_html_parts = []
    for line in body_text.split('\n'):
        if line.strip():
            body_html_parts.append(f'<p>{html_mod.escape(line)}</p>')
    body_html = '\n'.join(body_html_parts) if body_html_parts else f'<p>{html_mod.escape(body_text)}</p>'
    
    return {
        'title': title,
        'body': body_text,         # backward compat
        'body_text': body_text,
        'body_html': body_html,
        'fields': fields,
        'tags': [severity, group, event_type],
        'severity': severity,
        'group': group,
    }


def get_event_types_by_group() -> Dict[str, list]:
    """Get all event types organized by group, for UI rendering.
    
    Hidden templates (burst aggregations, internal types) are excluded
    from the UI. They still work in the backend and inherit enabled
    state from their parent event type.
    
    Returns:
        {group_key: [{'type': event_type, 'title': label, 
                       'default_enabled': bool}, ...]}
    """
    result = {}
    for event_type, template in TEMPLATES.items():
        # Skip hidden templates (bursts, internal, deprecated)
        if template.get('hidden', False):
            continue
        
        group = template.get('group', 'other')
        if group not in result:
            result[group] = []
        
        # Use explicit label if available, otherwise derive from title
        label = template.get('label', '')
        if not label:
            import re
            label = template['title'].replace('{hostname}', '').strip(': ')
            label = re.sub(r'\s*\{[^}]+\}', '', label).strip(' -:')
        if not label:
            label = event_type.replace('_', ' ').title()
        
        result[group].append({
            'type': event_type,
            'title': label,
            'default_enabled': template.get('default_enabled', True),
        })
    return result


def get_default_enabled_events() -> Dict[str, bool]:
    """Get the default enabled state for all event types."""
    return {
        event_type: template.get('default_enabled', True)
        for event_type, template in TEMPLATES.items()
    }


# ─── Emoji Enrichment (per-channel opt-in) ──────────────────────

# Category-level header icons
CATEGORY_EMOJI = {
    'vm_ct':     '\U0001F5A5\uFE0F',   # desktop computer
    'backup':    '\U0001F4BE',           # floppy disk (backup)
    'resources': '\U0001F4CA',           # bar chart
    'storage':   '\U0001F4BD',           # minidisc / hard disk
    'network':   '\U0001F310',           # globe with meridians
    'security':  '\U0001F6E1\uFE0F',    # shield
    'cluster':   '\U0001F517',           # chain link
    'services':  '\u2699\uFE0F',         # gear
    'health':    '\U0001FA7A',           # stethoscope
    'updates':   '\U0001F504',           # counterclockwise arrows (update)
    'other':     '\U0001F4E8',           # incoming envelope
}

# Event-specific title icons  (override category default when present)
EVENT_EMOJI = {
    # VM / CT
    'vm_start':             '\u25B6\uFE0F',    # play button
    'vm_stop':              '\u23F9\uFE0F',     # stop button
    'vm_shutdown':          '\u23CF\uFE0F',     # eject
    'vm_fail':              '\U0001F4A5',        # collision (crash)
    'vm_restart':           '\U0001F504',        # cycle
    'ct_start':             '\u25B6\uFE0F',
    'ct_stop':              '\u23F9\uFE0F',
    'ct_shutdown':          '\u23CF\uFE0F',
    'ct_restart':           '\U0001F504',
    'ct_fail':              '\U0001F4A5',
    'migration_start':      '\U0001F69A',        # moving truck
    'migration_complete':   '\u2705',            # check mark
    'migration_fail':       '\u274C',            # cross mark
    'replication_fail':     '\u274C',
    'replication_complete': '\u2705',
    # Backups
    'backup_start':         '\U0001F4BE\U0001F680',  # 💾🚀 floppy + rocket
    'backup_complete':      '\U0001F4BE\u2705',       # 💾✅ floppy + check
    'backup_fail':          '\U0001F4BE\u274C',       # 💾❌ floppy + cross
    'snapshot_complete':    '\U0001F4F8',         # camera with flash
    'snapshot_fail':        '\u274C',
    # Resources
    'cpu_high':             '\U0001F525',         # fire
    'ram_high':             '\U0001F4A7',         # droplet
    'temp_high':            '\U0001F321\uFE0F',   # thermometer
    'load_high':            '\u26A0\uFE0F',       # warning
    # Storage
    'disk_space_low':       '\U0001F4C9',         # chart decreasing
    'disk_io_error':        '\U0001F4A5',
    'storage_unavailable':  '\U0001F6AB',         # prohibited
    # Network
    'network_down':         '\U0001F50C',         # electric plug
    'network_latency':      '\U0001F422',         # turtle (slow)
    # Security
    'auth_fail':            '\U0001F6A8',         # police light
    'ip_block':             '\U0001F6B7',         # no pedestrians (banned)
    'firewall_issue':       '\U0001F525',
    'user_permission_change': '\U0001F511',       # key
    # Cluster
    'split_brain':          '\U0001F4A2',         # anger symbol
    'node_disconnect':      '\U0001F50C',
    'node_reconnect':       '\u2705',
    # Services
    'system_shutdown':      '\u23FB\uFE0F',       # power symbol (Unicode)
    'system_reboot':        '\U0001F504',
    'system_problem':       '\u26A0\uFE0F',
    'service_fail':         '\u274C',
    'oom_kill':             '\U0001F4A3',         # bomb
    # Health
    'new_error':            '\U0001F198',         # SOS
    'error_resolved':       '\u2705',
    'error_escalated':      '\U0001F53A',         # red triangle up
    'health_degraded':      '\u26A0\uFE0F',
    'health_persistent':    '\U0001F4CB',         # clipboard
    # Updates
    'update_summary':       '\U0001F4E6',
    'pve_update':           '\U0001F195',         # NEW
    'update_complete':      '\u2705',
}

# Decorative field-level icons for body text enrichment
FIELD_EMOJI = {
    'hostname':     '\U0001F4BB',   # laptop
    'vmid':         '\U0001F194',   # ID button
    'vmname':       '\U0001F3F7\uFE0F',  # label
    'device':       '\U0001F4BD',   # disk
    'mount':        '\U0001F4C2',   # open folder
    'source_ip':    '\U0001F310',   # globe
    'username':     '\U0001F464',   # bust in silhouette
    'service_name': '\u2699\uFE0F', # gear
    'node_name':    '\U0001F5A5\uFE0F',  # computer
    'target_node':  '\U0001F3AF',   # direct hit (target)
    'category':     '\U0001F4CC',   # pushpin
    'severity':     '\U0001F6A6',   # traffic light
    'duration':     '\u23F1\uFE0F', # stopwatch
    'timestamp':    '\U0001F552',   # clock three
    'size':         '\U0001F4CF',   # ruler
    'reason':       '\U0001F4DD',   # memo
    'value':        '\U0001F4CA',   # chart
    'threshold':    '\U0001F6A7',   # construction
    'jail':         '\U0001F512',   # lock
    'failures':     '\U0001F522',   # input numbers
    'quorum':       '\U0001F465',   # busts in silhouette
    'total_count':  '\U0001F4E6',   # package
    'security_count': '\U0001F6E1\uFE0F',  # shield
    'pve_count':    '\U0001F4E6',
    'kernel_count': '\u2699\uFE0F',
    'important_list': '\U0001F4CB',  # clipboard
}


def enrich_with_emojis(event_type: str, title: str, body: str,
                       data: Dict[str, Any]) -> tuple:
    """Replace the plain title/body with emoji-enriched versions.
    
    Returns (enriched_title, enriched_body).
    The function is idempotent: if the title already starts with an emoji,
    it is returned unchanged.
    """
    # Pick the best title icon: event-specific > category > severity circle
    template = TEMPLATES.get(event_type, {})
    group = template.get('group', 'other')
    severity = data.get('severity', 'INFO')
    
    icon = EVENT_EMOJI.get(event_type) or CATEGORY_EMOJI.get(group) or SEVERITY_ICONS.get(severity, '')
    
    # Build enriched title: replace severity circle with event-specific icon
    # Current format: "hostname: Something"  -> "ICON hostname: Something"
    # If title already starts with an emoji (from a previous pass), skip.
    enriched_title = title
    if icon and not any(title.startswith(e) for e in SEVERITY_ICONS.values()):
        enriched_title = f'{icon} {title}'
    elif icon:
        # Replace existing severity circle with richer icon
        for sev_icon in SEVERITY_ICONS.values():
            if title.startswith(sev_icon):
                enriched_title = title.replace(sev_icon, icon, 1)
                break
    
    # Build enriched body: prepend field emojis to recognizable lines
    lines = body.split('\n')
    enriched_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            enriched_lines.append(line)
            continue
        
        # Try to match "FieldName: value" patterns
        enriched = False
        for field_key, field_icon in FIELD_EMOJI.items():
            # Match common label patterns: "Device:", "Duration:", "Size:", etc.
            label_variants = [
                field_key.replace('_', ' ').title(),  # "Source Ip" -> not great
                field_key.replace('_', ' '),           # "source ip"
            ]
            # Also add specific known labels
            _LABEL_MAP = {
                'vmid': 'VM/CT', 'vmname': 'Name', 'source_ip': 'Source IP',
                'service_name': 'Service', 'node_name': 'Node',
                'target_node': 'Target', 'total_count': 'Total updates',
                'security_count': 'Security updates', 'pve_count': 'Proxmox-related updates',
                'kernel_count': 'Kernel updates', 'important_list': 'Important packages',
                'duration': 'Duration', 'severity': 'Previous severity',
                'original_severity': 'Previous severity',
            }
            if field_key in _LABEL_MAP:
                label_variants.append(_LABEL_MAP[field_key])
            
            for label in label_variants:
                if stripped.lower().startswith(label.lower() + ':'):
                    enriched_lines.append(f'{field_icon} {stripped}')
                    enriched = True
                    break
                elif stripped.lower().startswith(label.lower() + ' '):
                    enriched_lines.append(f'{field_icon} {stripped}')
                    enriched = True
                    break
            if enriched:
                break
        
        if not enriched:
            enriched_lines.append(line)
    
    enriched_body = '\n'.join(enriched_lines)
    
    return enriched_title, enriched_body


# ─── AI Enhancement (Optional) ───────────────────────────────────

# Supported languages for AI translation
AI_LANGUAGES = {
    'en': 'English',
    'es': 'Spanish',
    'fr': 'French',
    'de': 'German',
    'pt': 'Portuguese',
    'it': 'Italian',
    'ru': 'Russian',
    'sv': 'Swedish',
    'no': 'Norwegian',
    'ja': 'Japanese',
    'zh': 'Chinese',
    'nl': 'Dutch',
}

# Token limits for different detail levels
AI_DETAIL_TOKENS = {
    'brief': 100,      # 2-3 lines, essential only
    'standard': 200,   # Concise paragraph with context
    'detailed': 700,   # Complete technical details (raised: multi-VM backups can be long)
}

# System prompt template - informative, no recommendations
AI_SYSTEM_PROMPT = """You are a system notification formatter for ProxMenux Monitor, a Proxmox VE monitoring tool.

Your task is to translate and reformat incoming server alert messages into {language}.

═══ ABSOLUTE RULES ═══
1. Translate BOTH title and body to {language}. Every word, label, and unit must be in {language}.
2. NO markdown: no **bold**, no *italic*, no `code`, no headers (#), no bullet lists (- or *)
3. Plain text only — the output is sent to chat apps and email which handle their own formatting
4. Tone: factual, concise, technical. No greetings, no closings, no apologies
5. DO NOT add recommendations, action items, or suggestions ("you should…", "consider…")
6. Present ONLY the facts already in the input — do not invent or assume information
7. PLAIN NARRATIVE LINES — if a line in the input is a complete sentence (not a "Label: value"
   pair), translate it as-is. Never prepend "Message:", "Note:", or any other label to a sentence.
8. Detail level to apply: {detail_level}
   - brief    → 2-3 lines, essential data only (status + key metric)
   - standard → short paragraph covering who/what/where and the key value
   - detailed → full technical breakdown of all available fields
9. Keep the "hostname: " prefix in the title. Translate only the descriptive part.
   Example: "pve01: Updates available" → "pve01: Actualizaciones disponibles"
10. EMPTY LIST VALUES — if the input contains a list field that is empty, "none", or "0":
   - Always write the translated word for "none" on the line after the label, never leave it blank.
   - Example (English input "none"):  🗂️ Important packages:\n• none
   - Example (Spanish output):        🗂️ Paquetes importantes:\n• ninguno
11. DEDUPLICATION — input may contain redundant or repeated information from multiple monitoring sources:
   - Identify and merge duplicate facts (same device, same error, same metric mentioned twice)
   - Present each unique fact exactly once in a clear, consolidated form
   - If the same data appears in different formats, choose the most informative version
12. PROXMOX CONTEXT — silently translate Proxmox technical references into plain language.
    Never explain what the term means — just use the human-readable equivalent directly.

    Service / process name mapping (replace the raw name with the friendly form):
    - "pve-container@XXXX.service"  → "Container CT XXXX"
    - "qemu-server@XXXX.service"    → "Virtual Machine VM XXXX"
    - "pvesr-XXXX"                  → "storage replication job for XXXX"
    - "vzdump"                      → "backup process"
    - "pveproxy"                    → "Proxmox web proxy"
    - "pvedaemon"                   → "Proxmox daemon"
    - "pvestatd"                    → "Proxmox statistics service"
    - "pvescheduler"                → "Proxmox task scheduler"
    - "pve-cluster"                 → "Proxmox cluster service"
    - "corosync"                    → "cluster communication service"
    - "ceph-osd@N"                  → "Ceph storage disk N"
    - "ceph-mon"                    → "Ceph monitor service"

    systemd message patterns (rewrite the whole phrase, not just the service name):
    - "systemd[1]: pve-container@9000.service: Failed"
      → "Container CT 9000 service failed"
    - "systemd[1]: qemu-server@100.service: Failed with result 'exit-code'"
      → "Virtual Machine VM 100 failed to start"
    - "systemd[1]: Started pve-container@9000.service"
      → "Container CT 9000 started"

    ATA / SMART / kernel error patterns (replace raw kernel log with plain description):
    - "ata8.00: exception Emask 0x1 SAct 0x4ce0 SErr 0x40000 action 0x0"
      → "ATA controller error on port 8"
    - "blk_update_request: I/O error, dev sdX, sector NNNN"
      → "I/O error on disk /dev/sdX at sector NNNN"
    - "SCSI error: return code = 0x08000002"
      → "SCSI communication error"

    Apply these mappings everywhere: in the body narrative, in field values, and when
    the raw technical string appears inside a longer sentence.
{emoji_instructions}

═══ KNOWN MESSAGE TYPES AND HOW TO FORMAT THEM ═══

BACKUP (backup_complete / backup_fail / backup_start):
  Input contains: VM/CT names, IDs, size, duration, storage location, status per VM
  Output body: first line is plain text (no emoji) describing the event briefly.
  Then list each VM/CT with its fields. End with a summary line.
  PARTIAL FAILURE RULE: if some VMs succeeded and at least one failed, use a combined title
  like "Backup partially failed" / "Copia de seguridad parcialmente fallida" — never say
  "backup failed" when there are also successful VMs in the same job.
  NEVER omit the storage/archive line or the summary line — always include them even for long jobs.

UPDATES (update_summary / pve_update):
  Input contains: total count, security count, proxmox count, kernel count, package list
  Output body must show each count on its own line with its label.
  For the package list: use "• " (bullet + space) before each package name, NOT the 📋 emoji.
  The 📋 emoji goes only on the "Important packages:" header line.

  EXAMPLE — pve_update (new Proxmox VE version):
  - First line: plain sentence announcing the new version (no emoji — it goes on the title)
  - Blank line after the intro sentence
  - Current version line: 🔹 prefix
  - New version line:     🟢 prefix
  - Blank line before packages block
  - Packages header:      🗂️ prefix
  - Package lines:        📌 prefix (not bullet •), include version arrow as: v{old} ➜ v{new}

  EXAMPLE — pve_update:
  [TITLE]
  🆕 pve01: Proxmox VE 9.1.6 available
  [BODY]
  🚀 A new Proxmox VE release is available.

  🔹 Current: 9.1.4
  🟢 New: 9.1.6

  🗂️ Important packages:
  📌 pve-manager (v9.1.4 ➜ v9.1.6)

  Example packages block for update_summary:
    🗂️ Important packages:
    • pve-manager (9.1.4 -> 9.1.6)
    • qemu-server (9.1.3 -> 9.1.4)

DISK / SMART ERRORS (disk_io_error / storage_unavailable):
  Input contains: device name, error type, SMART values or I/O error codes
  Output body: device, then the specific error or failing attribute
  DEDUPLICATION: Input may contain repeated or similar information from multiple sources.
  If you see the same device, error count, or technical details mentioned multiple times,
  consolidate them into a single, clear statement. Never repeat the same information twice.

RESOURCES (cpu_high / ram_high / temp_high / load_high):
  Input contains: current value, threshold, core count
  Output: current value vs threshold, context if available

SECURITY (auth_fail / ip_block):
  Input contains: source IP, user, service, jail, failure count
  Output: list each field on its own line

VM/CT LIFECYCLE (vm_start, vm_stop, vm_fail, ct_*, migration_*, replication_*):
  Input contains: VM name, ID, target node (migrations), reason (failures)
  Output: one or two lines confirming the event with key facts

CLUSTER (split_brain / node_disconnect / node_reconnect):
  Input: node name, quorum status
  Output: state change + quorum value

HEALTH (new_error / error_resolved / health_persistent / health_degraded):
  Input: category, severity, duration, reason
  Output: what changed, in which category, for how long (if resolved)

═══ OUTPUT FORMAT (follow exactly — parsers rely on these markers) ═══
[TITLE]
translated title here
[BODY]
translated body here

CRITICAL OUTPUT RULES:
- Write [TITLE] on its own line, then the title on the very next line
- Write [BODY] on its own line, then the body starting on the very next line
- Do NOT write "Title:", "Título:", "Body:", "Cuerpo:" or any other label
- Do NOT include the literal words TITLE or BODY anywhere in the translated content
- Do NOT add extra blank lines between [TITLE] and the title text
- Do NOT add a blank line between [BODY] and the first body line"""

# Emoji instructions injected into AI_SYSTEM_PROMPT for rich channels (Telegram, Discord, Pushover)
AI_EMOJI_INSTRUCTIONS = """
9. EMOJI USAGE — place ONE emoji at the START of EVERY non-empty line (title and each body line).
   Never skip a line. Never put the emoji at the end. Never use two emojis on the same line.

   Use these exact emoji for each kind of content:

   TITLE emoji — pick by event type:
   ✅  success / resolved / complete / reconnected
   ❌  failed / FAILED / error
   💥  crash / collision / I/O error
   🆘  new critical health issue
   📦  backup started / updates available
   🆕  new PVE version available
   🔺  escalated / severity increased
   📋  health digest / persistent issues
   🚚  migration started
   🔌  network down / node disconnected
   🚨  auth failure / security alert
   🚷  IP banned / blocked
   🔑  permission change
   💢  split-brain
   💣  OOM kill
   ▶️  VM or CT started
   ⏹️  VM or CT stopped
   ⏏️  VM or CT shutdown
   🔄  restarted / reboot / proxmox updates
   🔥  high CPU / firewall issue
   💧  high memory
   🌡️  high temperature
   ⚠️  warning / degraded / high load / system problem
   📉  low disk space
   🚫  storage unavailable
   🐢  high latency
   📸  snapshot created
   ⏻  system shutdown

   BODY LINE emoji — pick by what the line is about:
   🏷️  VM name / CT name / ID / guest name
   ✅  status ok / success / completed
   ❌  status error / failed
   📏  size / tamaño / Größe
   ⏱️  duration / tiempo / Dauer
   🗄️  storage / almacenamiento / PBS
   🗃️  archive path / ruta de archivo
   📦  total updates / total actualizaciones
   🔒  security updates / actualizaciones de seguridad / jail
   🔄  proxmox updates / actualizaciones de proxmox
   ⚙️  kernel updates / actualizaciones del kernel / service
   📋  important packages header (update_summary)
   🗂️  important packages header (pve_update) / file index / archive listing
   🌐  source IP / IP origen
   👤  user / usuario
   📝  reason / motivo / razón / details
   🌡️  temperature / temperatura
   🔥  CPU usage / uso de CPU
   💧  memory / memoria
   📊  statistics / load / carga / summary line
   👥  quorum / cluster
   💿  disk device / dispositivo
   📂  filesystem / mount / ruta
   📌  category / categoría
   🚦  severity / severidad
   🖥️  node / nodo
   🎯  target / destino

   BLANK LINES FOR READABILITY — insert ONE blank line between logical sections within the body.
   Blank lines go BETWEEN groups, not before the first line or after the last line.
   A blank line must be completely empty — no emoji, no spaces.

   When to add a blank line:
   - Updates: after the last count line, before the packages block
   - Backup multi-VM: one blank line between each VM entry; one blank line before the summary line
   - Disk/SMART errors: after the device line, before the error description lines
   - VM events with a reason: after the main status line, before Reason / Node / Target lines
   - Health events: after the category/status line, before duration or detail lines

   EXAMPLE — updates message (no important packages):
   [TITLE]
   📦 amd: Updates available
   [BODY]
   📦 Total updates: 55
   🔒 Security updates: 0
   🔄 Proxmox updates: 0
   ⚙️ Kernel updates: 0

   🗂️ Important packages:
   • none

   EXAMPLE — updates message (with important packages):
   [TITLE]
   📦 amd: Updates available
   [BODY]
   📦 Total updates: 90
   🔒 Security updates: 6
   🔄 Proxmox updates: 14
   ⚙️ Kernel updates: 1

   🗂️ Important packages:
   • pve-manager (9.1.4 -> 9.1.6)
   • qemu-server (9.1.3 -> 9.1.4)
   • pve-container (6.0.18 -> 6.1.2)
   EXAMPLE — pve_update (new Proxmox VE version):
   [TITLE]
   🆕 pve01: Proxmox VE 9.1.6 available
   [BODY]
   🚀 A new Proxmox VE release is available.

   🔹 Current: 9.1.4
   🟢 New: 9.1.6

   🗂️ Important packages:
   📌 pve-manager (v9.1.4 ➜ v9.1.6)

   EXAMPLE — backup complete with multiple VMs:
   [TITLE]
   💾✅ pve01: Backup complete
   [BODY]
   Backup job finished on storage local-bak.

   🏷️ VM web01 (ID: 100)
   ✅ Status: ok
   📏 Size: 12.3 GiB
   ⏱️ Duration: 00:04:21
   🗄️ Storage: vm/100/2026-03-17T22:00:08Z

   🏷️ CT db (ID: 101)
   ✅ Status: ok
   📏 Size: 4.1 GiB
   ⏱️ Duration: 00:01:10
   🗄️ Storage: ct/101/2026-03-17T22:04:29Z

   📊 Total: 2 backups | 16.4 GiB | ⏱️ 00:05:31

   EXAMPLE — backup partially failed (some ok, some failed):
   [TITLE]
   💾❌ pve01: Backup partially failed
   [BODY]
   Backup job finished with errors on storage PBS2.

   🏷️ VM web01 (ID: 100)
   ✅ Status: ok
   📏 Size: 12.3 GiB
   ⏱️ Duration: 00:04:21
   🗄️ Storage: vm/100/2026-03-17T22:00:08Z

   🏷️ VM broken (ID: 102)
   ❌ Status: error
   📏 Size: 0 B
   ⏱️ Duration: 00:00:37

   📊 Total: 2 backups | ❌ 1 failed | 12.3 GiB | ⏱️ 00:04:58

   EXAMPLE — disk I/O health warning:
   [TITLE]
   💥 amd: Health warning — Disk I/O errors
   [BODY]
   💿 Device: /dev/sda

   ⚠️ 1 sector currently unreadable (pending)
   📝 Disk reports sectors in pending reallocation state

   EXAMPLE — VM failed:
   [TITLE]
   💥 pve01: VM web01 (100) FAILED
   [BODY]
   🏷️ Virtual machine web01 (ID: 100) failed to start.

   📝 Reason: kernel segfault"""

# No emoji instructions for email/plain text channels
AI_NO_EMOJI_INSTRUCTIONS = """
9. DO NOT use any emojis or special Unicode symbols. Plain ASCII text only for email compatibility."""


class AIEnhancer:
    """AI message enhancement using pluggable providers.
    
    Supports 6 providers: Groq, OpenAI, Anthropic, Gemini, Ollama, OpenRouter.
    Translates and formats notifications based on configured language and detail level.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize AIEnhancer with configuration.
        
        Args:
            config: Dictionary containing:
                - ai_provider: Provider name (groq, openai, anthropic, gemini, ollama, openrouter)
                - ai_api_key: API key (not required for ollama)
                - ai_model: Optional model override
                - ai_language: Target language code (en, es, fr, etc.)
                - ai_ollama_url: URL for Ollama server (optional)
        """
        self.config = config
        self._provider = None
        self._init_provider()
    
    def _init_provider(self):
        """Initialize the AI provider based on configuration."""
        try:
            # Import here to avoid circular imports
            import sys
            import os
            
            # Add script directory to path for ai_providers import
            script_dir = os.path.dirname(os.path.abspath(__file__))
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            
            from ai_providers import get_provider
            
            provider_name = self.config.get('ai_provider', 'groq')
            
            # Determine base_url based on provider
            if provider_name == 'ollama':
                base_url = self.config.get('ai_ollama_url', '')
            elif provider_name == 'openai':
                base_url = self.config.get('ai_openai_base_url', '')
            else:
                base_url = ''
            
            self._provider = get_provider(
                provider_name,
                api_key=self.config.get('ai_api_key', ''),
                model=self.config.get('ai_model', ''),
                base_url=base_url,
            )
        except Exception as e:
            print(f"[AIEnhancer] Failed to initialize provider: {e}")
            self._provider = None
    
    @property
    def enabled(self) -> bool:
        """Check if AI enhancement is available."""
        return self._provider is not None
    
    def enhance(self, title: str, body: str, severity: str,
                detail_level: str = 'standard',
                journal_context: str = '',
                use_emojis: bool = False) -> Optional[Dict[str, str]]:
        """Enhance/translate notification with AI.
        
        Args:
            title: Notification title
            body: Notification body text
            severity: Severity level (info, warning, critical)
            detail_level: Level of detail (brief, standard, detailed)
            journal_context: Optional journal log lines for context
            use_emojis: Whether to include emojis in the response (for push channels)
            
        Returns:
            Dict with 'title' and 'body' keys, or None if failed
        """
        if not self._provider:
            return None
        
        # Get language settings
        language_code = self.config.get('ai_language', 'en')
        language_name = AI_LANGUAGES.get(language_code, 'English')
        
        # Get token limit for detail level
        max_tokens = AI_DETAIL_TOKENS.get(detail_level, 200)
        
        # Select emoji instructions based on channel type
        emoji_instructions = AI_EMOJI_INSTRUCTIONS if use_emojis else AI_NO_EMOJI_INSTRUCTIONS
        
        # Build system prompt with emoji instructions
        system_prompt = AI_SYSTEM_PROMPT.format(
            language=language_name,
            detail_level=detail_level,
            emoji_instructions=emoji_instructions
        )
        
        # Build user message
        user_msg = f"Severity: {severity}\nTitle: {title}\nMessage:\n{body}"
        if journal_context:
            user_msg += f"\n\nJournal log context:\n{journal_context}"
        
        try:
            result = self._provider.generate(system_prompt, user_msg, max_tokens)
            return self._parse_ai_response(result, title, body)
        except Exception as e:
            print(f"[AIEnhancer] Enhancement failed: {e}")
            return None
    
    def _parse_ai_response(self, response: str, original_title: str, original_body: str) -> Dict[str, str]:
        """Parse AI response to extract title and body.
        
        Args:
            response: Raw AI response text
            original_title: Original title as fallback
            original_body: Original body as fallback
            
        Returns:
            Dict with 'title' and 'body' keys
        """
        if not response:
            return {'title': original_title, 'body': original_body}
        
        # Try to parse [TITLE] and [BODY] markers
        title_marker = '[TITLE]'
        body_marker = '[BODY]'
        
        title_start = response.find(title_marker)
        body_start = response.find(body_marker)
        
        if title_start != -1 and body_start != -1:
            # Extract title (between [TITLE] and [BODY])
            title_content = response[title_start + len(title_marker):body_start].strip()
            # Extract body (after [BODY])
            body_content = response[body_start + len(body_marker):].strip()
            
            return {
                'title': title_content if title_content else original_title,
                'body': body_content if body_content else original_body
            }
        
        # Fallback: if markers not found, use whole response as body
        return {
            'title': original_title,
            'body': response.strip()
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """Test the AI provider connection.
        
        Returns:
            Dict with success, message, and model info
        """
        if not self._provider:
            return {
                'success': False,
                'message': 'Provider not initialized',
                'model': ''
            }
        return self._provider.test_connection()


def format_with_ai(title: str, body: str, severity: str,
                   ai_config: Dict[str, Any],
                   detail_level: str = 'standard',
                   journal_context: str = '',
                   use_emojis: bool = False) -> str:
    """Format a message with AI enhancement/translation.
    
    Replaces the message body with AI-processed version if successful.
    Falls back to original body if AI is unavailable or fails.
    
    Args:
        title: Notification title
        body: Notification body
        severity: Severity level
        ai_config: Configuration dictionary with AI settings
        detail_level: Level of detail (brief, standard, detailed)
        journal_context: Optional journal log context
        use_emojis: Whether to include emojis (for push channels like Telegram/Discord)
    
    Returns:
        Enhanced body string or original if AI fails
    """
    result = format_with_ai_full(title, body, severity, ai_config, detail_level, journal_context, use_emojis)
    return result.get('body', body)


def format_with_ai_full(title: str, body: str, severity: str,
                        ai_config: Dict[str, Any],
                        detail_level: str = 'standard',
                        journal_context: str = '',
                        use_emojis: bool = False) -> Dict[str, str]:
    """Format a message with AI enhancement/translation, returning both title and body.
    
    Args:
        title: Notification title
        body: Notification body
        severity: Severity level
        ai_config: Configuration dictionary with AI settings
        detail_level: Level of detail (brief, standard, detailed)
        journal_context: Optional journal log context
        use_emojis: Whether to include emojis (for push channels like Telegram/Discord)
    
    Returns:
        Dict with 'title' and 'body' keys (translated/enhanced)
    """
    default_result = {'title': title, 'body': body}
    
    # Check if AI is enabled
    ai_enabled = ai_config.get('ai_enabled')
    if isinstance(ai_enabled, str):
        ai_enabled = ai_enabled.lower() == 'true'
    
    if not ai_enabled:
        return default_result
    
    # Check for API key (not required for Ollama)
    provider = ai_config.get('ai_provider', 'groq')
    if provider != 'ollama' and not ai_config.get('ai_api_key'):
        return default_result
    
    # For Ollama, check URL is configured
    if provider == 'ollama' and not ai_config.get('ai_ollama_url'):
        return default_result
    
    # Create enhancer and process
    enhancer = AIEnhancer(ai_config)
    enhanced = enhancer.enhance(
        title, body, severity,
        detail_level=detail_level,
        journal_context=journal_context,
        use_emojis=use_emojis
    )
    
    # Return enhanced result if successful, otherwise original
    if enhanced and isinstance(enhanced, dict):
        result_title = enhanced.get('title', title)
        result_body = enhanced.get('body', body)
        
        # For detailed level (email), append original message for reference
        # This ensures full technical data is available even after AI processing
        if detail_level == 'detailed' and body and len(body) > 50:
            # Only append if original has substantial content
            result_body += "\n\n" + "-" * 40 + "\n"
            result_body += "Original message:\n"
            result_body += body
        
        return {'title': result_title, 'body': result_body}
    
    return default_result
