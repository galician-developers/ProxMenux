"""
Flask routes for notification service configuration and management.
Blueprint pattern matching flask_health_routes.py / flask_security_routes.py.
"""

from flask import Blueprint, jsonify, request
from notification_manager import notification_manager

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
