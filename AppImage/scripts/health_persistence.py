"""
Health Monitor Persistence Module
Manages persistent error tracking across AppImage updates using SQLite.
Stores errors in /usr/local/share/proxmenux/health_monitor.db
(same directory as monitor.db for temperature history)

Features:
- Persistent error storage (survives AppImage updates)
- Smart error resolution (auto-clear when VM starts, or after 48h)
- Event system for future Telegram notifications
- Manual acknowledgment support

Author: MacRimi
Version: 1.1
"""

import sqlite3
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

class HealthPersistence:
    """Manages persistent health error tracking"""
    
    # Default suppression duration when no user setting exists for a category.
    # Users override per-category via the Suppression Duration settings UI.
    DEFAULT_SUPPRESSION_HOURS = 24
    
    # Mapping from error categories to settings keys
    CATEGORY_SETTING_MAP = {
        'temperature': 'suppress_cpu',
        'memory': 'suppress_memory',
        'storage': 'suppress_storage',
        'disks': 'suppress_disks',
        'network': 'suppress_network',
        'vms': 'suppress_vms',
        'pve_services': 'suppress_pve_services',
        'logs': 'suppress_logs',
        'updates': 'suppress_updates',
        'security': 'suppress_security',
    }
    
    def __init__(self):
        """Initialize persistence with database in shared ProxMenux data directory"""
        self.data_dir = Path('/usr/local/share/proxmenux')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = self.data_dir / 'health_monitor.db'
        self._db_lock = threading.Lock()
        self._init_database()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection with timeout and WAL mode for safe concurrency."""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=5000')
        return conn
    
    def _init_database(self):
        """Initialize SQLite database with required tables"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Errors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_key TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                reason TEXT NOT NULL,
                details TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                resolved_at TEXT,
                acknowledged INTEGER DEFAULT 0,
                notification_sent INTEGER DEFAULT 0
            )
        ''')
        
        # Events table (for future Telegram notifications)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                error_key TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                data TEXT
            )
        ''')
        
        # System capabilities table (detected once, cached forever)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_capabilities (
                cap_key TEXT PRIMARY KEY,
                cap_value TEXT NOT NULL,
                detected_at TEXT NOT NULL
            )
        ''')
        
        # User settings table (per-category suppression durations, etc.)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # Notification history table (records all sent notifications)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                channel TEXT NOT NULL,
                title TEXT,
                message TEXT,
                severity TEXT,
                sent_at TEXT NOT NULL,
                success INTEGER DEFAULT 1,
                error_message TEXT,
                source TEXT DEFAULT 'server'
            )
        ''')
        
        # Notification cooldown persistence (survives restarts)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_last_sent (
                fingerprint TEXT PRIMARY KEY,
                last_sent_ts INTEGER NOT NULL,
                count INTEGER DEFAULT 1
            )
        ''')
        
        # Migration: add suppression_hours column to errors if not present
        cursor.execute("PRAGMA table_info(errors)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'suppression_hours' not in columns:
            cursor.execute('ALTER TABLE errors ADD COLUMN suppression_hours INTEGER DEFAULT 24')
        
        # Indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_error_key ON errors(error_key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON errors(category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_resolved ON errors(resolved_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_error ON events(error_key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_sent_at ON notification_history(sent_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_severity ON notification_history(severity)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_nls_ts ON notification_last_sent(last_sent_ts)')
        
        # ── Disk Observations System ──
        # Registry of all physical disks seen by the system
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS disk_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL,
                serial TEXT,
                model TEXT,
                size_bytes INTEGER,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                removed INTEGER DEFAULT 0,
                worst_health TEXT DEFAULT 'healthy',
                worst_health_date TEXT,
                worst_health_reason TEXT,
                admin_cleared INTEGER DEFAULT 0,
                admin_cleared_date TEXT,
                admin_cleared_note TEXT,
                UNIQUE(device_name, serial)
            )
        ''')
        
        # Observation log: deduplicated error events per disk
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS disk_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                disk_registry_id INTEGER NOT NULL,
                error_type TEXT NOT NULL,
                error_signature TEXT NOT NULL,
                first_occurrence TEXT NOT NULL,
                last_occurrence TEXT NOT NULL,
                occurrence_count INTEGER DEFAULT 1,
                raw_message TEXT,
                severity TEXT DEFAULT 'warning',
                dismissed INTEGER DEFAULT 0,
                FOREIGN KEY(disk_registry_id) REFERENCES disk_registry(id),
                UNIQUE(disk_registry_id, error_type, error_signature)
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_disk_serial ON disk_registry(serial)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_disk_device ON disk_registry(device_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_obs_disk ON disk_observations(disk_registry_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_obs_dismissed ON disk_observations(dismissed)')
        
        # Migration: add worst_health columns to disk_registry if not present
        cursor.execute("PRAGMA table_info(disk_registry)")
        disk_columns = [col[1] for col in cursor.fetchall()]
        if 'worst_health' not in disk_columns:
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN worst_health TEXT DEFAULT 'healthy'")
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN worst_health_date TEXT")
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN worst_health_reason TEXT")
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN admin_cleared INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN admin_cleared_date TEXT")
            cursor.execute("ALTER TABLE disk_registry ADD COLUMN admin_cleared_note TEXT")
        
        conn.commit()
        conn.close()
    
    def record_error(self, error_key: str, category: str, severity: str, 
                    reason: str, details: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Record or update an error.
        Returns event info (new_error, updated, etc.)
        """
        with self._db_lock:
            return self._record_error_impl(error_key, category, severity, reason, details)
    
    def _record_error_impl(self, error_key, category, severity, reason, details):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        details_json = json.dumps(details) if details else None
        
        cursor.execute('''
            SELECT id, acknowledged, resolved_at, category, severity, first_seen, 
                   notification_sent, suppression_hours
            FROM errors WHERE error_key = ?
        ''', (error_key,))
        existing = cursor.fetchone()
        
        event_info = {'type': 'updated', 'needs_notification': False}
        
        if existing:
            err_id, ack, resolved_at, old_cat, old_severity, first_seen, notif_sent, stored_suppression = existing
            
            if ack == 1:
                # SAFETY OVERRIDE: Critical CPU temperature ALWAYS re-triggers
                # regardless of any dismiss/permanent setting (hardware protection)
                if error_key == 'cpu_temperature' and severity == 'CRITICAL':
                    cursor.execute('DELETE FROM errors WHERE error_key = ?', (error_key,))
                    cursor.execute('''
                        INSERT INTO errors 
                        (error_key, category, severity, reason, details, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (error_key, category, severity, reason, details_json, now, now))
                    event_info = {'type': 'new', 'needs_notification': True}
                    self._record_event(cursor, 'new', error_key, 
                                      {'severity': severity, 'reason': reason,
                                       'note': 'CRITICAL temperature override - safety alert'})
                    conn.commit()
                    conn.close()
                    return event_info
                
                # Check suppression: use per-record stored hours (set at dismiss time)
                sup_hours = stored_suppression if stored_suppression is not None else self.DEFAULT_SUPPRESSION_HOURS
                
                # Permanent dismiss (sup_hours == -1): always suppress
                if sup_hours == -1:
                    conn.close()
                    return {'type': 'skipped_acknowledged', 'needs_notification': False}
                
                # Time-limited suppression
                still_suppressed = False
                if resolved_at:
                    try:
                        resolved_dt = datetime.fromisoformat(resolved_at)
                        elapsed_hours = (datetime.now() - resolved_dt).total_seconds() / 3600
                        still_suppressed = elapsed_hours < sup_hours
                    except Exception:
                        pass
                
                if still_suppressed:
                    conn.close()
                    return {'type': 'skipped_acknowledged', 'needs_notification': False}
                else:
                    # Suppression expired.
                    # For log-based errors (spike, persistent, cascade),
                    # do NOT re-trigger.  The journal always contains old
                    # messages, so re-creating the error would cause an
                    # infinite notification cycle.  Instead, just delete
                    # the stale record so it stops appearing in the UI.
                    is_log_error = (
                        error_key.startswith('log_persistent_')
                        or error_key.startswith('log_spike_')
                        or error_key.startswith('log_cascade_')
                        or error_key.startswith('log_critical_')
                        or category == 'logs'
                    )
                    if is_log_error:
                        cursor.execute('DELETE FROM errors WHERE error_key = ?', (error_key,))
                        conn.commit()
                        conn.close()
                        return {'type': 'skipped_expired_log', 'needs_notification': False}
                    
                    # For non-log errors (hardware, services, etc.),
                    # re-triggering is correct -- the condition is real
                    # and still present.
                    cursor.execute('DELETE FROM errors WHERE error_key = ?', (error_key,))
                    cursor.execute('''
                        INSERT INTO errors 
                        (error_key, category, severity, reason, details, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (error_key, category, severity, reason, details_json, now, now))
                    event_info = {'type': 'new', 'needs_notification': True}
                    self._record_event(cursor, 'new', error_key, 
                                      {'severity': severity, 'reason': reason,
                                       'note': 'Re-triggered after suppression expired'})
                    conn.commit()
                    conn.close()
                    return event_info
            
            # Not acknowledged - update existing active error
            cursor.execute('''
                UPDATE errors 
                SET last_seen = ?, severity = ?, reason = ?, details = ?
                WHERE error_key = ? AND acknowledged = 0
            ''', (now, severity, reason, details_json, error_key))
            
            # Check if severity escalated
            if old_severity == 'WARNING' and severity == 'CRITICAL':
                event_info['type'] = 'escalated'
                event_info['needs_notification'] = True
        else:
            # Insert new error
            cursor.execute('''
                INSERT INTO errors 
                (error_key, category, severity, reason, details, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (error_key, category, severity, reason, details_json, now, now))
            
            event_info['type'] = 'new'
            event_info['needs_notification'] = True
        
        # ─── Auto-suppress: if the category has a non-default setting, ───
        # auto-dismiss immediately so the user never sees it as active.
        # Exception: CRITICAL CPU temperature is never auto-suppressed.
        if not (error_key == 'cpu_temperature' and severity == 'CRITICAL'):
            setting_key = self.CATEGORY_SETTING_MAP.get(category, '')
            if setting_key:
                stored = self.get_setting(setting_key)
                if stored is not None:
                    configured_hours = int(stored)
                    if configured_hours != self.DEFAULT_SUPPRESSION_HOURS:
                        # Non-default setting found: auto-acknowledge
                        cursor.execute('''
                            UPDATE errors 
                            SET acknowledged = 1, resolved_at = ?, suppression_hours = ?
                            WHERE error_key = ? AND acknowledged = 0
                        ''', (now, configured_hours, error_key))
                        
                        if cursor.rowcount > 0:
                            self._record_event(cursor, 'auto_suppressed', error_key, {
                                'severity': severity,
                                'reason': reason,
                                'suppression_hours': configured_hours,
                                'note': 'Auto-suppressed by user settings'
                            })
                            event_info['type'] = 'auto_suppressed'
                            event_info['needs_notification'] = False
                            conn.commit()
                            conn.close()
                            return event_info
        
        # Record event
        self._record_event(cursor, event_info['type'], error_key, 
                          {'severity': severity, 'reason': reason})
        
        conn.commit()
        conn.close()
        
        return event_info
    
    def resolve_error(self, error_key: str, reason: str = 'auto-resolved'):
        """Mark an error as resolved"""
        with self._db_lock:
            return self._resolve_error_impl(error_key, reason)
    
    def _resolve_error_impl(self, error_key, reason):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        
        cursor.execute('''
            UPDATE errors 
            SET resolved_at = ?
            WHERE error_key = ? AND resolved_at IS NULL
        ''', (now, error_key))
        
        if cursor.rowcount > 0:
            self._record_event(cursor, 'resolved', error_key, {'reason': reason})
        
        conn.commit()
        conn.close()
    
    def is_error_active(self, error_key: str, category: Optional[str] = None) -> bool:
        """
        Check if an error is currently active (unresolved and not acknowledged).
        Used by checks to avoid re-recording errors that are already tracked.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if category:
            cursor.execute('''
                SELECT COUNT(*) FROM errors 
                WHERE error_key = ? AND category = ?
                  AND resolved_at IS NULL AND acknowledged = 0
            ''', (error_key, category))
        else:
            cursor.execute('''
                SELECT COUNT(*) FROM errors 
                WHERE error_key = ? 
                  AND resolved_at IS NULL AND acknowledged = 0
            ''', (error_key,))
        
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    def clear_error(self, error_key: str):
        """
        Remove/resolve a specific error immediately.
        Used when the condition that caused the error no longer exists
        (e.g., storage became available again, CPU temp recovered).
        
        For acknowledged errors: if the condition resolved on its own,
        we delete the record entirely so it can re-trigger as a fresh
        event if the condition returns later.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        
        # Check if this error was acknowledged (dismissed)
        cursor.execute('''
            SELECT acknowledged FROM errors WHERE error_key = ?
        ''', (error_key,))
        row = cursor.fetchone()
        
        if row and row[0] == 1:
            # Dismissed error that naturally resolved - delete entirely
            # so it can re-trigger as a new event if it happens again
            cursor.execute('DELETE FROM errors WHERE error_key = ?', (error_key,))
            if cursor.rowcount > 0:
                self._record_event(cursor, 'cleared', error_key, 
                                  {'reason': 'condition_resolved_after_dismiss'})
        else:
            # Normal active error - mark as resolved
            cursor.execute('''
                UPDATE errors 
                SET resolved_at = ?
                WHERE error_key = ? AND resolved_at IS NULL
            ''', (now, error_key))
            
            if cursor.rowcount > 0:
                self._record_event(cursor, 'cleared', error_key, {'reason': 'condition_resolved'})
        
        conn.commit()
        conn.close()
    
    def acknowledge_error(self, error_key: str) -> Dict[str, Any]:
        """
        Manually acknowledge an error (dismiss).
        - Looks up the category's configured suppression duration from user settings
        - Stores suppression_hours on the error record (snapshot at dismiss time)
        - Marks as acknowledged so it won't re-appear during the suppression period
        """
        with self._db_lock:
            return self._acknowledge_error_impl(error_key)
    
    def _acknowledge_error_impl(self, error_key):
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        
        # Get current error info before acknowledging
        cursor.execute('SELECT * FROM errors WHERE error_key = ?', (error_key,))
        row = cursor.fetchone()
        
        result = {'success': False, 'error_key': error_key}
        
        if not row:
            # Error not in DB yet -- create a minimal record so the dismiss persists.
            # Try to infer category from the error_key prefix.
            category = ''
            # Order matters: more specific prefixes MUST come before shorter ones
            # e.g. 'security_updates' (updates) before 'security_' (security)
            for cat, prefix in [('updates', 'security_updates'), ('updates', 'system_age'),
                                ('updates', 'pending_updates'), ('updates', 'kernel_pve'),
                                ('security', 'security_'), 
                                ('pve_services', 'pve_service_'), ('vms', 'vmct_'), ('vms', 'vm_'), ('vms', 'ct_'),
                                ('disks', 'disk_'), ('disks', 'smart_'), ('disks', 'zfs_pool_'),
                                ('logs', 'log_'), ('network', 'net_'),
                                ('temperature', 'temp_')]:
                if error_key == prefix or error_key.startswith(prefix):
                    category = cat
                    break
            
            setting_key = self.CATEGORY_SETTING_MAP.get(category, '')
            sup_hours = self.DEFAULT_SUPPRESSION_HOURS
            if setting_key:
                stored = self.get_setting(setting_key)
                if stored is not None:
                    try:
                        sup_hours = int(stored)
                    except (ValueError, TypeError):
                        pass
            
            cursor.execute('''
                INSERT INTO errors (error_key, category, severity, reason, first_seen, last_seen,
                                    occurrence_count, acknowledged, resolved_at, suppression_hours)
                VALUES (?, ?, 'WARNING', 'Dismissed by user', ?, ?, 1, 1, ?, ?)
            ''', (error_key, category, now, now, now, sup_hours))
            
            self._record_event(cursor, 'acknowledged', error_key, {
                'original_severity': 'WARNING',
                'category': category,
                'suppression_hours': sup_hours
            })
            
            result = {
                'success': True,
                'error_key': error_key,
                'original_severity': 'WARNING',
                'category': category,
                'suppression_hours': sup_hours,
                'acknowledged_at': now
            }
            conn.commit()
            conn.close()
            return result
        
        if row:
            error_dict = dict(row)
            original_severity = error_dict.get('severity', 'WARNING')
            category = error_dict.get('category', '')
            
            # Look up the user's configured suppression for this category
            setting_key = self.CATEGORY_SETTING_MAP.get(category, '')
            sup_hours = self.DEFAULT_SUPPRESSION_HOURS
            if setting_key:
                stored = self.get_setting(setting_key)
                if stored is not None:
                    try:
                        sup_hours = int(stored)
                    except (ValueError, TypeError):
                        pass
            
            cursor.execute('''
                UPDATE errors 
                SET acknowledged = 1, resolved_at = ?, suppression_hours = ?
                WHERE error_key = ?
            ''', (now, sup_hours, error_key))
            
            self._record_event(cursor, 'acknowledged', error_key, {
                'original_severity': original_severity,
                'category': category,
                'suppression_hours': sup_hours
            })
            
            # Cascade acknowledge: when dismissing a group check
            # (e.g. log_persistent_errors), also dismiss all individual
            # sub-errors that share the same prefix in the DB.
            # Currently only persistent errors have per-pattern sub-records
            # (e.g. log_persistent_a1b2c3d4).
            CASCADE_PREFIXES = {
                'log_persistent_errors': 'log_persistent_',
            }
            child_prefix = CASCADE_PREFIXES.get(error_key)
            if child_prefix:
                # Only cascade to active (unresolved) child errors.
                # Already-resolved/expired entries must NOT be re-surfaced.
                cursor.execute('''
                    UPDATE errors 
                    SET acknowledged = 1, resolved_at = ?, suppression_hours = ?
                    WHERE error_key LIKE ? AND acknowledged = 0 AND resolved_at IS NULL
                ''', (now, sup_hours, child_prefix + '%'))
            
            result = {
                'success': True,
                'error_key': error_key,
                'original_severity': original_severity,
                'category': category,
                'acknowledged_at': now,
                'suppression_hours': sup_hours
            }
        
        conn.commit()
        conn.close()
        return result
    
    def is_error_acknowledged(self, error_key: str) -> bool:
        """Check if an error_key has been acknowledged and is still within suppression window."""
        try:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                'SELECT acknowledged, resolved_at, suppression_hours FROM errors WHERE error_key = ?',
                (error_key,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return False
            if not row['acknowledged']:
                return False
            # Check if still within suppression window
            resolved_at = row['resolved_at']
            sup_hours = row['suppression_hours'] or self.DEFAULT_SUPPRESSION_HOURS
            if resolved_at:
                try:
                    resolved_dt = datetime.fromisoformat(resolved_at)
                    if datetime.now() > resolved_dt + timedelta(hours=sup_hours):
                        return False  # Suppression expired
                except Exception:
                    pass
            return True
        except Exception:
            return False
    
    def get_active_errors(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all active (unresolved) errors, optionally filtered by category"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if category:
            cursor.execute('''
                SELECT * FROM errors 
                WHERE resolved_at IS NULL AND category = ?
                ORDER BY severity DESC, last_seen DESC
            ''', (category,))
        else:
            cursor.execute('''
                SELECT * FROM errors 
                WHERE resolved_at IS NULL
                ORDER BY severity DESC, last_seen DESC
            ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        errors = []
        for row in rows:
            error_dict = dict(row)
            if error_dict.get('details'):
                error_dict['details'] = json.loads(error_dict['details'])
            errors.append(error_dict)
        
        return errors
    
    def get_error_by_key(self, error_key: str) -> Optional[Dict[str, Any]]:
        """Get a single error record by its unique error_key.
        
        Returns the full row as a dict (including first_seen, last_seen,
        acknowledged, etc.) or None if not found / already resolved.
        Only returns unresolved (active) errors.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM errors
            WHERE error_key = ? AND resolved_at IS NULL
            LIMIT 1
        ''', (error_key,))
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        error_dict = dict(row)
        if error_dict.get('details'):
            try:
                error_dict['details'] = json.loads(error_dict['details'])
            except (json.JSONDecodeError, TypeError):
                pass
        return error_dict
    
    def cleanup_old_errors(self):
        """Clean up old resolved errors and auto-resolve stale errors"""
        with self._db_lock:
            return self._cleanup_old_errors_impl()
    
    def _cleanup_old_errors_impl(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        now = datetime.now()
        now_iso = now.isoformat()
        
        # Delete resolved errors older than 7 days
        cutoff_resolved = (now - timedelta(days=7)).isoformat()
        cursor.execute('DELETE FROM errors WHERE resolved_at < ?', (cutoff_resolved,))
        
        # ── Auto-resolve stale errors using Suppression Duration settings ──
        # Read per-category suppression hours from user_settings.
        # If the user hasn't configured a value, use DEFAULT_SUPPRESSION_HOURS.
        # This is the SINGLE source of truth for auto-resolution timing.
        user_settings = {}
        try:
            cursor.execute(
                'SELECT setting_key, setting_value FROM user_settings WHERE setting_key LIKE ?',
                ('suppress_%',)
            )
            for row in cursor.fetchall():
                user_settings[row[0]] = row[1]
        except Exception:
            pass
        
        for category, setting_key in self.CATEGORY_SETTING_MAP.items():
            stored = user_settings.get(setting_key)
            try:
                hours = int(stored) if stored else self.DEFAULT_SUPPRESSION_HOURS
            except (ValueError, TypeError):
                hours = self.DEFAULT_SUPPRESSION_HOURS
            
            # -1 means permanently suppressed -- skip auto-resolve
            if hours < 0:
                continue
            
            cutoff = (now - timedelta(hours=hours)).isoformat()
            cursor.execute('''
                UPDATE errors 
                SET resolved_at = ?
                WHERE category = ?
                  AND resolved_at IS NULL 
                  AND last_seen < ?
                  AND acknowledged = 0
            ''', (now_iso, category, cutoff))
        
        # Catch-all: auto-resolve any error from an unmapped category
        # whose last_seen exceeds DEFAULT_SUPPRESSION_HOURS.
        fallback_cutoff = (now - timedelta(hours=self.DEFAULT_SUPPRESSION_HOURS)).isoformat()
        cursor.execute('''
            UPDATE errors
            SET resolved_at = ?
            WHERE resolved_at IS NULL
              AND acknowledged = 0
              AND last_seen < ?
        ''', (now_iso, fallback_cutoff))
        
        # Delete old events (>30 days)
        cutoff_events = (now - timedelta(days=30)).isoformat()
        cursor.execute('DELETE FROM events WHERE timestamp < ?', (cutoff_events,))
        
        conn.commit()
        conn.close()
    
    def check_vm_running(self, vm_id: str) -> bool:
        """
        Check if a VM/CT is running and resolve error if so.
        Returns True if running and error was resolved.
        """
        import subprocess
        
        try:
            # Check qm status for VMs
            result = subprocess.run(
                ['qm', 'status', vm_id],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0 and 'running' in result.stdout.lower():
                self.resolve_error(f'vm_{vm_id}', 'VM started')
                return True
            
            # Check pct status for containers
            result = subprocess.run(
                ['pct', 'status', vm_id],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0 and 'running' in result.stdout.lower():
                self.resolve_error(f'ct_{vm_id}', 'Container started')
                return True
            
            return False
            
        except Exception:
            return False
    
    def get_dismissed_errors(self) -> List[Dict[str, Any]]:
        """
        Get errors that were acknowledged/dismissed but still within suppression period.
        These are shown as INFO in the frontend with a 'Dismissed' badge.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM errors 
            WHERE acknowledged = 1 AND resolved_at IS NOT NULL
            ORDER BY resolved_at DESC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        dismissed = []
        now = datetime.now()
        
        for row in rows:
            error_dict = dict(row)
            if error_dict.get('details'):
                try:
                    error_dict['details'] = json.loads(error_dict['details'])
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Check if still within suppression period using per-record hours
            try:
                resolved_dt = datetime.fromisoformat(error_dict['resolved_at'])
                sup_hours = error_dict.get('suppression_hours')
                if sup_hours is None:
                    sup_hours = self.DEFAULT_SUPPRESSION_HOURS
                
                error_dict['dismissed'] = True
                
                if sup_hours == -1:
                    # Permanent dismiss
                    error_dict['suppression_remaining_hours'] = -1
                    error_dict['permanent'] = True
                    dismissed.append(error_dict)
                else:
                    elapsed_seconds = (now - resolved_dt).total_seconds()
                    suppression_seconds = sup_hours * 3600
                    
                    if elapsed_seconds < suppression_seconds:
                        error_dict['suppression_remaining_hours'] = round(
                            (suppression_seconds - elapsed_seconds) / 3600, 1
                        )
                        error_dict['permanent'] = False
                        dismissed.append(error_dict)
            except (ValueError, TypeError):
                pass
        
        return dismissed
    
    def emit_event(self, event_type: str, category: str, severity: str, 
                   data: Optional[Dict] = None) -> int:
        """
        Emit a health event for the notification system.
        Returns the event ID.
        
        Event types: 
        - 'state_change': severity changed (OK->WARNING, WARNING->CRITICAL, etc.)
        - 'new_error': new error detected
        - 'resolved': error resolved
        - 'escalated': severity increased
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        event_data = data or {}
        event_data['category'] = category
        event_data['severity'] = severity
        event_data['needs_notification'] = True
        
        cursor.execute('''
            INSERT INTO events (event_type, error_key, timestamp, data)
            VALUES (?, ?, ?, ?)
        ''', (event_type, f'{category}_{severity}', datetime.now().isoformat(), 
              json.dumps(event_data)))
        
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return event_id
    
    def get_pending_notifications(self) -> List[Dict[str, Any]]:
        """
        Get events that need notification (for future Telegram/Gotify integration).
        Groups by severity for batch notification sending.
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT e.*, err.category as error_category, err.reason as error_reason
            FROM events e
            LEFT JOIN errors err ON e.error_key = err.error_key
            WHERE json_extract(e.data, '$.needs_notification') = 1
            ORDER BY e.timestamp DESC
            LIMIT 100
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        events = []
        for row in rows:
            event_dict = dict(row)
            if event_dict.get('data'):
                try:
                    event_dict['data'] = json.loads(event_dict['data'])
                except (json.JSONDecodeError, TypeError):
                    pass
            events.append(event_dict)
        
        return events
    
    def mark_events_notified(self, event_ids: List[int]):
        """Mark events as notified (notification was sent successfully)"""
        if not event_ids:
            return
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        for event_id in event_ids:
            cursor.execute('''
                UPDATE events 
                SET data = json_set(COALESCE(data, '{}'), '$.needs_notification', 0, '$.notified_at', ?)
                WHERE id = ?
            ''', (datetime.now().isoformat(), event_id))
        
        conn.commit()
        conn.close()
    
    def _record_event(self, cursor, event_type: str, error_key: str, data: Dict):
        """Internal: Record an event"""
        cursor.execute('''
            INSERT INTO events (event_type, error_key, timestamp, data)
            VALUES (?, ?, ?, ?)
        ''', (event_type, error_key, datetime.now().isoformat(), json.dumps(data)))
    
    def get_unnotified_errors(self) -> List[Dict[str, Any]]:
        """Get errors that need Telegram notification"""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM errors 
            WHERE notification_sent = 0 
              AND resolved_at IS NULL 
              AND acknowledged = 0
            ORDER BY severity DESC, first_seen ASC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        errors = []
        for row in rows:
            error_dict = dict(row)
            if error_dict.get('details'):
                error_dict['details'] = json.loads(error_dict['details'])
            errors.append(error_dict)
        
        return errors
    
    def mark_notified(self, error_key: str):
        """Mark error as notified"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE errors 
            SET notification_sent = 1
            WHERE error_key = ?
        ''', (error_key,))
        
        conn.commit()
        conn.close()
    
    # ─── System Capabilities Cache ───────────────────────────────
    
    def get_capability(self, cap_key: str) -> Optional[str]:
        """
        Get a cached system capability value.
        Returns None if not yet detected.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT cap_value FROM system_capabilities WHERE cap_key = ?',
            (cap_key,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    
    def set_capability(self, cap_key: str, cap_value: str):
        """Store a system capability value (detected once, cached forever)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO system_capabilities (cap_key, cap_value, detected_at)
            VALUES (?, ?, ?)
        ''', (cap_key, cap_value, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_all_capabilities(self) -> Dict[str, str]:
        """Get all cached system capabilities as a dict."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT cap_key, cap_value FROM system_capabilities')
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    
    # Note: System capabilities (has_zfs, has_lvm) are now derived at runtime
    # from Proxmox storage types in health_monitor.get_detailed_status()
    # This avoids redundant subprocess calls and ensures immediate detection
    # when the user adds new ZFS/LVM storage via Proxmox.
    
    # ─── User Settings ──────────────────────────────────────────
    
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a user setting value by key."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT setting_value FROM user_settings WHERE setting_key = ?', (key,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    
    def set_setting(self, key: str, value: str):
        """Store a user setting value."""
        with self._db_lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO user_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
            ''', (key, value, datetime.now().isoformat()))
            conn.commit()
            conn.close()
    
    def get_all_settings(self, prefix: Optional[str] = None) -> Dict[str, str]:
        """Get all user settings, optionally filtered by key prefix."""
        conn = self._get_conn()
        cursor = conn.cursor()
        if prefix:
            cursor.execute(
                'SELECT setting_key, setting_value FROM user_settings WHERE setting_key LIKE ?',
                (f'{prefix}%',)
            )
        else:
            cursor.execute('SELECT setting_key, setting_value FROM user_settings')
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    
    def sync_dismissed_suppression(self):
        """
        Retroactively update all existing dismissed errors to match current
        user settings. Called when the user saves settings, so changes are
        effective immediately on already-dismissed items.
        
        For each dismissed error, looks up its category's configured hours
        and updates the suppression_hours column to match.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Build reverse map: category -> setting_key
        cat_to_setting = {v['category']: k 
                          for k, v in self._get_category_labels().items()}
        
        # Get all current suppression settings
        current_settings = self.get_all_settings('suppress_')
        
        # Get all dismissed (acknowledged) errors
        cursor.execute('''
            SELECT id, error_key, category, suppression_hours
            FROM errors WHERE acknowledged = 1
        ''')
        dismissed = cursor.fetchall()
        
        updated_count = 0
        for err_id, error_key, category, old_hours in dismissed:
            setting_key = None
            for skey, meta in self._get_category_labels().items():
                if meta['category'] == category:
                    setting_key = skey
                    break
            
            if not setting_key:
                continue
            
            stored = current_settings.get(setting_key)
            new_hours = int(stored) if stored else self.DEFAULT_SUPPRESSION_HOURS
            
            if new_hours != old_hours:
                cursor.execute(
                    'UPDATE errors SET suppression_hours = ? WHERE id = ?',
                    (new_hours, err_id)
                )
                self._record_event(cursor, 'suppression_updated', error_key, {
                    'old_hours': old_hours,
                    'new_hours': new_hours,
                    'reason': 'settings_sync'
                })
                updated_count += 1
        
        conn.commit()
        conn.close()
        return updated_count
    
    def _get_category_labels(self) -> dict:
        """Internal helper for category label metadata."""
        return {
            'suppress_cpu': {'label': 'CPU Usage & Temperature', 'category': 'temperature', 'icon': 'cpu'},
            'suppress_memory': {'label': 'Memory & Swap', 'category': 'memory', 'icon': 'memory'},
            'suppress_storage': {'label': 'Storage Mounts & Space', 'category': 'storage', 'icon': 'storage'},
            'suppress_disks': {'label': 'Disk I/O & Errors', 'category': 'disks', 'icon': 'disk'},
            'suppress_network': {'label': 'Network Interfaces', 'category': 'network', 'icon': 'network'},
            'suppress_vms': {'label': 'VMs & Containers', 'category': 'vms', 'icon': 'vms'},
            'suppress_pve_services': {'label': 'PVE Services', 'category': 'pve_services', 'icon': 'services'},
            'suppress_logs': {'label': 'System Logs', 'category': 'logs', 'icon': 'logs'},
            'suppress_updates': {'label': 'System Updates', 'category': 'updates', 'icon': 'updates'},
            'suppress_security': {'label': 'Security & Certificates', 'category': 'security', 'icon': 'security'},
        }
    
    def get_suppression_categories(self) -> List[Dict[str, Any]]:
        """
        Get all health categories with their current suppression settings.
        Used by the settings page to render the per-category configuration.
        """
        category_labels = self._get_category_labels()
        current_settings = self.get_all_settings('suppress_')
        
        result = []
        for key, meta in category_labels.items():
            stored = current_settings.get(key)
            hours = int(stored) if stored else self.DEFAULT_SUPPRESSION_HOURS
            result.append({
                'key': key,
                'label': meta['label'],
                'category': meta['category'],
                'icon': meta['icon'],
                'hours': hours,
            })
        
        return result
    
    def get_custom_suppressions(self) -> List[Dict[str, Any]]:
        """
        Get only categories with non-default suppression settings.
        Used by the health modal to show a summary of custom suppressions.
        """
        all_cats = self.get_suppression_categories()
        return [c for c in all_cats if c['hours'] != self.DEFAULT_SUPPRESSION_HOURS]
    
    def record_unknown_persistent(self, category: str, reason: str):
        """
        Record a persistent UNKNOWN event when a health check has been
        unable to verify for >= 3 consecutive cycles (~15 min).
        Avoids duplicates by only recording once per 30 min per category.
        """
        with self._db_lock:
            self._record_unknown_persistent_impl(category, reason)
    
    def _record_unknown_persistent_impl(self, category, reason):
        try:
            event_key = f'unknown_persistent_{category}'
            now = datetime.now().isoformat()
            
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Check if we already recorded this within the last 30 minutes
            # Note: events table has columns (id, event_type, error_key, timestamp, data)
            # We use error_key for deduplication since it contains the category
            cursor.execute('''
                SELECT MAX(timestamp) FROM events 
                WHERE event_type = ? AND error_key = ?
            ''', ('unknown_persistent', event_key))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    last_recorded = datetime.fromisoformat(row[0])
                    if (datetime.now() - last_recorded).total_seconds() < 1800:
                        conn.close()
                        return  # Already recorded recently
                except (ValueError, TypeError):
                    pass  # If timestamp is malformed, proceed with recording
            
            cursor.execute('''
                INSERT INTO events (event_type, error_key, timestamp, data)
                VALUES (?, ?, ?, ?)
            ''', ('unknown_persistent', event_key, now, 
                  json.dumps({'category': category, 'reason': reason})))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error recording UNKNOWN persistent: {e}")


    # ────────────────────────────────────────────────────────────────
    #  Disk Observations API
    # ────────────────────────────────────────────────────────────────

    def register_disk(self, device_name: str, serial: Optional[str] = None,
                      model: Optional[str] = None, size_bytes: Optional[int] = None):
        """Register or update a physical disk in the registry.
        
        Uses (device_name, serial) as unique key. If the disk was previously
        marked removed, it's re-activated.
        
        Also consolidates old ATA-named entries: if an observation was recorded
        under 'ata8' and we now know the real block device is 'sdh' with
        serial 'WX72...', update the old entry so observations are linked.
        """
        now = datetime.now().isoformat()
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Consolidate: if serial is known and an old entry exists with
            # a different device_name (e.g. 'ata8' instead of 'sdh'),
            # update that entry's device_name so observations carry over.
            if serial:
                cursor.execute('''
                    SELECT id, device_name FROM disk_registry
                    WHERE serial = ? AND serial != '' AND device_name != ?
                ''', (serial, device_name))
                old_rows = cursor.fetchall()
                for old_id, old_dev in old_rows:
                    # Only consolidate ATA names -> block device names
                    if old_dev.startswith('ata') and not device_name.startswith('ata'):
                        # Check if target (device_name, serial) already exists
                        cursor.execute(
                            'SELECT id FROM disk_registry WHERE device_name = ? AND serial = ?',
                            (device_name, serial))
                        existing = cursor.fetchone()
                        if existing:
                            # Merge: move observations from old -> existing, then delete old
                            cursor.execute(
                                'UPDATE disk_observations SET disk_registry_id = ? WHERE disk_registry_id = ?',
                                (existing[0], old_id))
                            cursor.execute('DELETE FROM disk_registry WHERE id = ?', (old_id,))
                        else:
                            # Rename the old entry to the real block device name
                            cursor.execute(
                                'UPDATE disk_registry SET device_name = ?, model = COALESCE(?, model), '
                                'size_bytes = COALESCE(?, size_bytes), last_seen = ?, removed = 0 '
                                'WHERE id = ?',
                                (device_name, model, size_bytes, now, old_id))
            
            cursor.execute('''
                INSERT INTO disk_registry (device_name, serial, model, size_bytes, first_seen, last_seen, removed)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(device_name, serial) DO UPDATE SET
                    model = COALESCE(excluded.model, model),
                    size_bytes = COALESCE(excluded.size_bytes, size_bytes),
                    last_seen = excluded.last_seen,
                    removed = 0
            ''', (device_name, serial or '', model, size_bytes, now, now))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error registering disk {device_name}: {e}")

    def _get_disk_registry_id(self, cursor, device_name: str,
                               serial: Optional[str] = None) -> Optional[int]:
        """Find disk_registry.id, matching by serial first, then device_name.
        
        Also handles ATA-to-block cross-references: if looking for 'sdh' also
        checks entries with ATA names that share the same serial.
        """
        if serial:
            cursor.execute(
                'SELECT id FROM disk_registry WHERE serial = ? AND serial != "" ORDER BY last_seen DESC LIMIT 1',
                (serial,))
            row = cursor.fetchone()
            if row:
                return row[0]
        # Fallback: match by device_name (strip /dev/ prefix)
        clean_dev = device_name.replace('/dev/', '')
        cursor.execute(
            'SELECT id FROM disk_registry WHERE device_name = ? ORDER BY last_seen DESC LIMIT 1',
            (clean_dev,))
        row = cursor.fetchone()
        if row:
            return row[0]
        # Last resort: search for ATA-named entries that might refer to this device
        # This handles cases where observations were recorded under 'ata8'
        # but we're querying for 'sdh'
        if clean_dev.startswith('sd') or clean_dev.startswith('nvme'):
            cursor.execute(
                'SELECT id FROM disk_registry WHERE device_name LIKE "ata%" ORDER BY last_seen DESC')
            # For each ATA entry, we can't resolve here without OS access,
            # so just return None and let the serial-based consolidation
            # in register_disk handle it over time.
            pass
        return None

    def record_disk_observation(self, device_name: str, serial: Optional[str],
                                 error_type: str, error_signature: str,
                                 raw_message: str = '',
                                 severity: str = 'warning'):
        """Record or deduplicate a disk error observation.
        
        error_type:  'smart_error', 'io_error', 'connection_error'
        error_signature: Normalized unique string for dedup (e.g. 'FailedReadSmartSelfTestLog')
        """
        now = datetime.now().isoformat()
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Auto-register the disk if not present
            clean_dev = device_name.replace('/dev/', '')
            self.register_disk(clean_dev, serial)
            
            disk_id = self._get_disk_registry_id(cursor, clean_dev, serial)
            if not disk_id:
                conn.close()
                return
            
            # Upsert observation: if same (disk, type, signature), bump count + update last_occurrence
            cursor.execute('''
                INSERT INTO disk_observations
                    (disk_registry_id, error_type, error_signature, first_occurrence,
                     last_occurrence, occurrence_count, raw_message, severity, dismissed)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, 0)
                ON CONFLICT(disk_registry_id, error_type, error_signature) DO UPDATE SET
                    last_occurrence = excluded.last_occurrence,
                    occurrence_count = occurrence_count + 1,
                    severity = CASE WHEN excluded.severity = 'critical' THEN 'critical' ELSE severity END,
                    dismissed = 0
            ''', (disk_id, error_type, error_signature, now, now, raw_message, severity))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error recording disk observation: {e}")

    def get_disk_observations(self, device_name: Optional[str] = None,
                               serial: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get active (non-dismissed) observations for one disk or all disks."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            if device_name or serial:
                disk_id = self._get_disk_registry_id(cursor,
                                                      device_name or '', serial)
                if not disk_id:
                    conn.close()
                    return []
                cursor.execute('''
                    SELECT o.id, o.error_type, o.error_signature,
                           o.first_occurrence, o.last_occurrence,
                           o.occurrence_count, o.raw_message, o.severity, o.dismissed,
                           d.device_name, d.serial, d.model
                    FROM disk_observations o
                    JOIN disk_registry d ON o.disk_registry_id = d.id
                    WHERE o.disk_registry_id = ? AND o.dismissed = 0
                    ORDER BY o.last_occurrence DESC
                ''', (disk_id,))
            else:
                cursor.execute('''
                    SELECT o.id, o.error_type, o.error_signature,
                           o.first_occurrence, o.last_occurrence,
                           o.occurrence_count, o.raw_message, o.severity, o.dismissed,
                           d.device_name, d.serial, d.model
                    FROM disk_observations o
                    JOIN disk_registry d ON o.disk_registry_id = d.id
                    WHERE o.dismissed = 0
                    ORDER BY o.last_occurrence DESC
                ''')
            
            rows = cursor.fetchall()
            conn.close()
            
            return [{
                'id': r[0],
                'error_type': r[1],
                'error_signature': r[2],
                'first_occurrence': r[3],
                'last_occurrence': r[4],
                'occurrence_count': r[5],
                'raw_message': r[6] or '',
                'severity': r[7],
                'dismissed': bool(r[8]),
                'device_name': r[9],
                'serial': r[10],
                'model': r[11],
            } for r in rows]
        except Exception as e:
            print(f"[HealthPersistence] Error getting observations: {e}")
            return []

    def get_all_observed_devices(self) -> List[Dict[str, Any]]:
        """Return a list of unique device_name + serial pairs that have observations."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT device_name, serial
                FROM disk_observations
                WHERE dismissed = 0
            ''')
            rows = cursor.fetchall()
            conn.close()
            return [{'device_name': r[0], 'serial': r[1] or ''} for r in rows]
        except Exception:
            return []
    
    def get_disks_observation_counts(self) -> Dict[str, int]:
        """Return {device_name: count} of active observations per disk.
        
        Also includes serial-keyed entries for cross-device matching.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT d.device_name, d.serial, COUNT(o.id) as cnt
                FROM disk_observations o
                JOIN disk_registry d ON o.disk_registry_id = d.id
                WHERE o.dismissed = 0
                GROUP BY d.id
            ''')
            result = {}
            for device_name, serial, cnt in cursor.fetchall():
                result[device_name] = cnt
                if serial:
                    result[f'serial:{serial}'] = cnt
            conn.close()
            return result
        except Exception as e:
            print(f"[HealthPersistence] Error getting observation counts: {e}")
            return {}

    def dismiss_disk_observation(self, observation_id: int):
        """Mark a single observation as dismissed."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE disk_observations SET dismissed = 1 WHERE id = ?',
                (observation_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error dismissing observation: {e}")

    def cleanup_stale_observations(self, max_age_days: int = 30):
        """Auto-dismiss observations not seen in max_age_days."""
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE disk_observations 
                SET dismissed = 1 
                WHERE dismissed = 0 AND last_occurrence < ?
            ''', (cutoff,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error cleaning stale observations: {e}")

    def mark_removed_disks(self, active_device_names: List[str]):
        """Mark disks not in active_device_names as removed."""
        try:
            now = datetime.now().isoformat()
            conn = self._get_conn()
            cursor = conn.cursor()
            if active_device_names:
                placeholders = ','.join('?' for _ in active_device_names)
                cursor.execute(f'''
                    UPDATE disk_registry SET removed = 1
                    WHERE device_name NOT IN ({placeholders}) AND removed = 0
                ''', active_device_names)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[HealthPersistence] Error marking removed disks: {e}")

    # ────────────────────────────────────────────────────────────────
    #  Disk Worst Health State Tracking
    # ────────────────────────────────────────────────────────────────
    
    HEALTH_SEVERITY_ORDER = {'healthy': 0, 'warning': 1, 'critical': 2}
    
    def update_disk_worst_health(self, device_name: str, serial: Optional[str],
                                  health: str, reason: str = '') -> bool:
        """Update worst_health if the new health is worse than current.
        
        Health progression is one-way: healthy -> warning -> critical
        Only admin_clear_disk_health() can reset to healthy.
        
        Returns True if worst_health was updated.
        """
        health_lower = health.lower()
        if health_lower not in self.HEALTH_SEVERITY_ORDER:
            return False
        
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            disk_id = self._get_disk_registry_id(cursor, device_name.replace('/dev/', ''), serial)
            if not disk_id:
                # Auto-register disk if not present
                self.register_disk(device_name.replace('/dev/', ''), serial)
                disk_id = self._get_disk_registry_id(cursor, device_name.replace('/dev/', ''), serial)
            
            if not disk_id:
                conn.close()
                return False
            
            # Get current worst_health
            cursor.execute('SELECT worst_health, admin_cleared FROM disk_registry WHERE id = ?', (disk_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return False
            
            current_worst = row[0] or 'healthy'
            admin_cleared = row[1] or 0
            
            # If admin cleared and new issue is the same or less severe, don't update
            # But if admin cleared and issue escalates, update anyway
            current_severity = self.HEALTH_SEVERITY_ORDER.get(current_worst, 0)
            new_severity = self.HEALTH_SEVERITY_ORDER.get(health_lower, 0)
            
            # Only update if new health is worse
            if new_severity > current_severity:
                now = datetime.now().isoformat()
                cursor.execute('''
                    UPDATE disk_registry 
                    SET worst_health = ?, worst_health_date = ?, worst_health_reason = ?,
                        admin_cleared = 0
                    WHERE id = ?
                ''', (health_lower, now, reason, disk_id))
                conn.commit()
                conn.close()
                return True
            
            conn.close()
            return False
        except Exception as e:
            print(f"[HealthPersistence] Error updating disk worst_health: {e}")
            return False
    
    def get_disk_worst_health(self, device_name: str, serial: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get the worst health state for a specific disk."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            disk_id = self._get_disk_registry_id(cursor, device_name.replace('/dev/', ''), serial)
            if not disk_id:
                conn.close()
                return None
            
            cursor.execute('''
                SELECT worst_health, worst_health_date, worst_health_reason, 
                       admin_cleared, admin_cleared_date, admin_cleared_note
                FROM disk_registry WHERE id = ?
            ''', (disk_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'worst_health': row[0] or 'healthy',
                    'worst_health_date': row[1],
                    'worst_health_reason': row[2],
                    'admin_cleared': bool(row[3]),
                    'admin_cleared_date': row[4],
                    'admin_cleared_note': row[5],
                }
            return None
        except Exception as e:
            print(f"[HealthPersistence] Error getting disk worst_health: {e}")
            return None
    
    def admin_clear_disk_health(self, device_name: str, serial: Optional[str], note: str) -> bool:
        """Admin manually clears disk health history (e.g., after disk replacement).
        
        Requires a note explaining why (for audit trail).
        """
        if not note or len(note.strip()) < 5:
            return False  # Require meaningful note
        
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            disk_id = self._get_disk_registry_id(cursor, device_name.replace('/dev/', ''), serial)
            if not disk_id:
                conn.close()
                return False
            
            now = datetime.now().isoformat()
            cursor.execute('''
                UPDATE disk_registry 
                SET worst_health = 'healthy', admin_cleared = 1, 
                    admin_cleared_date = ?, admin_cleared_note = ?
                WHERE id = ?
            ''', (now, note.strip(), disk_id))
            
            # Also dismiss all active observations for this disk
            cursor.execute('''
                UPDATE disk_observations SET dismissed = 1 WHERE disk_registry_id = ?
            ''', (disk_id,))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[HealthPersistence] Error clearing disk health: {e}")
            return False
    
    def get_all_disks_health_summary(self) -> List[Dict[str, Any]]:
        """Get health summary for all registered disks (for Health Monitor listing).
        
        Returns list of disks with their current and worst health states.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT d.id, d.device_name, d.serial, d.model, d.size_bytes,
                       d.first_seen, d.last_seen, d.removed,
                       d.worst_health, d.worst_health_date, d.worst_health_reason,
                       d.admin_cleared, d.admin_cleared_date,
                       (SELECT COUNT(*) FROM disk_observations o 
                        WHERE o.disk_registry_id = d.id AND o.dismissed = 0) as active_observations
                FROM disk_registry d
                WHERE d.removed = 0
                ORDER BY d.device_name
            ''')
            rows = cursor.fetchall()
            conn.close()
            
            return [{
                'id': r[0],
                'device_name': r[1],
                'serial': r[2] or '',
                'model': r[3] or 'Unknown',
                'size_bytes': r[4],
                'first_seen': r[5],
                'last_seen': r[6],
                'removed': bool(r[7]),
                'worst_health': r[8] or 'healthy',
                'worst_health_date': r[9],
                'worst_health_reason': r[10] or '',
                'admin_cleared': bool(r[11]),
                'admin_cleared_date': r[12],
                'active_observations': r[13],
            } for r in rows]
        except Exception as e:
            print(f"[HealthPersistence] Error getting disks health summary: {e}")
            return []


# Global instance
health_persistence = HealthPersistence()
