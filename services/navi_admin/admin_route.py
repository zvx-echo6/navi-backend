"""navi-admin routes — the fleet admin front door (extraction #7).

Net-new (recon has no admin-info to port — Phase A §3). Three routes, all
``@require_auth`` (auth is also enforced at the Caddy edge in prod; the Flask
gate matches every other navi-* service and protects the localhost :8427 path):

  GET /api/admin/recon/info       recon's /api/health wrapped in the info shape
  GET /api/admin/fleet            fan-out aggregator across all navi-* + recon
  GET /api/admin/navi-admin/info  navi-admin's own admin-info (self-describe)

The per-service /api/admin/<svc>/info endpoints stay localhost-only (Phase A
§3/§7); this fleet endpoint is the single edge-exposed front door.
"""
import os
import time

from flask import Blueprint, jsonify, current_app, request

from shared.auth import require_auth
from shared.admin_info import build_info_response

from . import fleet

bp = Blueprint('navi_admin', __name__)

PORT = 8427


@bp.route('/api/admin/recon/info')
@require_auth
def recon_info():
    """recon's pipeline /api/health, wrapped into the uniform info shape.
    recon down → a degraded info dict, not a 5xx."""
    _, health, error = fleet.probe('recon', fleet.recon_health_url(), request.user_id)
    return jsonify(fleet.wrap_recon_health(health, error))


@bp.route('/api/admin/fleet')
@require_auth
def fleet_info():
    """Fan out to every navi-* service + recon over localhost, merged. Forwards
    the caller's X-Authentik-Username so the @require_auth upstreams accept it.
    Never 5xx — per-service failures land in `errors`."""
    return jsonify(fleet.build_fleet(request.user_id))


@bp.route('/api/admin/navi-admin/info')
@require_auth
def navi_admin_info():
    """navi-admin's own admin-info. No secrets (Phase A §9) — only non-secret
    URLs/paths. `dependencies` reuses the same probes the fleet runs."""
    metrics = current_app.config['METRICS']
    info = build_info_response(
        service='navi-admin',
        version=current_app.config.get('VERSION', 'unknown'),
        port=PORT,
        # What it aggregates — non-secret, documents the fleet membership.
        config={
            'fanned_services': [{'name': n, 'port': p} for n, p in fleet.SERVICES],
            'recon': {'name': fleet.RECON_SERVICE_NAME, 'port': fleet.RECON_PORT,
                      'git_sha': fleet.recon_git_sha()},
        },
        env=[
            {'name': 'RECON_HEALTH_URL', 'value': fleet.recon_health_url()},
            {'name': 'RECON_REPO_PATH', 'value': fleet.recon_repo_path()},
            {'name': 'NAVI_ADMIN_FANOUT_TIMEOUT_S', 'value': str(fleet.fanout_timeout())},
        ],
        dependencies=fleet.dependency_summaries(request.user_id),
        filesystem=[],   # navi-admin owns no files / no DB (Phase A §9)
        runtime={
            'uptime_s': round(time.time() - metrics['start_time'], 1),
            'request_count': metrics['request_count'],
            'last_error_at': metrics['last_error_at'],
        },
    )
    return jsonify(info)
