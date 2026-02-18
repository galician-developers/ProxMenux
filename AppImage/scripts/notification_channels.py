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
        
        if data:
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
    except Exception as e:
        print(f"[NotificationChannels] Failed to create {channel_type}: {e}")
    return None
