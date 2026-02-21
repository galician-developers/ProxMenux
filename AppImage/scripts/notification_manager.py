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
    JournalWatcher, TaskWatcher, PollingCollector, NotificationEvent,
    ProxmoxHookWatcher,
)


# ─── Constants ────────────────────────────────────────────────────

DB_PATH = Path('/usr/local/share/proxmenux/health_monitor.db')
SETTINGS_PREFIX = 'notification.'

# Cooldown defaults (seconds)
DEFAULT_COOLDOWNS = {
    'CRITICAL': 60,      # 60s minimum (prevents storm, delivers fast)
    'WARNING':  300,     # 5 min
    'INFO':     900,     # 15 min
    'resources': 900,    # 15 min for resource alerts
    'updates':  86400,   # 24h for update notifications
}


# ─── Storm Protection ────────────────────────────────────────────

GROUP_RATE_LIMITS = {
    'security':  {'max_per_minute': 5,  'max_per_hour': 30},
    'storage':   {'max_per_minute': 3,  'max_per_hour': 20},
    'cluster':   {'max_per_minute': 5,  'max_per_hour': 20},
    'network':   {'max_per_minute': 3,  'max_per_hour': 15},
    'resources': {'max_per_minute': 3,  'max_per_hour': 20},
    'vm_ct':     {'max_per_minute': 10, 'max_per_hour': 60},
    'backup':    {'max_per_minute': 5,  'max_per_hour': 30},
    'system':    {'max_per_minute': 5,  'max_per_hour': 30},
}


