"""
ProxMenux Notification Manager
Central orchestrator for the notification service.

Connects:
- notification_channels.py  (transport: Telegram, Gotify, Discord)
- notification_templates.py (message formatting + optional AI)
- notification_events.py    (event detection: Journal, Task, Polling watchers)
- health_persistence.py     (DB: config storage, notification_history)

Two interfaces consume this module:
1. Server mode: Flask imports and calls start()/stop()/send_notification()
2. CLI mode:    `python3 notification_manager.py --action send --type vm_fail ...`
                Scripts .sh in /usr/local/share/proxmenux/scripts call this directly.

Author: MacRimi
"""

import json
import os
import sys
import time
import socket
import sqlite3
import threading
from queue import Queue, Empty
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

# Ensure local imports work
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from notification_channels import create_channel, CHANNEL_TYPES
from notification_templates import (
    render_template, format_with_ai, TEMPLATES,
    EVENT_GROUPS, get_event_types_by_group, get_default_enabled_events
)
from notification_events import (
    JournalWatcher, TaskWatcher, PollingCollector, NotificationEvent
)


# ─── Constants ────────────────────────────────────────────────────

DB_PATH = Path('/usr/local/share/proxmenux/health_monitor.db')
SETTINGS_PREFIX = 'notification.'

# Cooldown defaults (seconds)
DEFAULT_COOLDOWNS = {
    'CRITICAL': 0,       # No cooldown for critical
    'WARNING':  300,     # 5 min
    'INFO':     900,     # 15 min
    'resources': 900,    # 15 min for resource alerts
    'updates':  86400,   # 24h for update notifications
}


# ─── Notification Manager ─────────────────────────────────────────

