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


@notification_bp.route('/api/notifications/proxmox/setup-webhook', methods=['POST'])
def setup_proxmox_webhook():
    """Automatically configure PVE notifications to call our webhook.
    
    Writes directly to /etc/pve/notifications.cfg (cluster filesystem)
    instead of using pvesh, which has complex property-string formats
    for headers and secrets that vary between PVE versions.
    
    Idempotent: safe to call multiple times.
    Only creates/updates ProxMenux-owned objects.
    """
    import re
    import secrets as secrets_mod
    
    ENDPOINT_ID = 'proxmenux-webhook'
    MATCHER_ID = 'proxmenux-default'
    WEBHOOK_URL = 'http://127.0.0.1:8008/api/notifications/webhook'
    NOTIFICATIONS_CFG = '/etc/pve/notifications.cfg'
    PRIV_CFG = '/etc/pve/priv/notifications.cfg'
    
    result = {
        'configured': False,
        'endpoint_id': ENDPOINT_ID,
        'matcher_id': MATCHER_ID,
        'url': WEBHOOK_URL,
        'fallback_commands': [],
        'error': None,
    }
    
    def _build_fallback(secret_val):
        """Build manual instructions as fallback."""
        return [
            "# Add to /etc/pve/notifications.cfg:",
            f"webhook: {ENDPOINT_ID}",
            f"\turl {WEBHOOK_URL}",
            "\tmethod post",
            f"\theader Content-Type:application/json",
            f"\theader X-Webhook-Secret:{{{{ secrets.proxmenux_secret }}}}",
            "",
            "# Add to /etc/pve/priv/notifications.cfg:",
            f"webhook: {ENDPOINT_ID}",
            f"\tsecret proxmenux_secret {secret_val}",
            "",
            "# Also add a matcher block to /etc/pve/notifications.cfg:",
            f"matcher: {MATCHER_ID}",
            f"\ttarget {ENDPOINT_ID}",
            "\tmatch-severity warning,error",
        ]
    
    try:
        # ── Step 1: Ensure webhook secret exists ──
        secret = notification_manager.get_webhook_secret()
        if not secret:
            secret = secrets_mod.token_urlsafe(32)
            notification_manager._save_setting('webhook_secret', secret)
        
        # ── Step 2: Read current config ──
        try:
            with open(NOTIFICATIONS_CFG, 'r') as f:
                cfg_text = f.read()
        except FileNotFoundError:
            cfg_text = ''
        except PermissionError:
            result['error'] = f'Permission denied reading {NOTIFICATIONS_CFG}'
            result['fallback_commands'] = _build_fallback(secret)
            return jsonify(result), 200
        
        # Read private config (secrets)
        try:
            with open(PRIV_CFG, 'r') as f:
                priv_text = f.read()
        except FileNotFoundError:
            priv_text = ''
        except PermissionError:
            result['error'] = f'Permission denied reading {PRIV_CFG}'
            result['fallback_commands'] = _build_fallback(secret)
            return jsonify(result), 200
        
        # ── Step 3: Build / replace our endpoint block ──
        endpoint_block = (
            f"webhook: {ENDPOINT_ID}\n"
            f"\turl {WEBHOOK_URL}\n"
            f"\tmethod post\n"
            f"\theader Content-Type:application/json\n"
            f"\theader X-Webhook-Secret:{{{{ secrets.proxmenux_secret }}}}\n"
        )
        
        # Regex to find existing block: "webhook: proxmenux-webhook\n..." until next block or EOF
        block_re = re.compile(
            rf'^webhook:\s+{re.escape(ENDPOINT_ID)}\n(?:\t[^\n]*\n)*',
            re.MULTILINE
        )
        
        if block_re.search(cfg_text):
            cfg_text = block_re.sub(endpoint_block, cfg_text)
        else:
            # Append with blank line separator
            if cfg_text and not cfg_text.endswith('\n\n'):
                cfg_text = cfg_text.rstrip('\n') + '\n\n'
            cfg_text += endpoint_block
        
        # ── Step 4: Build / replace matcher block ──
        matcher_block = (
            f"matcher: {MATCHER_ID}\n"
            f"\ttarget {ENDPOINT_ID}\n"
            f"\tmatch-severity warning,error\n"
        )
        
        matcher_re = re.compile(
            rf'^matcher:\s+{re.escape(MATCHER_ID)}\n(?:\t[^\n]*\n)*',
            re.MULTILINE
        )
        
        if matcher_re.search(cfg_text):
            cfg_text = matcher_re.sub(matcher_block, cfg_text)
        else:
            if not cfg_text.endswith('\n\n'):
                cfg_text = cfg_text.rstrip('\n') + '\n\n'
            cfg_text += matcher_block
        
        # ── Step 5: Build / replace private config (secret) ──
        priv_block = (
            f"webhook: {ENDPOINT_ID}\n"
            f"\tsecret proxmenux_secret {secret}\n"
        )
        
        priv_re = re.compile(
            rf'^webhook:\s+{re.escape(ENDPOINT_ID)}\n(?:\t[^\n]*\n)*',
            re.MULTILINE
        )
        
        if priv_re.search(priv_text):
            priv_text = priv_re.sub(priv_block, priv_text)
        else:
            if priv_text and not priv_text.endswith('\n\n'):
                priv_text = priv_text.rstrip('\n') + '\n\n'
            priv_text += priv_block
        
        # ── Step 6: Write back ──
        try:
            with open(NOTIFICATIONS_CFG, 'w') as f:
                f.write(cfg_text)
        except PermissionError:
            result['error'] = f'Permission denied writing {NOTIFICATIONS_CFG}'
            result['fallback_commands'] = _build_fallback(secret)
            return jsonify(result), 200
        
        try:
            with open(PRIV_CFG, 'w') as f:
                f.write(priv_text)
        except PermissionError:
            # Rollback is complex; just warn
            result['error'] = (
                f'Endpoint configured but secret could not be written to {PRIV_CFG}. '
                f'Add manually: secret proxmenux_secret {secret}'
            )
            result['fallback_commands'] = [
                f"# Add to {PRIV_CFG}:",
                f"webhook: {ENDPOINT_ID}",
                f"\tsecret proxmenux_secret {secret}",
            ]
            return jsonify(result), 200
        
        result['configured'] = True
        result['secret'] = secret
        return jsonify(result), 200
    
    except Exception as e:
        result['error'] = str(e)
        try:
            result['fallback_commands'] = _build_fallback(
                notification_manager.get_webhook_secret() or 'YOUR_SECRET'
            )
        except Exception:
            result['fallback_commands'] = _build_fallback('YOUR_SECRET')
        return jsonify(result), 200


