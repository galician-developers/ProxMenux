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
        'title': '{hostname}: VM {vmid} started',
        'body': '{vmname} ({vmid}) has been started.',
        'label': 'VM started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_stop': {
        'title': '{hostname}: VM {vmid} stopped',
        'body': '{vmname} ({vmid}) has been stopped.',
        'label': 'VM stopped',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_shutdown': {
        'title': '{hostname}: VM {vmid} shutdown',
        'body': '{vmname} ({vmid}) has been shut down.',
        'label': 'VM shutdown',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_fail': {
        'title': '{hostname}: VM {vmid} FAILED',
        'body': '{vmname} ({vmid}) has failed.\n{reason}',
        'label': 'VM FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_restart': {
        'title': '{hostname}: VM {vmid} restarted',
        'body': '{vmname} ({vmid}) has been restarted.',
        'label': 'VM restarted',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_start': {
        'title': '{hostname}: CT {vmid} started',
        'body': '{vmname} ({vmid}) has been started.',
        'label': 'CT started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'ct_stop': {
        'title': '{hostname}: CT {vmid} stopped',
        'body': '{vmname} ({vmid}) has been stopped.',
        'label': 'CT stopped',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_shutdown': {
        'title': '{hostname}: CT {vmid} shutdown',
        'body': '{vmname} ({vmid}) has been shut down.',
        'label': 'CT shutdown',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_restart': {
        'title': '{hostname}: CT {vmid} restarted',
        'body': '{vmname} ({vmid}) has been restarted.',
        'label': 'CT restarted',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_fail': {
        'title': '{hostname}: CT {vmid} FAILED',
        'body': '{vmname} ({vmid}) has failed.\n{reason}',
        'label': 'CT FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_start': {
        'title': '{hostname}: Migration started - {vmid}',
        'body': '{vmname} ({vmid}) migration to {target_node} started.',
        'label': 'Migration started',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_complete': {
        'title': '{hostname}: Migration complete - {vmid}',
        'body': '{vmname} ({vmid}) migrated successfully to {target_node}.',
        'label': 'Migration complete',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_fail': {
        'title': '{hostname}: Migration FAILED - {vmid}',
        'body': '{vmname} ({vmid}) migration to {target_node} failed.\n{reason}',
        'label': 'Migration FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'replication_fail': {
        'title': '{hostname}: Replication FAILED - {vmid}',
        'body': 'Replication of {vmname} ({vmid}) has failed.\n{reason}',
        'label': 'Replication FAILED',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'replication_complete': {
        'title': '{hostname}: Replication complete - {vmid}',
        'body': 'Replication of {vmname} ({vmid}) completed successfully.',
        'label': 'Replication complete',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    
    # ── Backup / Snapshot events ──
    'backup_start': {
        'title': '{hostname}: Backup started [{storage}]',
        'body': '{reason}',
        'label': 'Backup started',
        'group': 'backup',
        'default_enabled': False,
    },
    'backup_complete': {
        'title': '{hostname}: Backup complete - {vmid}',
        'body': 'Backup of {vmname} ({vmid}) completed successfully.\nSize: {size}',
        'label': 'Backup complete',
        'group': 'backup',
        'default_enabled': True,
    },
    'backup_fail': {
        'title': '{hostname}: Backup FAILED - {vmid}',
        'body': 'Backup of {vmname} ({vmid}) has failed.\n{reason}',
        'label': 'Backup FAILED',
        'group': 'backup',
        'default_enabled': True,
    },
    'snapshot_complete': {
        'title': '{hostname}: Snapshot created - {vmid}',
        'body': 'Snapshot of {vmname} ({vmid}) created: {snapshot_name}',
        'label': 'Snapshot created',
        'group': 'backup',
        'default_enabled': False,
    },
    'snapshot_fail': {
        'title': '{hostname}: Snapshot FAILED - {vmid}',
        'body': 'Snapshot of {vmname} ({vmid}) failed.\n{reason}',
        'label': 'Snapshot FAILED',
        'group': 'backup',
        'default_enabled': True,
    },
    
    # ── Resource events (from Health Monitor) ──
    'cpu_high': {
        'title': '{hostname}: High CPU usage ({value}%)',
        'body': 'CPU usage is at {value}% on {cores} cores.\n{details}',
        'label': 'High CPU usage',
        'group': 'resources',
        'default_enabled': True,
    },
    'ram_high': {
        'title': '{hostname}: High memory usage ({value}%)',
        'body': 'Memory usage: {used} / {total} ({value}%).\n{details}',
        'label': 'High memory usage',
        'group': 'resources',
        'default_enabled': True,
    },
    'temp_high': {
        'title': '{hostname}: High temperature ({value}C)',
        'body': 'CPU temperature: {value}C (threshold: {threshold}C).\n{details}',
        'label': 'High temperature',
        'group': 'resources',
        'default_enabled': True,
    },
    'disk_space_low': {
        'title': '{hostname}: Low disk space on {mount}',
        'body': '{mount}: {used}% used ({available} available).',
        'label': 'Low disk space',
        'group': 'storage',
        'default_enabled': True,
    },
    'disk_io_error': {
        'title': '{hostname}: Disk failure detected on {device}',
        'body': '{reason}',
        'label': 'Disk failure / I/O error',
        'group': 'storage',
        'default_enabled': True,
    },
    'storage_unavailable': {
        'title': '{hostname}: Storage unavailable - {storage_name}',
        'body': 'PVE storage "{storage_name}" ({storage_type}) is not available.\n{reason}',
        'label': 'Storage unavailable',
        'group': 'storage',
        'default_enabled': True,
    },
    'load_high': {
        'title': '{hostname}: High system load ({value})',
        'body': 'System load average: {value} on {cores} cores.\n{details}',
        'label': 'High system load',
        'group': 'resources',
        'default_enabled': True,
    },
    
    # ── Network events ──
    'network_down': {
        'title': '{hostname}: Network connectivity lost',
        'body': 'Network connectivity check failed.\n{reason}',
        'label': 'Network connectivity lost',
        'group': 'network',
        'default_enabled': True,
    },
    'network_latency': {
        'title': '{hostname}: High network latency ({value}ms)',
        'body': 'Latency to gateway: {value}ms (threshold: {threshold}ms).',
        'label': 'High network latency',
        'group': 'network',
        'default_enabled': False,
    },
    
    # ── Security events ──
    'auth_fail': {
        'title': '{hostname}: Authentication failure',
        'body': 'Failed login attempt from {source_ip}.\nUser: {username}\nService: {service}',
        'label': 'Authentication failure',
        'group': 'security',
        'default_enabled': True,
    },
    'ip_block': {
        'title': '{hostname}: IP blocked by Fail2Ban',
        'body': 'IP {source_ip} has been banned.\nJail: {jail}\nFailures: {failures}',
        'label': 'IP blocked by Fail2Ban',
        'group': 'security',
        'default_enabled': True,
    },
    'firewall_issue': {
        'title': '{hostname}: Firewall issue detected',
        'body': '{reason}',
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
        'body': 'Cluster split-brain condition detected.\nQuorum status: {quorum}',
        'label': 'SPLIT-BRAIN detected',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_disconnect': {
        'title': '{hostname}: Node disconnected',
        'body': 'Node {node_name} has disconnected from the cluster.',
        'label': 'Node disconnected',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_reconnect': {
        'title': '{hostname}: Node reconnected',
        'body': 'Node {node_name} has reconnected to the cluster.',
        'label': 'Node reconnected',
        'group': 'cluster',
        'default_enabled': True,
    },
    
    # ── Services events ──
    'system_shutdown': {
        'title': '{hostname}: System shutting down',
        'body': '{reason}',
        'label': 'System shutting down',
        'group': 'services',
        'default_enabled': True,
    },
    'system_reboot': {
        'title': '{hostname}: System rebooting',
        'body': '{reason}',
        'label': 'System rebooting',
        'group': 'services',
        'default_enabled': True,
    },
    'system_problem': {
        'title': '{hostname}: System problem detected',
        'body': '{reason}',
        'label': 'System problem detected',
        'group': 'services',
        'default_enabled': True,
    },
    'service_fail': {
        'title': '{hostname}: Service failed - {service_name}',
        'body': '{reason}',
        'label': 'Service failed',
        'group': 'services',
        'default_enabled': True,
    },
    'oom_kill': {
        'title': '{hostname}: OOM Kill - {process}',
        'body': '{reason}',
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
        'body': 'The following health issues remain active:\n{issue_list}\n\nThis digest is sent once every 24 hours while issues persist.',
        'label': 'Active health issues (daily)',
        'group': 'health',
        'default_enabled': True,
    },
    'health_issue_new': {
        'title': '{hostname}: New health issue - {category}',
        'body': 'New {severity} issue detected:\n{reason}',
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
        'body': 'Proxmox VE {current_version} -> {new_version}\n{details}',
        'label': 'Proxmox VE update available',
        'group': 'updates',
        'default_enabled': True,
    },
    'update_complete': {
        'title': '{hostname}: Update completed',
        'body': '{details}',
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
    }
    variables.update(data)
    
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
    'backup_start':         '\U0001F4E6',        # package
    'backup_complete':      '\u2705',
    'backup_fail':          '\u274C',
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

class AIEnhancer:
    """Optional AI message enhancement using external LLM API.
    
    Enriches template-generated messages with context and suggestions.
    Falls back to original message if AI is unavailable or fails.
    """
    
    SYSTEM_PROMPT = """You are a Proxmox system administrator assistant. 
You receive a notification message about a server event and must enhance it with:
1. A brief explanation of what this means in practical terms
2. A suggested action if applicable (1-2 sentences max)

Keep the response concise (max 3 sentences total). Do not repeat the original message.
Respond in the same language as the input message."""
    
    def __init__(self, provider: str, api_key: str, model: str = ''):
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model
        self._enabled = bool(api_key)
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def enhance(self, title: str, body: str, severity: str) -> Optional[str]:
        """Enhance a notification message with AI context.
        
        Returns enhanced body text, or None if enhancement fails/disabled.
        """
        if not self._enabled:
            return None
        
        try:
            if self.provider in ('openai', 'groq'):
                return self._call_openai_compatible(title, body, severity)
        except Exception as e:
            print(f"[AIEnhancer] Enhancement failed: {e}")
        
        return None
    
    def _call_openai_compatible(self, title: str, body: str, severity: str) -> Optional[str]:
        """Call OpenAI-compatible API (works with OpenAI, Groq, local)."""
        if self.provider == 'groq':
            url = 'https://api.groq.com/openai/v1/chat/completions'
            model = self.model or 'llama-3.3-70b-versatile'
        else:  # openai
            url = 'https://api.openai.com/v1/chat/completions'
            model = self.model or 'gpt-4o-mini'
        
        user_msg = f"Severity: {severity}\nTitle: {title}\nMessage: {body}"
        
        payload = json.dumps({
            'model': model,
            'messages': [
                {'role': 'system', 'content': self.SYSTEM_PROMPT},
                {'role': 'user', 'content': user_msg},
            ],
            'max_tokens': 150,
            'temperature': 0.3,
        }).encode('utf-8')
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
        }
        
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result['choices'][0]['message']['content'].strip()
            return content if content else None


def format_with_ai(title: str, body: str, severity: str,
                   ai_config: Dict[str, str]) -> str:
    """Format a message with optional AI enhancement.
    
    If AI is configured and succeeds, appends AI insight to the body.
    Otherwise returns the original body unchanged.
    
    Args:
        title: Notification title
        body: Notification body
        severity: Severity level
        ai_config: {'enabled': 'true', 'provider': 'groq', 'api_key': '...', 'model': ''}
    
    Returns:
        Enhanced body string
    """
    if ai_config.get('enabled') != 'true' or not ai_config.get('api_key'):
        return body
    
    enhancer = AIEnhancer(
        provider=ai_config.get('provider', 'groq'),
        api_key=ai_config['api_key'],
        model=ai_config.get('model', ''),
    )
    
    insight = enhancer.enhance(title, body, severity)
    if insight:
        return f"{body}\n\n---\n{insight}"
    
    return body
