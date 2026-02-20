"""
Flask routes for notification service configuration and management.
Blueprint pattern matching flask_health_routes.py / flask_security_routes.py.
"""

import hmac
import time
import hashlib
from collections import deque
from flask import Blueprint, jsonify, request
from notification_manager import notification_manager


# ─── Webhook Hardening Helpers ───────────────────────────────────

class WebhookRateLimiter:
    """Simple sliding-window rate limiter for the webhook endpoint."""
    
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque = deque()
    
    def allow(self) -> bool:
        now = time.time()
        # Prune entries outside the window
        while self._timestamps and now - self._timestamps[0] > self._window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True


class ReplayCache:
    """Bounded in-memory cache of recently seen request signatures (60s TTL)."""
    
    _MAX_SIZE = 2000  # Hard cap to prevent memory growth
    
    def __init__(self, ttl: int = 60):
        self._ttl = ttl
        self._seen: dict = {}  # signature -> timestamp
    
    def check_and_record(self, signature: str) -> bool:
        """Return True if this signature was already seen (replay). Records it otherwise."""
        now = time.time()
        # Periodic cleanup
        if len(self._seen) > self._MAX_SIZE // 2:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        if signature in self._seen and now - self._seen[signature] < self._ttl:
            return True  # Replay detected
        self._seen[signature] = now
        return False


# Module-level singletons (one per process)
_webhook_limiter = WebhookRateLimiter(max_requests=60, window_seconds=60)
_replay_cache = ReplayCache(ttl=60)

# Timestamp validation window (seconds)
_TIMESTAMP_MAX_DRIFT = 60

notification_bp = Blueprint('notifications', __name__)


@notification_bp.route('/api/notifications/settings', methods=['GET'])
def get_notification_settings():
    """Get all notification settings for the UI."""
    try:
        settings = notification_manager.get_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/settings', methods=['POST'])
def save_notification_settings():
    """Save notification settings from the UI."""
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({'error': 'No data provided'}), 400
        
        result = notification_manager.save_settings(payload)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/test', methods=['POST'])
def test_notification():
    """Send a test notification to one or all channels."""
    try:
        data = request.get_json() or {}
        channel = data.get('channel', 'all')
        
        result = notification_manager.test_channel(channel)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/status', methods=['GET'])
def get_notification_status():
    """Get notification service status."""
    try:
        status = notification_manager.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/history', methods=['GET'])