@notification_bp.route('/api/notifications/webhook', methods=['POST'])
def proxmox_webhook():
    """Receive native Proxmox VE notification webhooks (hardened).
    
    Security layers:
      1. Rate limiting (60 req/min) -- always
      2. Shared secret (X-Webhook-Secret) -- always required
      3. Anti-replay timestamp (60s window) -- remote only
      4. Replay cache (signature dedup) -- remote only
      5. IP allowlist (optional) -- remote only
    
    Localhost callers (127.0.0.1 / ::1) bypass layers 3-5 because Proxmox
    cannot inject dynamic timestamp headers. The shared secret is still
    required for localhost to prevent any local process from injecting events.
    """
    _reject = lambda code, error, status: (jsonify({'accepted': False, 'error': error}), status)
    
    client_ip = request.remote_addr or ''
    is_localhost = client_ip in ('127.0.0.1', '::1')
    
    # ── Layer 1: Rate limiting (always) ──
    if not _webhook_limiter.allow():
        resp = jsonify({'accepted': False, 'error': 'rate_limited'})
        resp.headers['Retry-After'] = '60'
        return resp, 429
    
    # ── Layer 2: Shared secret (always required) ──
    try:
        configured_secret = notification_manager.get_webhook_secret()
    except Exception:
        configured_secret = ''
    
    if not configured_secret:
        return _reject(500, 'webhook_not_configured', 500)
    
    request_secret = request.headers.get('X-Webhook-Secret', '')
    if not request_secret:
        return _reject(401, 'missing_secret', 401)
    if not hmac.compare_digest(configured_secret, request_secret):
        return _reject(401, 'invalid_secret', 401)
    
    # ── Layers 3-5: Remote-only checks ──
    if not is_localhost:
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