class NotificationManager:
    """Central notification orchestrator.
    
    Manages channels, event watchers, deduplication, and dispatch.
    Can run in server mode (background threads) or CLI mode (one-shot).
    """
    
    def __init__(self):
        self._channels: Dict[str, Any] = {}  # channel_name -> channel_instance
        self._event_queue: Queue = Queue()
        self._running = False
        self._config: Dict[str, str] = {}
        self._enabled = False
        self._lock = threading.Lock()
        
        # Watchers
        self._journal_watcher: Optional[JournalWatcher] = None
        self._task_watcher: Optional[TaskWatcher] = None
        self._polling_collector: Optional[PollingCollector] = None
        self._dispatch_thread: Optional[threading.Thread] = None
        
        # Cooldown tracking: {event_type_or_key: last_sent_timestamp}
        self._cooldowns: Dict[str, float] = {}
        
        # Stats
        self._stats = {
            'started_at': None,
            'total_sent': 0,
            'total_errors': 0,
            'last_sent_at': None,
        }
    
    # ─── Configuration ──────────────────────────────────────────
    
    def _load_config(self):
        """Load notification settings from the shared SQLite database."""
        self._config = {}
        try:
            if not DB_PATH.exists():
                return
            
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            cursor = conn.cursor()
            cursor.execute(
                'SELECT setting_key, setting_value FROM user_settings WHERE setting_key LIKE ?',
                (f'{SETTINGS_PREFIX}%',)
            )
            for key, value in cursor.fetchall():
                # Strip prefix for internal use
                short_key = key[len(SETTINGS_PREFIX):]
                self._config[short_key] = value
            conn.close()
        except Exception as e:
            print(f"[NotificationManager] Failed to load config: {e}")
        
        self._enabled = self._config.get('enabled', 'false') == 'true'
        self._rebuild_channels()
    
    def _save_setting(self, key: str, value: str):
        """Save a single notification setting to the database."""
        full_key = f'{SETTINGS_PREFIX}{key}'
        now = datetime.now().isoformat()
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO user_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
            ''', (full_key, value, now))
            conn.commit()
            conn.close()
            self._config[key] = value
        except Exception as e:
            print(f"[NotificationManager] Failed to save setting {key}: {e}")
    
    def _rebuild_channels(self):
        """Rebuild channel instances from current config."""
        self._channels = {}
        
        for ch_type in CHANNEL_TYPES:
            enabled_key = f'{ch_type}.enabled'
            if self._config.get(enabled_key) != 'true':
                continue
            
            # Gather config keys for this channel
            ch_config = {}
            for config_key in CHANNEL_TYPES[ch_type]['config_keys']:
                full_key = f'{ch_type}.{config_key}'
                ch_config[config_key] = self._config.get(full_key, '')
            
            channel = create_channel(ch_type, ch_config)
            if channel:
                valid, err = channel.validate_config()
                if valid:
                    self._channels[ch_type] = channel
                else:
                    print(f"[NotificationManager] Channel {ch_type} invalid: {err}")
    
    def reload_config(self):
        """Reload config from DB without restarting."""
        with self._lock:
            self._load_config()
        return {'success': True, 'channels': list(self._channels.keys())}
    
    # ─── Server Mode (Background) ──────────────────────────────
    
    def start(self):
        """Start the notification service in server mode.
        
        Launches watchers and dispatch loop as daemon threads.
        Called by flask_server.py on startup.
        """
        if self._running:
            return
        
        self._load_config()
        
        if not self._enabled:
            print("[NotificationManager] Service is disabled. Skipping start.")
            return
        
        self._running = True
        self._stats['started_at'] = datetime.now().isoformat()
        
        # Start event watchers
        self._journal_watcher = JournalWatcher(self._event_queue)
        self._task_watcher = TaskWatcher(self._event_queue)
        self._polling_collector = PollingCollector(self._event_queue)
        
        self._journal_watcher.start()
        self._task_watcher.start()
        self._polling_collector.start()
        
        # Start dispatch loop
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name='notification-dispatch'
        )
        self._dispatch_thread.start()
        
        print(f"[NotificationManager] Started with channels: {list(self._channels.keys())}")
    
    def stop(self):
        """Stop the notification service cleanly."""
        self._running = False
        
        if self._journal_watcher:
            self._journal_watcher.stop()
        if self._task_watcher:
            self._task_watcher.stop()
        if self._polling_collector:
            self._polling_collector.stop()
        
        print("[NotificationManager] Stopped.")
    
    def _dispatch_loop(self):
        """Main dispatch loop: reads queue -> filters -> formats -> sends -> records."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=2)
            except Empty:
                continue
            
            try:
                self._process_event(event)
            except Exception as e:
                print(f"[NotificationManager] Dispatch error: {e}")
    
    def _process_event(self, event: NotificationEvent):
        """Process a single event from the queue."""
        if not self._enabled:
            return
        
        # Check if this event type is enabled in settings
        event_setting = f'events.{event.event_type}'
        if self._config.get(event_setting, 'true') == 'false':
            return
        
        # Check severity filter
        min_severity = self._config.get('filter.min_severity', 'INFO')
        if not self._meets_severity(event.severity, min_severity):
            return
        
        # Check cooldown
        if not self._check_cooldown(event):
            return
        
        # Render message from template
        rendered = render_template(event.event_type, event.data)
        
        # Optional AI enhancement
        ai_config = {
            'enabled': self._config.get('ai_enabled', 'false'),
            'provider': self._config.get('ai_provider', ''),
            'api_key': self._config.get('ai_api_key', ''),
            'model': self._config.get('ai_model', ''),
        }
        body = format_with_ai(
            rendered['title'], rendered['body'], rendered['severity'], ai_config
        )
        
        # Send through all active channels
        self._dispatch_to_channels(
            rendered['title'], body, rendered['severity'],
            event.event_type, event.data, event.source
        )
    
    def _dispatch_to_channels(self, title: str, body: str, severity: str,
                               event_type: str, data: Dict, source: str):
        """Send notification through all configured channels."""
        with self._lock:
            channels = dict(self._channels)
        
        for ch_name, channel in channels.items():
            try:
                result = channel.send(title, body, severity, data)
                self._record_history(
                    event_type, ch_name, title, body, severity,
                    result.get('success', False),
                    result.get('error', ''),
                    source
                )
                
                if result.get('success'):
                    self._stats['total_sent'] += 1
                    self._stats['last_sent_at'] = datetime.now().isoformat()
                else:
                    self._stats['total_errors'] += 1
                    print(f"[NotificationManager] Send failed ({ch_name}): {result.get('error')}")
                    
            except Exception as e:
                self._stats['total_errors'] += 1
                self._record_history(
                    event_type, ch_name, title, body, severity,
                    False, str(e), source
                )
    
    # ─── Cooldown / Dedup ───────────────────────────────────────
    
    def _check_cooldown(self, event: NotificationEvent) -> bool:
        """Check if the event passes cooldown rules."""
        now = time.time()
        
        # Determine cooldown period
        template = TEMPLATES.get(event.event_type, {})
        group = template.get('group', 'system')
        
        # Priority: per-type config > per-severity > default
        cooldown_key = f'cooldown.{event.event_type}'
        cooldown_str = self._config.get(cooldown_key)
        
        if cooldown_str is None:
            cooldown_key_group = f'cooldown.{group}'
            cooldown_str = self._config.get(cooldown_key_group)
        
        if cooldown_str is not None:
            cooldown = int(cooldown_str)
        else:
            cooldown = DEFAULT_COOLDOWNS.get(event.severity, 300)
        
        # CRITICAL events have zero cooldown by default
        if event.severity == 'CRITICAL' and cooldown_str is None:
            cooldown = 0
        
        # Check against last sent time
        dedup_key = f"{event.event_type}:{event.data.get('category', '')}:{event.data.get('vmid', '')}"
        last_sent = self._cooldowns.get(dedup_key, 0)
        
        if now - last_sent < cooldown:
            return False
        
        self._cooldowns[dedup_key] = now
        return True
    
    @staticmethod
    def _meets_severity(event_severity: str, min_severity: str) -> bool:
        """Check if event severity meets the minimum threshold."""
        levels = {'INFO': 0, 'WARNING': 1, 'CRITICAL': 2}
        return levels.get(event_severity, 0) >= levels.get(min_severity, 0)
    
    # ─── History Recording ──────────────────────────────────────
    
    def _record_history(self, event_type: str, channel: str, title: str,
                        message: str, severity: str, success: bool,
                        error_message: str, source: str):
        """Record a notification attempt in the history table."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO notification_history
                (event_type, channel, title, message, severity, sent_at, success, error_message, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event_type, channel, title, message[:500], severity,
                datetime.now().isoformat(), 1 if success else 0,
                error_message[:500] if error_message else None, source
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[NotificationManager] History record error: {e}")
    
    # ─── Public API (used by Flask routes and CLI) ──────────────
    
    def send_notification(self, event_type: str, severity: str,
                          title: str, message: str,
                          data: Optional[Dict] = None,
                          source: str = 'api') -> Dict[str, Any]:
        """Send a notification directly (bypasses queue and cooldown).
        
        Used by CLI and API for explicit sends.
        """
        if not self._channels:
            self._load_config()
        
        if not self._channels:
            return {
                'success': False,
                'error': 'No channels configured or enabled',
                'channels_sent': [],
            }
        
        # Render template if available
        if event_type in TEMPLATES and not message:
            rendered = render_template(event_type, data or {})
            title = title or rendered['title']
            message = rendered['body']
            severity = severity or rendered['severity']
        
        # AI enhancement
        ai_config = {
            'enabled': self._config.get('ai_enabled', 'false'),
            'provider': self._config.get('ai_provider', ''),
            'api_key': self._config.get('ai_api_key', ''),
            'model': self._config.get('ai_model', ''),
        }
        message = format_with_ai(title, message, severity, ai_config)
        
        results = {}
        channels_sent = []
        errors = []
        
        with self._lock:
            channels = dict(self._channels)
        
        for ch_name, channel in channels.items():
            try:
                result = channel.send(title, message, severity, data)
                results[ch_name] = result
                
                self._record_history(
                    event_type, ch_name, title, message, severity,
                    result.get('success', False),
                    result.get('error', ''),
                    source
                )
                
                if result.get('success'):
                    channels_sent.append(ch_name)
                else:
                    errors.append(f"{ch_name}: {result.get('error')}")
            except Exception as e:
                errors.append(f"{ch_name}: {str(e)}")
        
        return {
            'success': len(channels_sent) > 0,
            'channels_sent': channels_sent,
            'errors': errors,
            'total_channels': len(channels),
        }
    
    def send_raw(self, title: str, message: str,
                 severity: str = 'INFO',
                 source: str = 'api') -> Dict[str, Any]:
        """Send a raw message without template (for custom scripts)."""
        return self.send_notification(
            'custom', severity, title, message, source=source
        )
    
    def test_channel(self, channel_name: str = 'all') -> Dict[str, Any]:
        """Test one or all configured channels."""
        if not self._channels:
            self._load_config()
        
        if not self._channels:
            return {'success': False, 'error': 'No channels configured'}
        
        results = {}
        
        if channel_name == 'all':
            targets = dict(self._channels)
        elif channel_name in self._channels:
            targets = {channel_name: self._channels[channel_name]}
        else:
            # Try to create channel from config even if not enabled
            ch_config = {}
            for config_key in CHANNEL_TYPES.get(channel_name, {}).get('config_keys', []):
                ch_config[config_key] = self._config.get(f'{channel_name}.{config_key}', '')
            
            channel = create_channel(channel_name, ch_config)
            if channel:
                targets = {channel_name: channel}
            else:
                return {'success': False, 'error': f'Channel {channel_name} not configured'}
        
        for ch_name, channel in targets.items():
            success, error = channel.test()
            results[ch_name] = {'success': success, 'error': error}
            
            self._record_history(
                'test', ch_name, 'ProxMenux Test',
                'Test notification', 'INFO',
                success, error, 'api'
            )
        
        overall_success = any(r['success'] for r in results.values())
        return {
            'success': overall_success,
            'results': results,
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get current service status."""
        if not self._config:
            self._load_config()
        
        return {
            'enabled': self._enabled,
            'running': self._running,
            'channels': {
                name: {
                    'type': name,
                    'connected': True,
                }
                for name in self._channels
            },
            'stats': self._stats,
            'watchers': {
                'journal': self._journal_watcher is not None and self._running,
                'task': self._task_watcher is not None and self._running,
                'polling': self._polling_collector is not None and self._running,
            },
        }
    
    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        """Enable or disable the notification service."""
        self._save_setting('enabled', 'true' if enabled else 'false')
        self._enabled = enabled
        
        if enabled and not self._running:
            self.start()
        elif not enabled and self._running:
            self.stop()
        
        return {'success': True, 'enabled': enabled}
    
    def list_channels(self) -> Dict[str, Any]:
        """List all channel types with their configuration status."""
        if not self._config:
            self._load_config()
        
        channels_info = {}
        for ch_type, info in CHANNEL_TYPES.items():
            enabled = self._config.get(f'{ch_type}.enabled', 'false') == 'true'
            configured = all(
                bool(self._config.get(f'{ch_type}.{k}', ''))
                for k in info['config_keys']
            )
            channels_info[ch_type] = {
                'name': info['name'],
                'enabled': enabled,
                'configured': configured,
                'active': ch_type in self._channels,
            }
        
        return {'channels': channels_info}
    
    def get_history(self, limit: int = 50, offset: int = 0,
                    severity: str = '', channel: str = '') -> Dict[str, Any]:
        """Get notification history with optional filters."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = 'SELECT * FROM notification_history WHERE 1=1'
            params: list = []
            
            if severity:
                query += ' AND severity = ?'
                params.append(severity)
            if channel:
                query += ' AND channel = ?'
                params.append(channel)
            
            query += ' ORDER BY sent_at DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]
            
            # Get total count
            count_query = 'SELECT COUNT(*) FROM notification_history WHERE 1=1'
            count_params: list = []
            if severity:
                count_query += ' AND severity = ?'
                count_params.append(severity)
            if channel:
                count_query += ' AND channel = ?'
                count_params.append(channel)
            
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'history': rows,
                'total': total,
                'limit': limit,
                'offset': offset,
            }
        except Exception as e:
            return {'history': [], 'total': 0, 'error': str(e)}
    
    def clear_history(self) -> Dict[str, Any]:
        """Clear all notification history."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            conn.execute('DELETE FROM notification_history')
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_settings(self) -> Dict[str, Any]:
        """Get all notification settings for the UI."""
        if not self._config:
            self._load_config()
        
        return {
            'enabled': self._enabled,
            'settings': {f'{SETTINGS_PREFIX}{k}': v for k, v in self._config.items()},
            'channels': self.list_channels()['channels'],
            'event_groups': EVENT_GROUPS,
            'event_types': get_event_types_by_group(),
            'default_events': get_default_enabled_events(),
        }
    
    def save_settings(self, settings: Dict[str, str]) -> Dict[str, Any]:
        """Save multiple notification settings at once."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            for key, value in settings.items():
                # Accept both prefixed and unprefixed keys
                full_key = key if key.startswith(SETTINGS_PREFIX) else f'{SETTINGS_PREFIX}{key}'
                short_key = full_key[len(SETTINGS_PREFIX):]
                
                cursor.execute('''
                    INSERT OR REPLACE INTO user_settings (setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?)
                ''', (full_key, str(value), now))
                
                self._config[short_key] = str(value)
            
            conn.commit()
            conn.close()
            
            # Rebuild channels with new config
            self._enabled = self._config.get('enabled', 'false') == 'true'
            self._rebuild_channels()
            
            return {'success': True, 'channels_active': list(self._channels.keys())}
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ─── Singleton (for server mode) ─────────────────────────────────

notification_manager = NotificationManager()


# ─── CLI Interface ────────────────────────────────────────────────

def _print_result(result: Dict, as_json: bool):
    """Print CLI result in human-readable or JSON format."""
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return
    
    if result.get('success'):
        print(f"OK: ", end='')
    elif 'success' in result and not result['success']:
        print(f"ERROR: ", end='')
    
    # Format based on content
    if 'channels_sent' in result:
        sent = result.get('channels_sent', [])
        print(f"Sent via: {', '.join(sent) if sent else 'none'}")
        if result.get('errors'):
            for err in result['errors']:
                print(f"  Error: {err}")
    elif 'results' in result:
        for ch, r in result['results'].items():
            status = 'OK' if r['success'] else f"FAILED: {r['error']}"
            print(f"  {ch}: {status}")
    elif 'channels' in result:
        for ch, info in result['channels'].items():
            status = 'active' if info.get('active') else ('configured' if info.get('configured') else 'not configured')
            enabled = 'enabled' if info.get('enabled') else 'disabled'
            print(f"  {info['name']}: {enabled}, {status}")
    elif 'enabled' in result and 'running' in result:
        print(f"Enabled: {result['enabled']}, Running: {result['running']}")
        if result.get('stats'):
            stats = result['stats']
            print(f"  Total sent: {stats.get('total_sent', 0)}")
            print(f"  Total errors: {stats.get('total_errors', 0)}")
            if stats.get('last_sent_at'):
                print(f"  Last sent: {stats['last_sent_at']}")
    elif 'enabled' in result:
        print(f"Service {'enabled' if result['enabled'] else 'disabled'}")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='ProxMenux Notification Manager CLI',
        epilog='Example: python3 notification_manager.py --action send --type vm_fail --severity CRITICAL --title "VM 100 failed" --message "QEMU process crashed"'
    )
    parser.add_argument('--action', required=True,
                        choices=['send', 'send-raw', 'test', 'status',
                                 'enable', 'disable', 'list-channels'],
                        help='Action to perform')
    parser.add_argument('--type', help='Event type for send action (e.g. vm_fail, backup_complete)')
    parser.add_argument('--severity', default='INFO',
                        choices=['INFO', 'WARNING', 'CRITICAL'],
                        help='Notification severity (default: INFO)')
    parser.add_argument('--title', help='Notification title')
    parser.add_argument('--message', help='Notification message body')
    parser.add_argument('--channel', default='all',
                        help='Specific channel for test (default: all)')
    parser.add_argument('--json', action='store_true',
                        help='Output result as JSON')
    
    args = parser.parse_args()
    
    mgr = NotificationManager()
    mgr._load_config()
    
    if args.action == 'send':
        if not args.type:
            parser.error('--type is required for send action')
        result = mgr.send_notification(
            args.type, args.severity,
            args.title or '', args.message or '',
            data={
                'hostname': socket.gethostname().split('.')[0],
                'reason': args.message or '',
            },
            source='cli'
        )
    
    elif args.action == 'send-raw':
        if not args.title or not args.message:
            parser.error('--title and --message are required for send-raw')
        result = mgr.send_raw(args.title, args.message, args.severity, source='cli')
    
    elif args.action == 'test':
        result = mgr.test_channel(args.channel)
    
    elif args.action == 'status':
        result = mgr.get_status()
    
    elif args.action == 'enable':
        result = mgr.set_enabled(True)
    
    elif args.action == 'disable':
        result = mgr.set_enabled(False)
    
    elif args.action == 'list-channels':
        result = mgr.list_channels()
    
    else:
        result = {'error': f'Unknown action: {args.action}'}
    
    _print_result(result, args.json)
    
    # Exit with appropriate code
    sys.exit(0 if result.get('success', True) else 1)
