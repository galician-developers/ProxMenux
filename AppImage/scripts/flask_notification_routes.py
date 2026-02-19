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
    
    Idempotent: safe to call multiple times. Only creates/updates
    ProxMenux-owned objects (proxmenux-webhook endpoint, proxmenux-default matcher).
    Never deletes or overrides user notification targets.
    """
    import subprocess
    import secrets as secrets_mod
    
    ENDPOINT_ID = 'proxmenux-webhook'
    MATCHER_ID = 'proxmenux-default'
    WEBHOOK_URL = 'http://127.0.0.1:8008/api/notifications/webhook'
    
    result = {
        'configured': False,
        'endpoint_id': ENDPOINT_ID,
        'matcher_id': MATCHER_ID,
        'url': WEBHOOK_URL,
        'fallback_commands': [],
        'error': None,
    }
    
    def _run_pvesh(args: list, check: bool = True) -> tuple:
        """Run pvesh command. Returns (success, stdout, stderr)."""
        try:
            proc = subprocess.run(
                ['pvesh'] + args,
                capture_output=True, text=True, timeout=15
            )
            return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()
        except FileNotFoundError:
            return False, '', 'pvesh not found'
        except subprocess.TimeoutExpired:
            return False, '', 'pvesh timed out'
        except Exception as e:
            return False, '', str(e)
    
    try:
        # Step 1: Ensure webhook secret exists
        secret = notification_manager.get_webhook_secret()
        if not secret:
            secret = secrets_mod.token_urlsafe(32)
            notification_manager._save_setting('webhook_secret', secret)
        
        secret_header = f'X-Webhook-Secret={secret}'
        
        # Step 2: Check if endpoint already exists
        exists_ok, _, _ = _run_pvesh([
            'get', f'/cluster/notifications/endpoints/webhook/{ENDPOINT_ID}',
            '--output-format', 'json'
        ])
        
        if exists_ok:
            # Update existing endpoint
            ok, _, err = _run_pvesh([
                'set', f'/cluster/notifications/endpoints/webhook/{ENDPOINT_ID}',
                '--url', WEBHOOK_URL,
                '--method', 'post',
                '--header', secret_header,
            ])
        else:
            # Create new endpoint
            ok, _, err = _run_pvesh([
                'create', '/cluster/notifications/endpoints/webhook',
                '--name', ENDPOINT_ID,
                '--url', WEBHOOK_URL,
                '--method', 'post',
                '--header', secret_header,
            ])
        
        if not ok:
            # Build fallback commands for manual execution
            result['fallback_commands'] = [
                f'pvesh create /cluster/notifications/endpoints/webhook '
                f'--name {ENDPOINT_ID} --url {WEBHOOK_URL} --method post '
                f'--header "{secret_header}"',
                f'pvesh create /cluster/notifications/matchers '
                f'--name {MATCHER_ID} --target {ENDPOINT_ID} '
                f'--match-severity warning,error',
            ]
            result['error'] = f'Failed to configure endpoint: {err}'
            return jsonify(result), 200
        
        # Step 3: Create or update matcher
        matcher_exists, _, _ = _run_pvesh([
            'get', f'/cluster/notifications/matchers/{MATCHER_ID}',
            '--output-format', 'json'
        ])
        
        if matcher_exists:
            ok_m, _, err_m = _run_pvesh([
                'set', f'/cluster/notifications/matchers/{MATCHER_ID}',
                '--target', ENDPOINT_ID,
                '--match-severity', 'warning,error',
            ])
        else:
            ok_m, _, err_m = _run_pvesh([
                'create', '/cluster/notifications/matchers',
                '--name', MATCHER_ID,
                '--target', ENDPOINT_ID,
                '--match-severity', 'warning,error',
            ])
        
        if not ok_m:
            result['fallback_commands'] = [
                f'pvesh create /cluster/notifications/matchers '
                f'--name {MATCHER_ID} --target {ENDPOINT_ID} '
                f'--match-severity warning,error',
            ]
            result['error'] = f'Endpoint OK, but matcher failed: {err_m}'
            result['configured'] = False
            return jsonify(result), 200
        
        result['configured'] = True
        result['secret'] = secret  # Return so UI can display it
        return jsonify(result), 200
    
    except Exception as e:
        result['error'] = str(e)
        result['fallback_commands'] = [
            f'pvesh create /cluster/notifications/endpoints/webhook '
            f'--name {ENDPOINT_ID} --url {WEBHOOK_URL} --method post '
            f'--header "X-Webhook-Secret=YOUR_SECRET"',
            f'pvesh create /cluster/notifications/matchers '
            f'--name {MATCHER_ID} --target {ENDPOINT_ID} '
            f'--match-severity warning,error',
        ]
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
