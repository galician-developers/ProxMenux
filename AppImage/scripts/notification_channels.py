"""
ProxMenux Notification Channels
Provides transport adapters for Telegram, Gotify, and Discord.

Each channel implements send() and test() with:
- Retry with exponential backoff (3 attempts)
- Request timeout of 10s
- Rate limiting (max 30 msg/min per channel)

Author: MacRimi
"""

import json
import time
import urllib.request
import urllib.error
import urllib.parse
from abc import ABC, abstractmethod
from collections import deque
from typing import Tuple, Optional, Dict, Any


# ─── Rate Limiter ────────────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter: max N messages per window."""
    
    def __init__(self, max_calls: int = 30, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window = window_seconds
        self._timestamps: deque = deque()
    
    def allow(self) -> bool:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self.window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls:
            return False
        self._timestamps.append(now)
        return True
    
    def wait_time(self) -> float:
        if not self._timestamps:
            return 0.0
        return max(0.0, self.window - (time.monotonic() - self._timestamps[0]))


# ─── Base Channel ────────────────────────────────────────────────

class NotificationChannel(ABC):
    """Abstract base for all notification channels."""
    
    MAX_RETRIES = 3
    RETRY_DELAYS = [2, 4, 8]  # exponential backoff seconds
    REQUEST_TIMEOUT = 10
    
    def __init__(self):
        self._rate_limiter = RateLimiter(max_calls=30, window_seconds=60)
    
    @abstractmethod
    def send(self, title: str, message: str, severity: str = 'INFO',
             data: Optional[Dict] = None) -> Dict[str, Any]:
        """Send a notification. Returns {success, error, channel}."""
        pass
    
    @abstractmethod
    def test(self) -> Tuple[bool, str]:
        """Send a test message. Returns (success, error_message)."""
        pass
    
    @abstractmethod
    def validate_config(self) -> Tuple[bool, str]:
        """Check if config is valid without sending. Returns (valid, error)."""
        pass
    
    def _http_request(self, url: str, data: bytes, headers: Dict[str, str],
                      method: str = 'POST') -> Tuple[int, str]:
        """Execute HTTP request with timeout. Returns (status_code, body)."""
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as resp:
                body = resp.read().decode('utf-8', errors='replace')
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace') if e.fp else str(e)
            return e.code, body
        except urllib.error.URLError as e:
            return 0, str(e.reason)
        except Exception as e:
            return 0, str(e)
    
    def _send_with_retry(self, send_fn) -> Dict[str, Any]:
        """Wrap a send function with rate limiting and retry logic."""
        if not self._rate_limiter.allow():
            wait = self._rate_limiter.wait_time()
            return {
                'success': False,
                'error': f'Rate limited. Retry in {wait:.0f}s',
                'rate_limited': True
            }
        
        last_error = ''
        for attempt in range(self.MAX_RETRIES):
            try:
                status, body = send_fn()
                if 200 <= status < 300:
                    return {'success': True, 'error': None}
                last_error = f'HTTP {status}: {body[:200]}'
            except Exception as e:
                last_error = str(e)
            
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAYS[attempt])
        
        return {'success': False, 'error': last_error}


# ─── Telegram ────────────────────────────────────────────────────

