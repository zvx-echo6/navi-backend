"""navi-geo admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-geo/info`` — Authentik-gated, read-only.

Per Phase A §10 this service has NO secrets: landclass is HTTP-delegated to
navi-landclass, so ``PADUS_DB_*`` disappears entirely. The env block below
contains only non-secret URLs/paths, and ``build_info_response`` is given no
masked values.
"""
import os
import time

import requests
from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response

from .geocode import photon_url
from .landclass_client import landclass_url
from .netsyms import db_path as netsyms_db_path
from shared.dem import dem_path
from .geo_route import tz_db_path
from .address_book import _config_path as address_book_path

bp = Blueprint('geo_admin', __name__)

PORT = 8426


def _photon_dependency():
    """Photon up iff GET /api?q=test&limit=1 returns 200."""
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{photon_url()}/api", params={'q': 'test', 'limit': 1}, timeout=3
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        ok = resp.status_code == 200
        r = {'name': 'photon', 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
        if not ok:
            r['error'] = f'HTTP {resp.status_code}'
        return r
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'photon', 'status': 'error', 'latency_ms': latency_ms, 'error': type(e).__name__}


def _landclass_dependency():
    """navi-landclass up iff /api/landclass?lat=0&lon=0 returns 200 (ocean point
    → summary:null, the confirmed 'no coverage' shape)."""
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{landclass_url()}/api/landclass", params={'lat': 0, 'lon': 0}, timeout=3
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        ok = resp.status_code == 200
        r = {'name': 'navi-landclass', 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
        if not ok:
            r['error'] = f'HTTP {resp.status_code}'
        return r
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'navi-landclass', 'status': 'error', 'latency_ms': latency_ms, 'error': type(e).__name__}


def _file_entry(path):
    """Cheap present/absent + readable report for an external data file. Never
    errors on missing — just reports (Phase A §11 files stay external)."""
    return {
        'path': path,
        'exists': os.path.exists(path),
        'readable': os.access(path, os.R_OK),
    }


@bp.route('/api/admin/navi-geo/info')
@require_auth
def navi_geo_info():
    metrics = current_app.config['METRICS']
    netsyms_db = netsyms_db_path()
    timezone_db = tz_db_path()
    dem_file = str(dem_path())
    trace_log = os.environ.get('NAVI_GEO_RERANK_TRACE_LOG', '(unset — trace off)')

    info = build_info_response(
        service='navi-geo',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        # No secrets in this service (Phase A §10) — all non-secret URLs/paths.
        env=[
            {'name': 'PHOTON_URL', 'value': photon_url()},
            {'name': 'NAVI_LANDCLASS_URL', 'value': landclass_url()},
            {'name': 'NAVI_NETSYMS_DB', 'value': netsyms_db},
            {'name': 'NAVI_TIMEZONE_DB', 'value': timezone_db},
            {'name': 'NAVI_DEM_PMTILES', 'value': dem_file},
            {'name': 'NAVI_ADDRESS_BOOK_YAML', 'value': address_book_path()},
            {'name': 'NAVI_GEO_RERANK_TRACE_LOG', 'value': trace_log},
        ],
        dependencies=[
            _photon_dependency(),
            _landclass_dependency(),
        ],
        filesystem=[
            _file_entry(netsyms_db),
            _file_entry(timezone_db),
            _file_entry(dem_file),
            _file_entry(address_book_path()),
        ],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
