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
import socket
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional


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
    'state_change': {
        'title': '{hostname}: {category} changed to {current}',
        'body': '{category} status changed from {previous} to {current}.\n{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'new_error': {
        'title': '{hostname}: New {severity} - {category}',
        'body': '{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'error_resolved': {
        'title': '{hostname}: Resolved - {category}',
        'body': '{reason}\nDuration: {duration}',
        'group': 'system',
        'default_enabled': True,
    },
    'error_escalated': {
        'title': '{hostname}: Escalated to {severity} - {category}',
        'body': '{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    
    # ── VM / CT events ──
    'vm_start': {
        'title': '{hostname}: VM {vmid} started',
        'body': '{vmname} ({vmid}) has been started.',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_stop': {
        'title': '{hostname}: VM {vmid} stopped',
        'body': '{vmname} ({vmid}) has been stopped.',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_shutdown': {
        'title': '{hostname}: VM {vmid} shutdown',
        'body': '{vmname} ({vmid}) has been shut down.',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'vm_fail': {
        'title': '{hostname}: VM {vmid} FAILED',
        'body': '{vmname} ({vmid}) has failed.\n{reason}',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'vm_restart': {
        'title': '{hostname}: VM {vmid} restarted',
        'body': '{vmname} ({vmid}) has been restarted.',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_start': {
        'title': '{hostname}: CT {vmid} started',
        'body': '{vmname} ({vmid}) has been started.',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'ct_stop': {
        'title': '{hostname}: CT {vmid} stopped',
        'body': '{vmname} ({vmid}) has been stopped.',
        'group': 'vm_ct',
        'default_enabled': False,
    },
    'ct_fail': {
        'title': '{hostname}: CT {vmid} FAILED',
        'body': '{vmname} ({vmid}) has failed.\n{reason}',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_start': {
        'title': '{hostname}: Migration started - {vmid}',
        'body': '{vmname} ({vmid}) migration to {target_node} started.',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_complete': {
        'title': '{hostname}: Migration complete - {vmid}',
        'body': '{vmname} ({vmid}) migrated successfully to {target_node}.',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    'migration_fail': {
        'title': '{hostname}: Migration FAILED - {vmid}',
        'body': '{vmname} ({vmid}) migration to {target_node} failed.\n{reason}',
        'group': 'vm_ct',
        'default_enabled': True,
    },
    
    # ── Backup / Snapshot events ──
    'backup_start': {
        'title': '{hostname}: Backup started - {vmid}',
        'body': 'Backup of {vmname} ({vmid}) has started.',
        'group': 'backup',
        'default_enabled': False,
    },
    'backup_complete': {
        'title': '{hostname}: Backup complete - {vmid}',
        'body': 'Backup of {vmname} ({vmid}) completed successfully.\nSize: {size}',
        'group': 'backup',
        'default_enabled': True,
    },
    'backup_fail': {
        'title': '{hostname}: Backup FAILED - {vmid}',
        'body': 'Backup of {vmname} ({vmid}) has failed.\n{reason}',
        'group': 'backup',
        'default_enabled': True,
    },
    'snapshot_complete': {
        'title': '{hostname}: Snapshot created - {vmid}',
        'body': 'Snapshot of {vmname} ({vmid}) created: {snapshot_name}',
        'group': 'backup',
        'default_enabled': False,
    },
    'snapshot_fail': {
        'title': '{hostname}: Snapshot FAILED - {vmid}',
        'body': 'Snapshot of {vmname} ({vmid}) failed.\n{reason}',
        'group': 'backup',
        'default_enabled': True,
    },
    
    # ── Resource events (from Health Monitor) ──
    'cpu_high': {
        'title': '{hostname}: High CPU usage ({value}%)',
        'body': 'CPU usage is at {value}% on {cores} cores.\n{details}',
        'group': 'resources',
        'default_enabled': True,
    },
    'ram_high': {
        'title': '{hostname}: High memory usage ({value}%)',
        'body': 'Memory usage: {used} / {total} ({value}%).\n{details}',
        'group': 'resources',
        'default_enabled': True,
    },
    'temp_high': {
        'title': '{hostname}: High temperature ({value}C)',
        'body': 'CPU temperature: {value}C (threshold: {threshold}C).\n{details}',
        'group': 'resources',
        'default_enabled': True,
    },
    'disk_space_low': {
        'title': '{hostname}: Low disk space on {mount}',
        'body': '{mount}: {used}% used ({available} available).',
        'group': 'storage',
        'default_enabled': True,
    },
    'disk_io_error': {
        'title': '{hostname}: Disk I/O error',
        'body': 'I/O error detected on {device}.\n{reason}',
        'group': 'storage',
        'default_enabled': True,
    },
    'load_high': {
        'title': '{hostname}: High system load ({value})',
        'body': 'System load average: {value} on {cores} cores.\n{details}',
        'group': 'resources',
        'default_enabled': True,
    },
    
    # ── Network events ──
    'network_down': {
        'title': '{hostname}: Network connectivity lost',
        'body': 'Network connectivity check failed.\n{reason}',
        'group': 'network',
        'default_enabled': True,
    },
    'network_latency': {
        'title': '{hostname}: High network latency ({value}ms)',
        'body': 'Latency to gateway: {value}ms (threshold: {threshold}ms).',
        'group': 'network',
        'default_enabled': False,
    },
    
    # ── Security events ──
    'auth_fail': {
        'title': '{hostname}: Authentication failure',
        'body': 'Failed login attempt from {source_ip}.\nUser: {username}\nService: {service}',
        'group': 'security',
        'default_enabled': True,
    },
    'ip_block': {
        'title': '{hostname}: IP blocked by Fail2Ban',
        'body': 'IP {source_ip} has been banned.\nJail: {jail}\nFailures: {failures}',
        'group': 'security',
        'default_enabled': True,
    },
    'firewall_issue': {
        'title': '{hostname}: Firewall issue detected',
        'body': '{reason}',
        'group': 'security',
        'default_enabled': True,
    },
    'user_permission_change': {
        'title': '{hostname}: User permission changed',
        'body': 'User: {username}\nChange: {change_details}',
        'group': 'security',
        'default_enabled': True,
    },
    
    # ── Cluster events ──
    'split_brain': {
        'title': '{hostname}: SPLIT-BRAIN detected',
        'body': 'Cluster split-brain condition detected.\nQuorum status: {quorum}',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_disconnect': {
        'title': '{hostname}: Node disconnected',
        'body': 'Node {node_name} has disconnected from the cluster.',
        'group': 'cluster',
        'default_enabled': True,
    },
    'node_reconnect': {
        'title': '{hostname}: Node reconnected',
        'body': 'Node {node_name} has reconnected to the cluster.',
        'group': 'cluster',
        'default_enabled': True,
    },
    
    # ── System events ──
    'system_shutdown': {
        'title': '{hostname}: System shutting down',
        'body': 'The system is shutting down.\n{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'system_reboot': {
        'title': '{hostname}: System rebooting',
        'body': 'The system is rebooting.\n{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'system_problem': {
        'title': '{hostname}: System problem detected',
        'body': '{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'service_fail': {
        'title': '{hostname}: Service failed - {service_name}',
        'body': 'Service {service_name} has failed.\n{reason}',
        'group': 'system',
        'default_enabled': True,
    },
    'update_available': {
        'title': '{hostname}: Updates available ({count})',
        'body': '{count} package updates are available.\n{details}',
        'group': 'system',
        'default_enabled': False,
    },
    'update_complete': {
        'title': '{hostname}: Update completed',
        'body': '{details}',
        'group': 'system',
        'default_enabled': False,
    },
    
    # ── Unknown persistent (from health monitor) ──
    'unknown_persistent': {
        'title': '{hostname}: Check unavailable - {category}',
        'body': 'Health check for {category} has been unavailable for 3+ cycles.\n{reason}',
        'group': 'system',
        'default_enabled': False,
    },
    
    # ── Burst aggregation summaries ──
    'burst_auth_fail': {
        'title': '{hostname}: {count} auth failures in {window}',
        'body': '{count} authentication failures detected in {window}.\nSources: {entity_list}',
        'group': 'security',
        'default_enabled': True,
    },
    'burst_ip_block': {
        'title': '{hostname}: Fail2Ban banned {count} IPs in {window}',
        'body': '{count} IPs banned by Fail2Ban in {window}.\nIPs: {entity_list}',
        'group': 'security',
        'default_enabled': True,
    },
    'burst_disk_io': {
        'title': '{hostname}: {count} disk I/O errors on {entity_list}',
        'body': '{count} I/O errors detected in {window}.\nDevices: {entity_list}',
        'group': 'storage',
        'default_enabled': True,
    },
    'burst_cluster': {
        'title': '{hostname}: Cluster flapping detected ({count} changes)',
        'body': 'Cluster state changed {count} times in {window}.\nNodes: {entity_list}',
        'group': 'cluster',
        'default_enabled': True,
    },
    'burst_generic': {
        'title': '{hostname}: {count} {event_type} events in {window}',
        'body': '{count} events of type {event_type} in {window}.\n{entity_list}',
        'group': 'system',
        'default_enabled': True,
    },
}

# ─── Event Groups (for UI filtering) ─────────────────────────────

EVENT_GROUPS = {
    'system':    {'label': 'System',     'description': 'System health, services, updates'},
    'vm_ct':     {'label': 'VM / CT',    'description': 'Virtual machines and containers'},
    'backup':    {'label': 'Backup',     'description': 'Backups and snapshots'},
    'resources': {'label': 'Resources',  'description': 'CPU, memory, temperature, load'},
    'storage':   {'label': 'Storage',    'description': 'Disk space and I/O'},
    'network':   {'label': 'Network',    'description': 'Connectivity and latency'},
    'security':  {'label': 'Security',   'description': 'Authentication, firewall, bans'},
    'cluster':   {'label': 'Cluster',    'description': 'Cluster health and quorum'},
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
        fallback_body = data.get('message', data.get('reason', str(data)))
        severity = data.get('severity', 'INFO')
        return {
            'title': f"{_get_hostname()}: {event_type}",
            'body': fallback_body, 'body_text': fallback_body,
            'body_html': f'<p>{html_mod.escape(str(fallback_body))}</p>',
            'fields': [], 'tags': [severity, 'system', event_type],
            'severity': severity, 'group': 'system',
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
    }
    variables.update(data)
    
    try:
        title = template['title'].format(**variables)
    except (KeyError, ValueError):
        title = template['title']
    
    try:
        body_text = template['body'].format(**variables)
    except (KeyError, ValueError):
        body_text = template['body']
    
    # Clean up empty lines from missing optional variables
    body_text = '\n'.join(line for line in body_text.split('\n') if line.strip())
    
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
    
    Returns:
        {group_key: [{'type': event_type, 'title': template_title, 
                       'default_enabled': bool}, ...]}
    """
    result = {}
    for event_type, template in TEMPLATES.items():
        group = template.get('group', 'system')
        if group not in result:
            result[group] = []
        import re
        # Clean title: remove {hostname}: prefix and any remaining {placeholders}
        title = template['title'].replace('{hostname}', '').strip(': ')
        title = re.sub(r'\s*\{[^}]+\}', '', title).strip(' -:')
        if not title:
            title = event_type.replace('_', ' ').title()
        result[group].append({
            'type': event_type,
            'title': title,
            'default_enabled': template.get('default_enabled', True),
        })
    return result


def get_default_enabled_events() -> Dict[str, bool]:
    """Get the default enabled state for all event types."""
    return {
        event_type: template.get('default_enabled', True)
        for event_type, template in TEMPLATES.items()
    }


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
