"""navi-contacts admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-contacts/info`` — Authentik-gated, read-only.
"""
import os
import time

from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response

from .contacts_db import DEFAULT_DB_PATH
from .address_book import DEFAULT_CONFIG_PATH

bp = Blueprint('contacts_admin', __name__)

PORT = 8423


@bp.route('/api/admin/navi-contacts/info')
@require_auth
def navi_contacts_info():
    metrics = current_app.config['METRICS']
    db_path = os.environ.get('NAVI_CONTACTS_DB', DEFAULT_DB_PATH)
    yaml_path = os.environ.get('NAVI_ADDRESS_BOOK_YAML', DEFAULT_CONFIG_PATH)
    # env values here are NOT secrets (filesystem paths) — shown as-is, no mask_key.
    info = build_info_response(
        service='navi-contacts',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        env=[
            {'name': 'NAVI_CONTACTS_DB', 'value': db_path},
            {'name': 'NAVI_ADDRESS_BOOK_YAML', 'value': yaml_path},
        ],
        dependencies=[],  # no upstream HTTP — local SQLite + YAML
        filesystem=[
            {
                'path': db_path,
                'exists': os.path.exists(db_path),
                'readable': os.access(db_path, os.R_OK),
                'writable': os.access(db_path, os.W_OK),
            },
            {
                'path': yaml_path,
                'exists': os.path.exists(yaml_path),
                'readable': os.access(yaml_path, os.R_OK),
            },
        ],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