class GroupRateLimiter:
    """Rate limiter per event group. Prevents notification storms."""
    
    def __init__(self):
        from collections import deque
        self._deque = deque
        self._minute_counts: Dict[str, Any] = {}  # group -> deque[timestamp]
        self._hour_counts: Dict[str, Any] = {}    # group -> deque[timestamp]
    
    def allow(self, group: str) -> bool:
        """Check if group rate limit allows this event."""
        limits = GROUP_RATE_LIMITS.get(group, GROUP_RATE_LIMITS['system'])
        now = time.time()
        
        # Initialize if needed
        if group not in self._minute_counts:
            self._minute_counts[group] = self._deque()
            self._hour_counts[group] = self._deque()
        
        # Prune old entries
        minute_q = self._minute_counts[group]
        hour_q = self._hour_counts[group]
        while minute_q and now - minute_q[0] > 60:
            minute_q.popleft()
        while hour_q and now - hour_q[0] > 3600:
            hour_q.popleft()
        
        # Check limits
        if len(minute_q) >= limits['max_per_minute']:
            return False
        if len(hour_q) >= limits['max_per_hour']:
            return False
        
        # Record
        minute_q.append(now)
        hour_q.append(now)
        return True
    
    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Return current rate stats per group."""
        now = time.time()
        stats = {}
        for group in self._minute_counts:
            minute_q = self._minute_counts.get(group, [])
            hour_q = self._hour_counts.get(group, [])
            stats[group] = {
                'last_minute': sum(1 for t in minute_q if now - t <= 60),
                'last_hour': sum(1 for t in hour_q if now - t <= 3600),
            }
        return stats


AGGREGATION_RULES = {
    'auth_fail':     {'window': 120, 'min_count': 3,  'burst_type': 'burst_auth_fail'},
    'ip_block':      {'window': 120, 'min_count': 3,  'burst_type': 'burst_ip_block'},
    'disk_io_error': {'window': 60,  'min_count': 3,  'burst_type': 'burst_disk_io'},
    'split_brain':   {'window': 300, 'min_count': 2,  'burst_type': 'burst_cluster'},
    'node_disconnect': {'window': 300, 'min_count': 2, 'burst_type': 'burst_cluster'},
}


class BurstAggregator:
    """Accumulates similar events in a time window, then sends a single summary.
    
    Examples:
    - "Fail2Ban banned 17 IPs in 2 minutes"
    - "Disk I/O errors: 34 events on /dev/sdb in 60s"
    """
    
    def __init__(self):
        self._buckets: Dict[str, List] = {}         # bucket_key -> [events]
        self._deadlines: Dict[str, float] = {}      # bucket_key -> flush_deadline
        self._lock = threading.Lock()
    
    def ingest(self, event: NotificationEvent) -> Optional[NotificationEvent]:
        """Add event to aggregation. Returns:
        - None if event is being buffered (wait for window)
        - Original event if not eligible for aggregation
        """
        rule = AGGREGATION_RULES.get(event.event_type)
        if not rule:
            return event  # Not aggregable, pass through
        
        bucket_key = f"{event.event_type}:{event.data.get('hostname', '')}"
        
        with self._lock:
            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = []
                self._deadlines[bucket_key] = time.time() + rule['window']
            
            self._buckets[bucket_key].append(event)
            
            # First event in bucket: pass through immediately so user gets fast alert
            if len(self._buckets[bucket_key]) == 1:
                return event
            
            # Subsequent events: buffer (will be flushed as summary)
            return None
    
    def flush_expired(self) -> List[NotificationEvent]:
        """Flush all buckets past their deadline. Returns summary events."""
        now = time.time()
        summaries = []
        
        with self._lock:
            expired_keys = [k for k, d in self._deadlines.items() if now >= d]
            
            for key in expired_keys:
                events = self._buckets.pop(key, [])
                del self._deadlines[key]
                
                if len(events) < 2:
                    continue  # Single event already sent on ingest, no summary needed
                
                rule_type = key.split(':')[0]
                rule = AGGREGATION_RULES.get(rule_type, {})
                min_count = rule.get('min_count', 2)
                
                if len(events) < min_count:
                    continue  # Not enough events for a summary
                
                summary = self._create_summary(events, rule)
                if summary:
                    summaries.append(summary)
        
        return summaries
    
    def _create_summary(self, events: List[NotificationEvent],
                        rule: dict) -> Optional[NotificationEvent]:
        """Create a single summary event from multiple events."""
        if not events:
            return None
        
        first = events[0]
        # Determine highest severity
        sev_order = {'INFO': 0, 'WARNING': 1, 'CRITICAL': 2}
        max_severity = max(events, key=lambda e: sev_order.get(e.severity, 0)).severity
        
        # Collect unique entity_ids
        entity_ids = list(set(e.entity_id for e in events if e.entity_id))
        entity_list = ', '.join(entity_ids[:10]) if entity_ids else 'multiple sources'
        if len(entity_ids) > 10:
            entity_list += f' (+{len(entity_ids) - 10} more)'
        
        # Calculate window
        window_secs = events[-1].ts_epoch - events[0].ts_epoch
        if window_secs < 120:
            window_str = f'{int(window_secs)}s'
        else:
            window_str = f'{int(window_secs / 60)}m'
        
        burst_type = rule.get('burst_type', 'burst_generic')
        
        data = {
            'hostname': first.data.get('hostname', socket.gethostname()),
            'count': str(len(events)),
            'window': window_str,
            'entity_list': entity_list,
            'event_type': first.event_type,
        }
        
        return NotificationEvent(
            event_type=burst_type,
            severity=max_severity,
            data=data,
            source='aggregator',
            entity=first.entity,
            entity_id='burst',
        )


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
        
        # Webhook receiver (no thread, passive)
        self._hook_watcher: Optional[ProxmoxHookWatcher] = None
        
        # Cooldown tracking: {fingerprint: last_sent_timestamp}
        self._cooldowns: Dict[str, float] = {}
        
        # Storm protection
        self._group_limiter = GroupRateLimiter()
        self._aggregator = BurstAggregator()
        self._aggregation_thread: Optional[threading.Thread] = None
        
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
        
        # Reconcile per-event toggles with current template defaults.
        # If a template's default_enabled was changed (e.g. state_change False),
        # but the DB has a stale 'true' from a previous default, fix it now.
        # Only override if the user hasn't explicitly set it (we track this with
        # a sentinel: if the value came from auto-save of defaults, it may be stale).
        for event_type, tmpl in TEMPLATES.items():
            key = f'event.{event_type}'
            if key in self._config:
                db_val = self._config[key] == 'true'
                tmpl_default = tmpl.get('default_enabled', True)
                # If template says disabled but DB says enabled, AND there's no
                # explicit user marker, enforce the template default.
                if not tmpl_default and db_val:
                    # Check if user explicitly enabled it (look for a marker)
                    marker = f'event_explicit.{event_type}'
                    if marker not in self._config:
                        self._config[key] = 'false'
        
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
        self._load_cooldowns_from_db()
        
        if not self._enabled:
            print("[NotificationManager] Service is disabled. Skipping start.")
            return
        
        self._running = True
        self._stats['started_at'] = datetime.now().isoformat()
        
        # Ensure PVE webhook is configured (repairs priv config if missing)
        try:
            from flask_notification_routes import setup_pve_webhook_core
            wh_result = setup_pve_webhook_core()
            if wh_result.get('configured'):
                print("[NotificationManager] PVE webhook configured OK.")
            elif wh_result.get('error'):
                print(f"[NotificationManager] PVE webhook warning: {wh_result['error']}")
        except ImportError:
            pass  # flask_notification_routes not loaded yet (early startup)
        except Exception as e:
            print(f"[NotificationManager] PVE webhook setup error: {e}")
        
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
        last_cleanup = time.monotonic()
        last_flush = time.monotonic()
        cleanup_interval = 3600  # Cleanup cooldowns every hour
        flush_interval = 5       # Flush aggregation buckets every 5s
        
        while self._running:
            try:
                event = self._event_queue.get(timeout=2)
            except Empty:
                # Periodic maintenance during idle
                now_mono = time.monotonic()
                if now_mono - last_cleanup > cleanup_interval:
                    self._cleanup_old_cooldowns()
                    last_cleanup = now_mono
                # Flush expired aggregation buckets
                if now_mono - last_flush > flush_interval:
                    self._flush_aggregation()
                    last_flush = now_mono
                continue
            
            try:
                self._process_event(event)
            except Exception as e:
                print(f"[NotificationManager] Dispatch error: {e}")
            
            # Also flush aggregation after each event
            if time.monotonic() - last_flush > flush_interval:
                self._flush_aggregation()
                last_flush = time.monotonic()
    
    def _flush_aggregation(self):
        """Flush expired aggregation buckets and dispatch summaries."""
        try:
            summaries = self._aggregator.flush_expired()
            for summary_event in summaries:
                # Burst summaries bypass aggregator but still pass cooldown + rate limit
                self._process_event_direct(summary_event)
        except Exception as e:
            print(f"[NotificationManager] Aggregation flush error: {e}")
    
    def _process_event(self, event: NotificationEvent):
        """Process a single event: filter -> aggregate -> cooldown -> rate limit -> dispatch."""
        if not self._enabled:
            return
        
        # Track fingerprint for cross-source dedup (webhook vs tasks/journal).
        # The webhook handler checks this dict to skip events already
        # processed from tasks or journal watchers within 60s.
        import time
        if not hasattr(self, '_recent_fingerprints'):
            self._recent_fingerprints = {}
        self._recent_fingerprints[event.fingerprint] = time.time()
        # Cleanup old entries
        if len(self._recent_fingerprints) > 500:
            cutoff = time.time() - 120
            self._recent_fingerprints = {
                k: v for k, v in self._recent_fingerprints.items() if v > cutoff
            }
        
        # Check if this event's GROUP is enabled in settings.
        # The UI saves categories by group key: events.vm_ct, events.backup, etc.
        template = TEMPLATES.get(event.event_type, {})
        event_group = template.get('group', 'system')
        group_setting = f'events.{event_group}'
        if self._config.get(group_setting, 'true') == 'false':
            return
        
        # Check if this SPECIFIC event type is enabled (granular per-event toggle).
        # Key format: event.{event_type} = "true"/"false"
        # Default comes from the template's default_enabled field.
        default_enabled = 'true' if template.get('default_enabled', True) else 'false'
        event_specific = f'event.{event.event_type}'
        if self._config.get(event_specific, default_enabled) == 'false':
            return
        
        # Check severity filter.
        # The UI saves severity_filter as: "all", "warning", "critical".
        # Map to our internal severity names for comparison.
        severity_map = {'all': 'INFO', 'warning': 'WARNING', 'critical': 'CRITICAL'}
        raw_filter = self._config.get('severity_filter', 'all')
        min_severity = severity_map.get(raw_filter.lower(), 'INFO')
        if not self._meets_severity(event.severity, min_severity):
            return
        
        # Try aggregation (may buffer the event)
        result = self._aggregator.ingest(event)
        if result is None:
            return  # Buffered, will be flushed as summary later
        event = result  # Use original event (first in burst passes through)
        
        # From here, proceed with dispatch (shared with _process_event_direct)
        self._dispatch_event(event)
    
    def _process_event_direct(self, event: NotificationEvent):
        """Process a burst summary event. Bypasses aggregator but applies ALL other filters."""
        if not self._enabled:
            return
        
        # Check group filter (same as _process_event)
        template = TEMPLATES.get(event.event_type, {})
        event_group = template.get('group', 'system')
        group_setting = f'events.{event_group}'
        if self._config.get(group_setting, 'true') == 'false':
            return
        
        # Check per-event filter (same as _process_event)
        default_enabled = 'true' if template.get('default_enabled', True) else 'false'
        event_specific = f'event.{event.event_type}'
        if self._config.get(event_specific, default_enabled) == 'false':
            return
        
        # Check severity filter (same mapping as _process_event)
        severity_map = {'all': 'INFO', 'warning': 'WARNING', 'critical': 'CRITICAL'}
        raw_filter = self._config.get('severity_filter', 'all')
        min_severity = severity_map.get(raw_filter.lower(), 'INFO')
        if not self._meets_severity(event.severity, min_severity):
            return
        
        self._dispatch_event(event)
    
    def _dispatch_event(self, event: NotificationEvent):
        """Shared dispatch pipeline: cooldown -> rate limit -> render -> send."""
        # Check cooldown
        if not self._check_cooldown(event):
            return
        
        # Check group rate limit
        template = TEMPLATES.get(event.event_type, {})
        group = template.get('group', 'system')
        if not self._group_limiter.allow(group):
            return
        
        # Use the properly mapped severity from the event, not from template defaults.
        # event.severity was set by _map_severity which normalises to CRITICAL/WARNING/INFO.
        severity = event.severity
        
        # Inject the canonical severity into data so templates see it too.
        event.data['severity'] = severity
        
        # Render message from template (structured output)
        rendered = render_template(event.event_type, event.data)
        
        # Optional AI enhancement (on text body only)
        ai_config = {
            'enabled': self._config.get('ai_enabled', 'false'),
            'provider': self._config.get('ai_provider', ''),
            'api_key': self._config.get('ai_api_key', ''),
            'model': self._config.get('ai_model', ''),
        }
        body = format_with_ai(
            rendered['title'], rendered['body'], severity, ai_config
        )
        
        # Enrich data with structured fields for channels that support them
        enriched_data = dict(event.data)
        enriched_data['_rendered_fields'] = rendered.get('fields', [])
        enriched_data['_body_html'] = rendered.get('body_html', '')
        
        # Send through all active channels
        self._dispatch_to_channels(
            rendered['title'], body, severity,
            event.event_type, enriched_data, event.source
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
        
        # CRITICAL events: 60s minimum cooldown (prevents storm, but delivers fast)
        if event.severity == 'CRITICAL' and cooldown_str is None:
            cooldown = 60
        
        # Check against last sent time using stable fingerprint
        last_sent = self._cooldowns.get(event.fingerprint, 0)
        
        if now - last_sent < cooldown:
            return False
        
        self._cooldowns[event.fingerprint] = now
        self._persist_cooldown(event.fingerprint, now)
        return True
    
    def _load_cooldowns_from_db(self):
        """Load persistent cooldown state from SQLite (up to 48h)."""
        try:
            if not DB_PATH.exists():
                return
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            cursor = conn.cursor()
            cursor.execute('SELECT fingerprint, last_sent_ts FROM notification_last_sent')
            now = time.time()
            for fp, ts in cursor.fetchall():
                if now - ts < 172800:  # 48h window
                    self._cooldowns[fp] = ts
            conn.close()
        except Exception as e:
            print(f"[NotificationManager] Failed to load cooldowns: {e}")
    
    def _persist_cooldown(self, fingerprint: str, ts: float):
        """Save cooldown timestamp to SQLite for restart persistence."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
            conn.execute('''
                INSERT OR REPLACE INTO notification_last_sent (fingerprint, last_sent_ts, count)
                VALUES (?, ?, COALESCE(
                    (SELECT count + 1 FROM notification_last_sent WHERE fingerprint = ?), 1
                ))
            ''', (fingerprint, int(ts), fingerprint))
            conn.commit()
            conn.close()
        except Exception:
            pass  # Non-critical, in-memory cooldown still works
    
    def _cleanup_old_cooldowns(self):
        """Remove cooldown entries older than 48h from both memory and DB."""
        cutoff = time.time() - 172800  # 48h
        self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > cutoff}
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('DELETE FROM notification_last_sent WHERE last_sent_ts < ?', (int(cutoff),))
            conn.commit()
            conn.close()
        except Exception:
            pass
    
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
    
    # ─── Proxmox Webhook ──────────────────────────────────────────
    
    def process_webhook(self, payload: dict) -> dict:
        """Process incoming Proxmox webhook. Delegates to ProxmoxHookWatcher."""
        if not self._hook_watcher:
            self._hook_watcher = ProxmoxHookWatcher(self._event_queue)
            self._hook_watcher._pipeline = self  # For cross-source dedup
        return self._hook_watcher.process_webhook(payload)
    
    def get_webhook_secret(self) -> str:
        """Get configured webhook secret, or empty string if none."""
        if not self._config:
            self._load_config()
        return self._config.get('webhook_secret', '')
    
    def get_webhook_allowed_ips(self) -> list:
        """Get list of allowed IPs for webhook, or empty list (allow all)."""
        if not self._config:
            self._load_config()
        raw = self._config.get('webhook_allowed_ips', '')
        if not raw:
            return []
        return [ip.strip() for ip in str(raw).split(',') if ip.strip()]
    
    # ─── Status & Settings ──────────────────────────────────────
    
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
        """Get all notification settings for the UI.
        
        Returns a structure matching the frontend's NotificationConfig shape
        so the round-trip (GET -> edit -> POST) is seamless.
        """
        if not self._config:
            self._load_config()
        
        # Build nested channels object matching frontend ChannelConfig
        channels = {}
        for ch_type, info in CHANNEL_TYPES.items():
            ch_cfg: Dict[str, Any] = {
                'enabled': self._config.get(f'{ch_type}.enabled', 'false') == 'true',
            }
            for config_key in info['config_keys']:
                ch_cfg[config_key] = self._config.get(f'{ch_type}.{config_key}', '')
            channels[ch_type] = ch_cfg
        
        # Build event_categories dict (group-level toggle)
        # EVENT_GROUPS is a dict: { 'system': {...}, 'vm_ct': {...}, ... }
        event_categories = {}
        for group_key in EVENT_GROUPS:
            event_categories[group_key] = self._config.get(f'events.{group_key}', 'true') == 'true'
        
        # Build per-event toggles: { 'vm_start': true, 'vm_stop': false, ... }
        event_toggles = {}
        for event_type, tmpl in TEMPLATES.items():
            default = tmpl.get('default_enabled', True)
            saved = self._config.get(f'event.{event_type}', None)
            if saved is not None:
                event_toggles[event_type] = saved == 'true'
            else:
                event_toggles[event_type] = default
        
        # Build event_types_by_group for UI rendering
        event_types_by_group = get_event_types_by_group()
        
        config = {
            'enabled': self._enabled,
            'channels': channels,
            'severity_filter': self._config.get('severity_filter', 'all'),
            'event_categories': event_categories,
            'event_toggles': event_toggles,
            'event_types_by_group': event_types_by_group,
            'ai_enabled': self._config.get('ai_enabled', 'false') == 'true',
            'ai_provider': self._config.get('ai_provider', 'openai'),
            'ai_api_key': self._config.get('ai_api_key', ''),
            'ai_model': self._config.get('ai_model', ''),
            'hostname': self._config.get('hostname', ''),
            'webhook_secret': self._config.get('webhook_secret', ''),
            'webhook_allowed_ips': self._config.get('webhook_allowed_ips', ''),
            'pbs_host': self._config.get('pbs_host', ''),
            'pve_host': self._config.get('pve_host', ''),
            'pbs_trusted_sources': self._config.get('pbs_trusted_sources', ''),
        }
        
        return {
            'success': True,
            'config': config,
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
                
                # If user is explicitly enabling an event that defaults to disabled,
                # mark it so _load_config reconciliation won't override it later.
                if short_key.startswith('event.') and str(value) == 'true':
                    event_type = short_key[6:]  # strip 'event.'
                    tmpl = TEMPLATES.get(event_type, {})
                    if not tmpl.get('default_enabled', True):
                        marker_key = f'{SETTINGS_PREFIX}event_explicit.{event_type}'
                        cursor.execute('''
                            INSERT OR REPLACE INTO user_settings (setting_key, setting_value, updated_at)
                            VALUES (?, ?, ?)
                        ''', (marker_key, 'true', now))
                        self._config[f'event_explicit.{event_type}'] = 'true'
            
            conn.commit()
            conn.close()
            
            # Rebuild channels with new config
            was_enabled = self._enabled
            self._enabled = self._config.get('enabled', 'false') == 'true'
            self._rebuild_channels()
            
            # Start/stop service and auto-configure PVE webhook
            pve_webhook_result = None
            if self._enabled and not was_enabled:
                # Notifications just got ENABLED -> start service + setup PVE webhook
                if not self._running:
                    self.start()
                try:
                    from flask_notification_routes import setup_pve_webhook_core
                    pve_webhook_result = setup_pve_webhook_core()
                except ImportError:
                    pass  # flask_notification_routes not available (CLI mode)
                except Exception as e:
                    pve_webhook_result = {'configured': False, 'error': str(e)}
            elif not self._enabled and was_enabled:
                # Notifications just got DISABLED -> stop service + cleanup PVE webhook
                if self._running:
                    self.stop()
                try:
                    from flask_notification_routes import cleanup_pve_webhook_core
                    cleanup_pve_webhook_core()
                except ImportError:
                    pass
                except Exception:
                    pass
            
            result = {'success': True, 'channels_active': list(self._channels.keys())}
            if pve_webhook_result:
                result['pve_webhook'] = pve_webhook_result
            return result
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