def get_notification_history():
    """Get notification history with optional filters."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        severity = request.args.get('severity', '')
        channel = request.args.get('channel', '')
        
        result = notification_manager.get_history(limit, offset, severity, channel)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/history', methods=['DELETE'])
def clear_notification_history():
    """Clear all notification history."""
    try:
        result = notification_manager.clear_history()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@notification_bp.route('/api/notifications/send', methods=['POST'])
def send_notification():
    """Send a notification via API (for testing or external triggers)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        result = notification_manager.send_notification(
            event_type=data.get('event_type', 'custom'),
            severity=data.get('severity', 'INFO'),
            title=data.get('title', ''),
            message=data.get('message', ''),
            data=data.get('data', {}),
            source='api'
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PVE config constants ──
_PVE_ENDPOINT_ID = 'proxmenux-webhook'
_PVE_MATCHER_ID = 'proxmenux-default'
_PVE_WEBHOOK_URL = 'http://127.0.0.1:8008/api/notifications/webhook'
_PVE_NOTIFICATIONS_CFG = '/etc/pve/notifications.cfg'
_PVE_PRIV_CFG = '/etc/pve/priv/notifications.cfg'
_PVE_OUR_HEADERS = {
    f'webhook: {_PVE_ENDPOINT_ID}',
    f'matcher: {_PVE_MATCHER_ID}',
}


def _pve_read_file(path):
    """Read file, return (content, error). Content is '' if missing."""
    try:
        with open(path, 'r') as f:
            return f.read(), None
    except FileNotFoundError:
        return '', None
    except PermissionError:
        return None, f'Permission denied reading {path}'
    except Exception as e:
        return None, str(e)


def _pve_backup_file(path):
    """Create timestamped backup if file exists. Never fails fatally."""
    import os, shutil
    from datetime import datetime
    try:
        if os.path.exists(path):
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = f"{path}.proxmenux_backup_{ts}"
            shutil.copy2(path, backup)
    except Exception:
        pass


def _pve_remove_our_blocks(text, headers_to_remove):
    """Remove only blocks whose header line matches one of ours.
    
    Preserves ALL other content byte-for-byte.
    A block = header line + indented continuation lines + trailing blank line.
    """
    lines = text.splitlines(keepends=True)
    cleaned = []
    skip_block = False
    
    for line in lines:
        stripped = line.strip()
        
        if stripped and not line[0:1].isspace() and ':' in stripped:
            if stripped in headers_to_remove:
                skip_block = True
                continue
            else:
                skip_block = False
        
        if skip_block:
            if not stripped:
                skip_block = False
                continue
            elif line[0:1].isspace():
                continue
            else:
                skip_block = False
        
        cleaned.append(line)
    
    return ''.join(cleaned)


@notification_bp.route('/api/notifications/proxmox/setup-webhook', methods=['POST'])
def setup_proxmox_webhook():
    """Automatically configure PVE notifications to call our webhook.
    
    Strategy: parse existing config into discrete blocks, only add or
    replace blocks whose name matches our IDs, preserve everything else
    byte-for-byte.  Creates timestamped backups before any modification.
    
    Idempotent: safe to call multiple times.
    Only touches blocks named 'proxmenux-webhook' / 'proxmenux-default'.
    """
    import secrets as secrets_mod
    
    result = {
        'configured': False,
        'endpoint_id': _PVE_ENDPOINT_ID,
        'matcher_id': _PVE_MATCHER_ID,
        'url': _PVE_WEBHOOK_URL,
        'fallback_commands': [],
        'error': None,
    }
    
    def _build_fallback():
        return [
            "# Append to END of /etc/pve/notifications.cfg",
            "# (do NOT delete existing content):",
            "",
            f"webhook: {_PVE_ENDPOINT_ID}",
            f"\tmethod post",
            f"\turl {_PVE_WEBHOOK_URL}",
            "",
            f"matcher: {_PVE_MATCHER_ID}",
            f"\ttarget {_PVE_ENDPOINT_ID}",
            "\tmode all",
        ]
    
    try:
        # ── Step 1: Ensure webhook secret exists (for our own internal use) ──
        secret = notification_manager.get_webhook_secret()
        if not secret:
            secret = secrets_mod.token_urlsafe(32)
            notification_manager._save_setting('webhook_secret', secret)
        
        # ── Step 2: Read main config ──
        cfg_text, err = _pve_read_file(_PVE_NOTIFICATIONS_CFG)
        if err:
            result['error'] = err
            result['fallback_commands'] = _build_fallback()
            return jsonify(result), 200
        
        # ── Step 3: Read priv config (to clean up any broken blocks we wrote before) ──
        priv_text, err = _pve_read_file(_PVE_PRIV_CFG)
        if err:
            priv_text = None
        
        # ── Step 4: Create backups before ANY modification ──
        _pve_backup_file(_PVE_NOTIFICATIONS_CFG)
        if priv_text is not None:
            _pve_backup_file(_PVE_PRIV_CFG)
        
        # ── Step 5: Remove any previous proxmenux blocks from BOTH files ──
        cleaned_cfg = _pve_remove_our_blocks(cfg_text, _PVE_OUR_HEADERS)
        
        if priv_text is not None:
            cleaned_priv = _pve_remove_our_blocks(priv_text, _PVE_OUR_HEADERS)
        
        # ── Step 6: Build new blocks ──
        # Exact format from a real working PVE server:
        #   webhook: name
        #   \tmethod post
        #   \turl http://...
        #
        # NO header lines -- localhost webhook doesn't need them.
        # PVE header format is: header name=X-Key,value=<base64>
        # PVE secret format is: secret name=key,value=<base64>
        # Neither is needed for localhost calls.
        
        endpoint_block = (
            f"webhook: {_PVE_ENDPOINT_ID}\n"
            f"\tmethod post\n"
            f"\turl {_PVE_WEBHOOK_URL}\n"
        )
        
        matcher_block = (
            f"matcher: {_PVE_MATCHER_ID}\n"
            f"\ttarget {_PVE_ENDPOINT_ID}\n"
            f"\tmode all\n"
        )
        
        # ── Step 7: Append our blocks to cleaned main config ──
        if cleaned_cfg and not cleaned_cfg.endswith('\n'):
            cleaned_cfg += '\n'
        if cleaned_cfg and not cleaned_cfg.endswith('\n\n'):
            cleaned_cfg += '\n'
        
        new_cfg = cleaned_cfg + endpoint_block + '\n' + matcher_block
        
        # ── Step 8: Write main config ──
        try:
            with open(_PVE_NOTIFICATIONS_CFG, 'w') as f:
                f.write(new_cfg)
        except PermissionError:
            result['error'] = f'Permission denied writing {_PVE_NOTIFICATIONS_CFG}'
            result['fallback_commands'] = _build_fallback()
            return jsonify(result), 200
        except Exception as e:
            try:
                with open(_PVE_NOTIFICATIONS_CFG, 'w') as f:
                    f.write(cfg_text)
            except Exception:
                pass
            result['error'] = str(e)
            result['fallback_commands'] = _build_fallback()
            return jsonify(result), 200
        
        # ── Step 9: Clean priv config (remove our broken blocks, write nothing new) ──
        if priv_text is not None and cleaned_priv != priv_text:
            try:
                with open(_PVE_PRIV_CFG, 'w') as f:
                    f.write(cleaned_priv)
            except Exception:
                pass
        
        result['configured'] = True
        result['secret'] = secret
        return jsonify(result), 200
    
    except Exception as e:
        result['error'] = str(e)
        result['fallback_commands'] = _build_fallback()
        return jsonify(result), 200


@notification_bp.route('/api/notifications/proxmox/cleanup-webhook', methods=['POST'])
def cleanup_proxmox_webhook():
    """Remove ProxMenux webhook blocks from PVE notification config.
    
    Called when the notification service is disabled.
    Only removes blocks named 'proxmenux-webhook' / 'proxmenux-default'.
    All other blocks are preserved byte-for-byte.
    Creates backups before modification.
    """
    result = {'cleaned': False, 'error': None}
    
    try:
        # Read both files
        cfg_text, err = _pve_read_file(_PVE_NOTIFICATIONS_CFG)
        if err:
            result['error'] = err
            return jsonify(result), 200
        
        priv_text, err = _pve_read_file(_PVE_PRIV_CFG)
        if err:
            priv_text = None
        
        # Check if our blocks actually exist before doing anything
        has_our_blocks = any(
            h in cfg_text for h in [f'webhook: {_PVE_ENDPOINT_ID}', f'matcher: {_PVE_MATCHER_ID}']
        )
        has_priv_blocks = priv_text and f'webhook: {_PVE_ENDPOINT_ID}' in priv_text
        
        if not has_our_blocks and not has_priv_blocks:
            result['cleaned'] = True
            return jsonify(result), 200
        
        # Backup before modification
        _pve_backup_file(_PVE_NOTIFICATIONS_CFG)
        if priv_text is not None:
            _pve_backup_file(_PVE_PRIV_CFG)
        
        # Remove our blocks
        if has_our_blocks:
            cleaned_cfg = _pve_remove_our_blocks(cfg_text, _PVE_OUR_HEADERS)
            try:
                with open(_PVE_NOTIFICATIONS_CFG, 'w') as f:
                    f.write(cleaned_cfg)
            except PermissionError:
                result['error'] = f'Permission denied writing {_PVE_NOTIFICATIONS_CFG}'
                return jsonify(result), 200
            except Exception as e:
                # Rollback
                try:
                    with open(_PVE_NOTIFICATIONS_CFG, 'w') as f:
                        f.write(cfg_text)
                except Exception:
                    pass
                result['error'] = str(e)
                return jsonify(result), 200
        
        if has_priv_blocks and priv_text is not None:
            cleaned_priv = _pve_remove_our_blocks(priv_text, _PVE_OUR_HEADERS)
            try:
                with open(_PVE_PRIV_CFG, 'w') as f:
                    f.write(cleaned_priv)
            except Exception:
                pass  # Best-effort
        
        result['cleaned'] = True
        return jsonify(result), 200
    
    except Exception as e:
        result['error'] = str(e)
        return jsonify(result), 200


@notification_bp.route('/api/notifications/proxmox/read-cfg', methods=['GET'])
def read_pve_notification_cfg():
    """Diagnostic: return raw content of PVE notification config files.
    
    GET /api/notifications/proxmox/read-cfg
    Returns both notifications.cfg and priv/notifications.cfg content.
    """
    import os
    
    files = {
        'notifications_cfg': '/etc/pve/notifications.cfg',
        'priv_cfg': '/etc/pve/priv/notifications.cfg',
    }
    
    # Also look for any backups we created
    backup_dir = '/etc/pve'
    priv_backup_dir = '/etc/pve/priv'
    
    result = {}
    for key, path in files.items():
        try:
            with open(path, 'r') as f:
                result[key] = {
                    'path': path,
                    'content': f.read(),
                    'size': os.path.getsize(path),
                    'error': None,
                }
        except FileNotFoundError:
            result[key] = {'path': path, 'content': None, 'size': 0, 'error': 'file_not_found'}
        except PermissionError:
            result[key] = {'path': path, 'content': None, 'size': 0, 'error': 'permission_denied'}
        except Exception as e:
            result[key] = {'path': path, 'content': None, 'size': 0, 'error': str(e)}
    
    # Find backups
    backups = []
    for d in [backup_dir, priv_backup_dir]:
        try:
            for fname in sorted(os.listdir(d)):
                if 'proxmenux_backup' in fname:
                    fpath = os.path.join(d, fname)
                    try:
                        with open(fpath, 'r') as f:
                            backups.append({
                                'path': fpath,
                                'content': f.read(),
                                'size': os.path.getsize(fpath),
                            })
                    except Exception:
                        backups.append({'path': fpath, 'content': None, 'error': 'read_failed'})
        except Exception:
            pass
    
    result['backups'] = backups
    return jsonify(result), 200


@notification_bp.route('/api/notifications/proxmox/restore-cfg', methods=['POST'])
def restore_pve_notification_cfg():
    """Restore PVE notification config from our backup.
    
    POST /api/notifications/proxmox/restore-cfg
    Finds the most recent proxmenux_backup and restores it.
    """
    import os
    import shutil
    
    files_to_restore = {
        '/etc/pve': '/etc/pve/notifications.cfg',
        '/etc/pve/priv': '/etc/pve/priv/notifications.cfg',
    }
    
    restored = []
    errors = []
    
    for search_dir, target_path in files_to_restore.items():
        try:
            candidates = sorted([
                f for f in os.listdir(search_dir)
                if 'proxmenux_backup' in f and f.startswith('notifications.cfg')
            ], reverse=True)
            
            if candidates:
                backup_path = os.path.join(search_dir, candidates[0])
                shutil.copy2(backup_path, target_path)
                restored.append({'target': target_path, 'from_backup': backup_path})
            else:
                errors.append({'target': target_path, 'error': 'no_backup_found'})
        except Exception as e:
            errors.append({'target': target_path, 'error': str(e)})
    
    return jsonify({
        'restored': restored,
        'errors': errors,
        'success': len(errors) == 0 and len(restored) > 0,
    }), 200


@notification_bp.route('/api/notifications/webhook', methods=['POST'])
def proxmox_webhook():
    """Receive native Proxmox VE notification webhooks (hardened).
    
    Security layers:
      Localhost (127.0.0.1 / ::1): rate limiting only.
        PVE calls us on localhost and cannot send custom auth headers,
        so we trust the loopback interface (only local processes can reach it).
      Remote: rate limiting + shared secret + timestamp + replay + IP allowlist.
    """
    _reject = lambda code, error, status: (jsonify({'accepted': False, 'error': error}), status)
    
    client_ip = request.remote_addr or ''
    is_localhost = client_ip in ('127.0.0.1', '::1')
    
    # ── Layer 1: Rate limiting (always) ──
    if not _webhook_limiter.allow():
        resp = jsonify({'accepted': False, 'error': 'rate_limited'})
        resp.headers['Retry-After'] = '60'
        return resp, 429
    
    # ── Layers 2-5: Remote-only checks ──
    if not is_localhost:
        # Layer 2: Shared secret
        try:
            configured_secret = notification_manager.get_webhook_secret()
        except Exception:
            configured_secret = ''
        
        if configured_secret:
            request_secret = request.headers.get('X-Webhook-Secret', '')
            if not request_secret:
                return _reject(401, 'missing_secret', 401)
            if not hmac.compare_digest(configured_secret, request_secret):
                return _reject(401, 'invalid_secret', 401)
        
        # Layer 3: Anti-replay timestamp
        ts_header = request.headers.get('X-ProxMenux-Timestamp', '')
        if not ts_header:
            return _reject(401, 'missing_timestamp', 401)
        try:
            ts_value = int(ts_header)
        except (ValueError, TypeError):
            return _reject(401, 'invalid_timestamp', 401)
        if abs(time.time() - ts_value) > _TIMESTAMP_MAX_DRIFT:
            return _reject(401, 'timestamp_expired', 401)
        
        # Layer 4: Replay cache
        raw_body = request.get_data(as_text=True) or ''
        signature = hashlib.sha256(f"{ts_value}:{raw_body}".encode(errors='replace')).hexdigest()
        if _replay_cache.check_and_record(signature):
            return _reject(409, 'replay_detected', 409)
        
        # Layer 5: IP allowlist
        try:
            allowed_ips = notification_manager.get_webhook_allowed_ips()
            if allowed_ips and client_ip not in allowed_ips:
                return _reject(403, 'forbidden_ip', 403)
        except Exception:
            pass
    
    # ── Parse and process payload ──
    try:
        payload = request.get_json(silent=True) or {}
        if not payload:
            payload = dict(request.form)
        if not payload:
            return _reject(400, 'invalid_payload', 400)
        
        result = notification_manager.process_webhook(payload)
        status_code = 200 if result.get('accepted') else 400
        return jsonify(result), status_code
    except Exception:
        return jsonify({'accepted': False, 'error': 'internal_error'}), 500
