"""navi-places admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-places/info`` — Authentik-gated, read-only.
"""
import os
import time

import requests
from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response, mask_key

from . import overture
from . import place_cache
from . import wiki_index

bp = Blueprint('places_admin', __name__)

PORT = 8425


def _recon_base():
    return os.environ.get('RECON_BASE_URL', 'http://127.0.0.1:8420')


def _overture_dependency():
    start = time.monotonic()
    ok, detail = overture.probe_db()
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    r = {'name': 'overture-postgis', 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
    if not ok:
        r['error'] = detail
    return r


def _recon_probe(name, path):
    """Probe a recon endpoint with no params — expect HTTP 400 ('no usable key'),
    which signals the route exists and responds."""
    start = time.monotonic()
    try:
        resp = requests.get(f"{_recon_base()}{path}", timeout=3)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        # 400 is the healthy signal (route up, rejects empty input)
        ok = resp.status_code == 400
        r = {'name': name, 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
        if not ok:
            r['error'] = f'HTTP {resp.status_code}'
        return r
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': name, 'status': 'error', 'latency_ms': latency_ms, 'error': type(e).__name__}


@bp.route('/api/admin/navi-places/info')
@require_auth
def navi_places_info():
    metrics = current_app.config['METRICS']
    cache_path = place_cache.db_path()
    wiki_path = wiki_index.db_path()
    # Two real secrets -> mask_key. The rest are non-secret paths/URLs/params.
    info = build_info_response(
        service='navi-places',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        env=[
            {'name': 'OVERTURE_DB_HOST', 'value': os.environ.get('OVERTURE_DB_HOST', 'localhost')},
            {'name': 'OVERTURE_DB_PORT', 'value': os.environ.get('OVERTURE_DB_PORT', '5432')},
            {'name': 'OVERTURE_DB_NAME', 'value': os.environ.get('OVERTURE_DB_NAME', 'overture')},
            {'name': 'OVERTURE_DB_USER', 'value': os.environ.get('OVERTURE_DB_USER', 'overture')},
            {'name': 'OVERTURE_DB_PASSWORD', 'value': mask_key(os.environ.get('OVERTURE_DB_PASSWORD'))},
            {'name': 'GOOGLE_PLACES_API_KEY', 'value': mask_key(os.environ.get('GOOGLE_PLACES_API_KEY'))},
            {'name': 'RECON_BASE_URL', 'value': _recon_base()},
            {'name': 'NAVI_PLACE_CACHE_DB', 'value': cache_path},
            {'name': 'NAVI_WIKI_INDEX_DB', 'value': wiki_path},
            {'name': 'NAVI_PROFILES_DIR', 'value': os.environ.get('NAVI_PROFILES_DIR', '(default vendored)')},
        ],
        dependencies=[
            _overture_dependency(),
            _recon_probe('recon-wiki-rewrite', '/api/wiki-rewrite'),
        ],
        filesystem=[{
            'path': cache_path,
            'exists': os.path.exists(cache_path),
            'readable': os.access(cache_path, os.R_OK),
            'writable': os.access(cache_path, os.W_OK),
        }, {
            'path': wiki_path,
            'exists': os.path.exists(wiki_path),
            'readable': os.access(wiki_path, os.R_OK),
        }],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
