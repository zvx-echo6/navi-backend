"""navi-config admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-config/info`` — Authentik-gated, read-only.
"""
import os
import time

from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response

from .config_loader import profiles_dir, profile_name, active_profile_path

bp = Blueprint('admin', __name__)

PORT = 8422


@bp.route('/api/admin/navi-config/info')
@require_auth
def navi_config_info():
    metrics = current_app.config['METRICS']
    path = active_profile_path()
    # NOTE: these env values are NOT secrets — a directory path and a profile
    # name — so they are shown as-is. mask_key() is only for credential-bearing
    # vars (cf. navi-traffic's TOMTOM_API_KEY); there are none here.
    info = build_info_response(
        service='navi-config',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        env=[
            {'name': 'NAVI_CONFIG_PROFILES_DIR', 'value': profiles_dir()},
            {'name': 'RECON_PROFILE', 'value': profile_name()},
        ],
        dependencies=[],  # no upstream HTTP — config is a local file read
        filesystem=[{
            'path': path,
            'exists': os.path.exists(path),
            'readable': os.access(path, os.R_OK),
        }],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
