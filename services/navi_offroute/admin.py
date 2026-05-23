"""navi-offroute admin-info endpoint (handoff §4.5).

``GET /api/admin/navi-offroute/info`` — Authentik-gated, read-only.

Per Phase A §10 this service has NO secrets: PADUS uses libpq peer-auth
(``dbname=padus``, no password); everything else is non-secret paths/URLs.
Probes are cheap (file existence + version/ping) — NO COUNT/DISTINCT against
navi.db, which is a fleet-aggregator hot path (see the navi-geo cold-start
lesson).
"""
import os
import subprocess
import time

import psycopg2
import requests
from flask import Blueprint, jsonify, current_app

from shared.auth import require_auth
from shared.admin_info import build_info_response
from shared.dem import dem_path

from . import router as router_mod
from .mvum import navi_db_path
from .barriers import barriers_tif_path, wilderness_tif_path
from .friction import friction_vrt_path
from .trails import trails_tif_path

bp = Blueprint('offroute_admin', __name__)

PORT = 8428


def _valhalla_dependency():
    start = time.monotonic()
    try:
        resp = requests.get(f"{router_mod.VALHALLA_URL}/status", timeout=3)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        ok = resp.status_code == 200
        r = {'name': 'valhalla', 'status': 'ok' if ok else 'error', 'latency_ms': latency_ms}
        if not ok:
            r['error'] = f'HTTP {resp.status_code}'
        return r
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'valhalla', 'status': 'error', 'latency_ms': latency_ms, 'error': type(e).__name__}


def _padus_pg_dependency():
    """SELECT 1 over the peer-auth DSN (no password). Cheap liveness check."""
    start = time.monotonic()
    conn = None
    try:
        conn = psycopg2.connect(router_mod.POSTGIS_DSN, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'padus-postgis', 'status': 'ok', 'latency_ms': latency_ms}
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'padus-postgis', 'status': 'error', 'latency_ms': latency_ms, 'error': type(e).__name__}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _osmium_dependency():
    """osmium-tool version (router shells out to `osmium extract` per route)."""
    start = time.monotonic()
    try:
        out = subprocess.check_output(['osmium', '--version'], stderr=subprocess.STDOUT,
                                      text=True, timeout=3)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        version = out.splitlines()[0].strip() if out else 'unknown'
        return {'name': 'osmium-tool', 'status': 'ok', 'latency_ms': latency_ms, 'version': version}
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {'name': 'osmium-tool', 'status': 'error', 'latency_ms': latency_ms,
                'error': 'not installed' if isinstance(e, FileNotFoundError) else type(e).__name__}


def _file_entry(name, path):
    """Cheap existence/readable report — never errors, never reads contents."""
    p = str(path)
    return {'name': name, 'path': p, 'exists': os.path.exists(p), 'readable': os.access(p, os.R_OK)}


@bp.route('/api/admin/navi-offroute/info')
@require_auth
def navi_offroute_info():
    metrics = current_app.config['METRICS']
    osm_pbf = str(router_mod.OSM_PBF_PATH)
    info = build_info_response(
        service='navi-offroute',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        config={},
        # No secrets (Phase A §10) — peer-auth DSN carries no password.
        env=[
            {'name': 'NAVI_OFFROUTE_VALHALLA_URL', 'value': router_mod.VALHALLA_URL},
            {'name': 'NAVI_OFFROUTE_POSTGIS_DSN', 'value': router_mod.POSTGIS_DSN},
            {'name': 'NAVI_OFFROUTE_DENSIFY_M', 'value': str(router_mod.DENSIFY_INTERVAL_M)},
            {'name': 'NAVI_OFFROUTE_OSM_PBF', 'value': osm_pbf},
            {'name': 'NAVI_OFFROUTE_NAVI_DB', 'value': str(navi_db_path())},
            {'name': 'NAVI_DEM_PMTILES', 'value': str(dem_path())},
            {'name': 'NAVI_OFFROUTE_BARRIERS_TIF', 'value': str(barriers_tif_path())},
            {'name': 'NAVI_OFFROUTE_WILDERNESS_TIF', 'value': str(wilderness_tif_path())},
            {'name': 'NAVI_OFFROUTE_TRAILS_TIF', 'value': str(trails_tif_path())},
            {'name': 'NAVI_OFFROUTE_FRICTION_VRT', 'value': str(friction_vrt_path())},
        ],
        dependencies=[
            _valhalla_dependency(),
            _padus_pg_dependency(),
            _osmium_dependency(),
        ],
        filesystem=[
            _file_entry('dem', dem_path()),
            _file_entry('osm_pbf', osm_pbf),
            _file_entry('navi_db', navi_db_path()),
            _file_entry('barriers_tif', barriers_tif_path()),
            _file_entry('wilderness_tif', wilderness_tif_path()),
            _file_entry('trails_tif', trails_tif_path()),
            _file_entry('friction_vrt', friction_vrt_path()),
        ],
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
