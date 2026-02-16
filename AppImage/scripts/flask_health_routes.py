"""
Flask routes for health monitoring with persistence support
"""

from flask import Blueprint, jsonify, request
from health_monitor import health_monitor
from health_persistence import health_persistence

health_bp = Blueprint('health', __name__)

@health_bp.route('/api/health/status', methods=['GET'])
def get_health_status():
    """Get overall health status summary"""
    try:
        status = health_monitor.get_overall_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/details', methods=['GET'])
def get_health_details():
    """Get detailed health status with all checks"""
    try:
        details = health_monitor.get_detailed_status()
        return jsonify(details)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/system-info', methods=['GET'])
def get_system_info():
    """
    Get lightweight system info for header display.
    Returns: hostname, uptime, and health status with proper structure.
    """
    try:
        info = health_monitor.get_system_info()
        
        if 'health' in info:
            status_map = {
                'OK': 'healthy',
                'WARNING': 'warning',
                'CRITICAL': 'critical',
                'UNKNOWN': 'warning'
            }
            current_status = info['health'].get('status', 'OK').upper()
            info['health']['status'] = status_map.get(current_status, 'healthy')
        
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/acknowledge', methods=['POST'])
def acknowledge_error():
    """
    Acknowledge/dismiss an error manually.
    Returns details about the acknowledged error including original severity
    and suppression period info.
    """
    try:
        data = request.get_json()
        if not data or 'error_key' not in data:
            return jsonify({'error': 'error_key is required'}), 400
        
        error_key = data['error_key']
        result = health_persistence.acknowledge_error(error_key)
        
        if result.get('success'):
            # Determine suppression period for the response
            category = result.get('category', '')
            if category == 'updates':
                suppression_hours = 180 * 24  # 180 days in hours
                suppression_label = '6 months'
            else:
                suppression_hours = 24
                suppression_label = '24 hours'
            
            return jsonify({
                'success': True,
                'message': f'Error dismissed for {suppression_label}',
                'error_key': error_key,
                'original_severity': result.get('original_severity', 'WARNING'),
                'category': category,
                'suppression_hours': suppression_hours,
                'suppression_label': suppression_label,
                'acknowledged_at': result.get('acknowledged_at')
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Error not found or already dismissed',
                'error_key': error_key
            }), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/active-errors', methods=['GET'])
def get_active_errors():
    """Get all active persistent errors"""
    try:
        category = request.args.get('category')
        errors = health_persistence.get_active_errors(category)
        return jsonify({'errors': errors})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/dismissed', methods=['GET'])
def get_dismissed_errors():
    """
    Get dismissed errors that are still within their suppression period.
    These are shown as INFO items with a 'Dismissed' badge in the frontend.
    """
    try:
        dismissed = health_persistence.get_dismissed_errors()
        return jsonify({'dismissed': dismissed})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/full', methods=['GET'])
def get_full_health():
    """
    Get complete health data in a single request: detailed status + active errors + dismissed.
    Reduces frontend round-trips.
    """
    try:
        details = health_monitor.get_detailed_status()
        active_errors = health_persistence.get_active_errors()
        dismissed = health_persistence.get_dismissed_errors()
        
        return jsonify({
            'health': details,
            'active_errors': active_errors,
            'dismissed': dismissed,
            'timestamp': details.get('timestamp')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/pending-notifications', methods=['GET'])
def get_pending_notifications():
    """
    Get events pending notification (for future Telegram/Gotify/Discord integration).
    This endpoint will be consumed by the Notification Service (Bloque A).
    """
    try:
        pending = health_persistence.get_pending_notifications()
        return jsonify({'pending': pending, 'count': len(pending)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@health_bp.route('/api/health/mark-notified', methods=['POST'])
def mark_events_notified():
    """
    Mark events as notified after notification was sent successfully.
    Used by the Notification Service (Bloque A) after sending alerts.
    """
    try:
        data = request.get_json()
        if not data or 'event_ids' not in data:
            return jsonify({'error': 'event_ids array is required'}), 400
        
        event_ids = data['event_ids']
        if not isinstance(event_ids, list):
            return jsonify({'error': 'event_ids must be an array'}), 400
        
        health_persistence.mark_events_notified(event_ids)
        return jsonify({'success': True, 'marked_count': len(event_ids)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