class TelegramChannel(NotificationChannel):
    """Telegram Bot API channel using HTML parse mode."""
    
    API_BASE = 'https://api.telegram.org/bot{token}/sendMessage'
    MAX_LENGTH = 4096
    
    SEVERITY_ICONS = {
        'CRITICAL': '\U0001F534',  # red circle
        'WARNING':  '\U0001F7E1',  # yellow circle
        'INFO':     '\U0001F535',  # blue circle
        'OK':       '\U0001F7E2',  # green circle
        'UNKNOWN':  '\u26AA',      # white circle
    }
    
    def __init__(self, bot_token: str, chat_id: str):
        super().__init__()
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()
    
    def validate_config(self) -> Tuple[bool, str]:
        if not self.bot_token:
            return False, 'Bot token is required'
        if not self.chat_id:
            return False, 'Chat ID is required'
        if ':' not in self.bot_token:
            return False, 'Invalid bot token format (expected BOT_ID:TOKEN)'
        return True, ''
    
    def send(self, title: str, message: str, severity: str = 'INFO',
             data: Optional[Dict] = None) -> Dict[str, Any]:
        icon = self.SEVERITY_ICONS.get(severity, self.SEVERITY_ICONS['INFO'])
        html_msg = f"<b>{icon} {self._escape_html(title)}</b>\n\n{self._escape_html(message)}"
        
        # Split long messages
        chunks = self._split_message(html_msg)
        result = {'success': True, 'error': None, 'channel': 'telegram'}
        
        for chunk in chunks:
            res = self._send_with_retry(lambda c=chunk: self._post_message(c))
            if not res['success']:
                result = {**res, 'channel': 'telegram'}
                break
        
        return result
    
    def test(self) -> Tuple[bool, str]:
        valid, err = self.validate_config()
        if not valid:
            return False, err
        
        result = self.send(
            'ProxMenux Test',
            'Notification service is working correctly.\nThis is a test message from ProxMenux Monitor.',
            'INFO'
        )
        return result['success'], result.get('error', '')
    
    def _post_message(self, text: str) -> Tuple[int, str]:
        url = self.API_BASE.format(token=self.bot_token)
        payload = json.dumps({
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }).encode('utf-8')
        
        return self._http_request(url, payload, {'Content-Type': 'application/json'})
    
    def _split_message(self, text: str) -> list:
        if len(text) <= self.MAX_LENGTH:
            return [text]
        chunks = []
        while text:
            if len(text) <= self.MAX_LENGTH:
                chunks.append(text)
                break
            split_at = text.rfind('\n', 0, self.MAX_LENGTH)
            if split_at == -1:
                split_at = self.MAX_LENGTH
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip('\n')
        return chunks
    
    @staticmethod
    def _escape_html(text: str) -> str:
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))


# ─── Gotify ──────────────────────────────────────────────────────

class GotifyChannel(NotificationChannel):
    """Gotify push notification channel with priority mapping."""
    
    PRIORITY_MAP = {
        'OK':       1,
        'INFO':     2,
        'UNKNOWN':  3,
        'WARNING':  5,
        'CRITICAL': 10,
    }
    
    def __init__(self, server_url: str, app_token: str):
        super().__init__()
        self.server_url = server_url.rstrip('/').strip()
        self.app_token = app_token.strip()
    
    def validate_config(self) -> Tuple[bool, str]:
        if not self.server_url:
            return False, 'Server URL is required'
        if not self.app_token:
            return False, 'Application token is required'
        if not self.server_url.startswith(('http://', 'https://')):
            return False, 'Server URL must start with http:// or https://'
        return True, ''
    
    def send(self, title: str, message: str, severity: str = 'INFO',
             data: Optional[Dict] = None) -> Dict[str, Any]:
        priority = self.PRIORITY_MAP.get(severity, 2)
        
        result = self._send_with_retry(
            lambda: self._post_message(title, message, priority)
        )
        result['channel'] = 'gotify'
        return result
    
    def test(self) -> Tuple[bool, str]:
        valid, err = self.validate_config()
        if not valid:
            return False, err
        
        result = self.send(
            'ProxMenux Test',
            'Notification service is working correctly.\nThis is a test message from ProxMenux Monitor.',
            'INFO'
        )
        return result['success'], result.get('error', '')
    
    def _post_message(self, title: str, message: str, priority: int) -> Tuple[int, str]:
        url = f"{self.server_url}/message?token={self.app_token}"
        payload = json.dumps({
            'title': title,
            'message': message,
            'priority': priority,
            'extras': {
                'client::display': {'contentType': 'text/markdown'}
            }
        }).encode('utf-8')
        
        return self._http_request(url, payload, {'Content-Type': 'application/json'})


# ─── Discord ─────────────────────────────────────────────────────

