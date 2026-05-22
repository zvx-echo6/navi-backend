"""navi-landclass admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-landclass/info`` — Authentik-gated, read-only.
"""
import os
import time

from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response, mask_key

from . import db

bp = Blueprint('landclass_admin', __name__)

PORT = 8424


def _padus_dependency():
    """Health-check the PAD-US PostGIS connection via a SELECT 1 probe."""
    start = time.monotonic()
    ok, detail = db.probe_db()
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    result = {'name': 'padus-postgis', 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
    if not ok:
        result['error'] = detail
    return result


@bp.route('/api/admin/navi-landclass/info')
@require_auth
def navi_landclass_info():
    metrics = current_app.config['METRICS']
    # PADUS_DB_PASSWORD is a real secret -> mask_key. The other four are
    # non-secret connection params, shown as-is.
    info = build_info_response(
        service='navi-landclass',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        env=[
            {'name': 'PADUS_DB_HOST', 'value': os.environ.get('PADUS_DB_HOST', 'localhost')},
            {'name': 'PADUS_DB_PORT', 'value': os.environ.get('PADUS_DB_PORT', '5432')},
            {'name': 'PADUS_DB_NAME', 'value': os.environ.get('PADUS_DB_NAME', 'padus')},
            {'name': 'PADUS_DB_USER', 'value': os.environ.get('PADUS_DB_USER', 'overture')},
            {'name': 'PADUS_DB_PASSWORD', 'value': mask_key(os.environ.get('PADUS_DB_PASSWORD'))},
        ],
        dependencies=[_padus_dependency()],
        filesystem=[],  # no FS — PostGIS is external
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
