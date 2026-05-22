"""navi-traffic admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-traffic/info`` — Authentik-gated, read-only. Exposes the
service's version, port, masked env, upstream health, and runtime counters.
"""
import os
import time

from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response, mask_key, time_dependency

bp = Blueprint('admin', __name__)

PORT = 8421


@bp.route('/api/admin/navi-traffic/info')
@require_auth
def navi_traffic_info():
    metrics = current_app.config['METRICS']
    info = build_info_response(
        service='navi-traffic',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        env=[{
            'name': 'TOMTOM_API_KEY',
            'value': mask_key(os.environ.get('TOMTOM_API_KEY')),
        }],
        dependencies=[
            time_dependency('tomtom-api', 'https://api.tomtom.com/'),
        ],
        filesystem=[],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