class DiscordChannel(NotificationChannel):
    """Discord webhook channel with color-coded embeds."""
    
    MAX_EMBED_DESC = 2048
    
    SEVERITY_COLORS = {
        'CRITICAL': 0xED4245,   # red
        'WARNING':  0xFEE75C,   # yellow
        'INFO':     0x5865F2,   # blurple
        'OK':       0x57F287,   # green
        'UNKNOWN':  0x99AAB5,   # grey
    }
    
    def __init__(self, webhook_url: str):
        super().__init__()
        self.webhook_url = webhook_url.strip()
    
    def validate_config(self) -> Tuple[bool, str]:
        if not self.webhook_url:
            return False, 'Webhook URL is required'
        if 'discord.com/api/webhooks/' not in self.webhook_url:
            return False, 'Invalid Discord webhook URL'
        return True, ''
    
    def send(self, title: str, message: str, severity: str = 'INFO',
             data: Optional[Dict] = None) -> Dict[str, Any]:
        color = self.SEVERITY_COLORS.get(severity, 0x5865F2)
        
        desc = message[:self.MAX_EMBED_DESC] if len(message) > self.MAX_EMBED_DESC else message
        
        embed = {
            'title': title,
            'description': desc,
            'color': color,
            'footer': {'text': 'ProxMenux Monitor'},
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        
        # Use structured fields from render_template if available
        rendered_fields = (data or {}).get('_rendered_fields', [])
        if rendered_fields:
            embed['fields'] = [
                {'name': name, 'value': val[:1024], 'inline': True}
                for name, val in rendered_fields[:25]  # Discord limit: 25 fields
            ]
        elif data:
            fields = []
            if data.get('category'):
                fields.append({'name': 'Category', 'value': data['category'], 'inline': True})
            if data.get('hostname'):
                fields.append({'name': 'Host', 'value': data['hostname'], 'inline': True})
            if data.get('severity'):
                fields.append({'name': 'Severity', 'value': data['severity'], 'inline': True})
            if fields:
                embed['fields'] = fields
        
        result = self._send_with_retry(
            lambda: self._post_webhook(embed)
        )
        result['channel'] = 'discord'
        return result
    
    def test(self) -> Tuple[bool, str]:
        valid, err = self.validate_config()
        if not valid:
            return False, err
        
        result = self.send(
            'ProxMenux Test',
            'Notification service is working correctly.\nThis is a test message from ProxMenux Monitor.',
            'INFO'
        )
        return result['success'], result.get('error', '')
    
    def _post_webhook(self, embed: Dict) -> Tuple[int, str]:
        payload = json.dumps({
            'username': 'ProxMenux',
            'embeds': [embed]
        }).encode('utf-8')
        
        return self._http_request(
            self.webhook_url, payload, {'Content-Type': 'application/json'}
        )


# ─── Email Channel ──────────────────────────────────────────────

class EmailChannel(NotificationChannel):
    """Email notification channel using SMTP (smtplib) or sendmail fallback.
    
    Config keys:
      host, port, username, password, tls_mode (none|starttls|ssl),
      from_address, to_addresses (comma-separated), subject_prefix, timeout
    """
    
    def __init__(self, config: Dict[str, str]):
        super().__init__()
        self.host = config.get('host', '')
        self.port = int(config.get('port', 587) or 587)
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.tls_mode = config.get('tls_mode', 'starttls')  # none | starttls | ssl
        self.from_address = config.get('from_address', '')
        self.to_addresses = self._parse_recipients(config.get('to_addresses', ''))
        self.subject_prefix = config.get('subject_prefix', '[ProxMenux]')
        self.timeout = int(config.get('timeout', 10) or 10)
    
    @staticmethod
    def _parse_recipients(raw) -> list:
        if isinstance(raw, list):
            return [a.strip() for a in raw if a.strip()]
        return [addr.strip() for addr in str(raw).split(',') if addr.strip()]
    
    def validate_config(self) -> Tuple[bool, str]:
        if not self.to_addresses:
            return False, 'No recipients configured'
        if not self.from_address:
            return False, 'No from address configured'
        # Must have SMTP host OR local sendmail available
        if not self.host:
            import os
            if not os.path.exists('/usr/sbin/sendmail'):
                return False, 'No SMTP host configured and /usr/sbin/sendmail not found'
        return True, ''
    
    def send(self, title: str, message: str, severity: str = 'INFO',
             data: Optional[Dict] = None) -> Dict[str, Any]:
        subject = f"{self.subject_prefix} [{severity}] {title}"
        
        def _do_send():
            if self.host:
                return self._send_smtp(subject, message, severity)
            else:
                return self._send_sendmail(subject, message, severity)
        
        return self._send_with_retry(_do_send)
    
    def _send_smtp(self, subject: str, body: str, severity: str) -> Tuple[int, str]:
        import smtplib
        from email.message import EmailMessage
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = self.from_address
        msg['To'] = ', '.join(self.to_addresses)
        msg.set_content(body)
        
        # Add HTML alternative
        html_body = self._format_html(subject, body, severity)
        if html_body:
            msg.add_alternative(html_body, subtype='html')
        
        try:
            if self.tls_mode == 'ssl':
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                if self.tls_mode == 'starttls':
                    server.starttls()
            
            if self.username and self.password:
                server.login(self.username, self.password)
            
            server.send_message(msg)
            server.quit()
            return 200, 'OK'
        except smtplib.SMTPAuthenticationError as e:
            return 0, f'SMTP authentication failed: {e}'
        except smtplib.SMTPConnectError as e:
            return 0, f'SMTP connection failed: {e}'
        except smtplib.SMTPException as e:
            return 0, f'SMTP error: {e}'
        except (OSError, TimeoutError) as e:
            return 0, f'Connection error: {e}'
    
    def _send_sendmail(self, subject: str, body: str, severity: str) -> Tuple[int, str]:
        import os
        import subprocess
        from email.message import EmailMessage
        
        sendmail = '/usr/sbin/sendmail'
        if not os.path.exists(sendmail):
            return 0, 'sendmail not found at /usr/sbin/sendmail'
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = self.from_address or 'proxmenux@localhost'
        msg['To'] = ', '.join(self.to_addresses)
        msg.set_content(body)
        
        try:
            proc = subprocess.run(
                [sendmail, '-t', '-oi'],
                input=msg.as_string(), capture_output=True, text=True, timeout=30
            )
            if proc.returncode == 0:
                return 200, 'OK'
            return 0, f'sendmail failed (rc={proc.returncode}): {proc.stderr[:200]}'
        except subprocess.TimeoutExpired:
            return 0, 'sendmail timed out after 30s'
        except Exception as e:
            return 0, f'sendmail error: {e}'
    
    @staticmethod
    def _format_html(subject: str, body: str, severity: str) -> str:
        """Create professional HTML email."""
        import html as html_mod
        
        severity_colors = {'CRITICAL': '#dc2626', 'WARNING': '#f59e0b', 'INFO': '#3b82f6'}
        color = severity_colors.get(severity, '#6b7280')
        
        body_html = ''.join(
            f'<p style="margin:4px 0;color:#374151;">{html_mod.escape(line)}</p>'
            for line in body.split('\n') if line.strip()
        )
        
        return f'''<!DOCTYPE html>
<html><body style="font-family:-apple-system,Arial,sans-serif;background:#f3f4f6;padding:20px;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
  <div style="background:{color};padding:16px 24px;">
    <h2 style="color:#fff;margin:0;font-size:16px;">ProxMenux Monitor</h2>
    <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:13px;">{html_mod.escape(severity)} Alert</p>
  </div>
  <div style="padding:24px;">
    <h3 style="margin:0 0 12px;color:#111827;">{html_mod.escape(subject)}</h3>
    {body_html}
  </div>
  <div style="background:#f9fafb;padding:12px 24px;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:11px;color:#9ca3af;">Sent by ProxMenux Notification Service</p>
  </div>
</div>
</body></html>'''
    
    def test(self) -> Tuple[bool, str]:
        result = self.send(
            'ProxMenux Test Notification',
            'This is a test notification from ProxMenux Monitor.\n'
            'If you received this, your email channel is working correctly.',
            'INFO'
        )
        return result.get('success', False), result.get('error', '')


# ─── Channel Factory ─────────────────────────────────────────────

CHANNEL_TYPES = {
    'telegram': {
        'name': 'Telegram',
        'config_keys': ['bot_token', 'chat_id'],
        'class': TelegramChannel,
    },
    'gotify': {
        'name': 'Gotify',
        'config_keys': ['url', 'token'],
        'class': GotifyChannel,
    },
    'discord': {
        'name': 'Discord',
        'config_keys': ['webhook_url'],
        'class': DiscordChannel,
    },
    'email': {
        'name': 'Email (SMTP)',
        'config_keys': ['host', 'port', 'username', 'password', 'tls_mode',
                        'from_address', 'to_addresses', 'subject_prefix'],
        'class': EmailChannel,
    },
}


def create_channel(channel_type: str, config: Dict[str, str]) -> Optional[NotificationChannel]:
    """Create a channel instance from type name and config dict.
    
    Args:
        channel_type: 'telegram', 'gotify', or 'discord'
        config: Dict with channel-specific keys (see CHANNEL_TYPES)
    
    Returns:
        Channel instance or None if creation fails
    """
    try:
        if channel_type == 'telegram':
            return TelegramChannel(
                bot_token=config.get('bot_token', ''),
                chat_id=config.get('chat_id', '')
            )
        elif channel_type == 'gotify':
            return GotifyChannel(
                server_url=config.get('url', ''),
                app_token=config.get('token', '')
            )
        elif channel_type == 'discord':
            return DiscordChannel(
                webhook_url=config.get('webhook_url', '')
            )
        elif channel_type == 'email':
            return EmailChannel(config)
    except Exception as e:
        print(f"[NotificationChannels] Failed to create {channel_type}: {e}")
    return None
