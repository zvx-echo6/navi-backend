"""Fleet fan-out + recon-health wrapping for navi-admin.

navi-admin is a stateless aggregator: it fans out over localhost to each
navi-* service's ``/api/admin/<svc>/info`` endpoint (and recon's pipeline
``/api/health``), merging them into one fleet response. Every per-service admin
endpoint is ``@require_auth``, so the fan-out forwards the caller's validated
``X-Authentik-Username`` header — otherwise the upstreams would 401.

Service discovery: a hardcoded module-level list (Option B). The set of navi-*
services changes only when we ship a new extraction — the same moment we'd be
editing this file to add it — so an env list would add a moving part with no
payoff. (One source of truth: the ports/names live here only.)
"""
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import requests

# (service-name, port) for every shipped navi-* service. The admin-info path is
# always /api/admin/<service-name>/info. Add a row when a new extraction ships.
SERVICES = [
    ('navi-traffic', 8421),    # #1 TomTom traffic tile proxy
    ('navi-config', 8422),     # #2 deployment profile API
    ('navi-contacts', 8423),   # #3 contacts + address book
    ('navi-landclass', 8424),  # #4 PAD-US land classification
    ('navi-places', 8425),     # #5 OSM place detail + enrichment
    ('navi-geo', 8426),        # #6 geocode + reverse + reverse bundle
]

RECON_SERVICE_NAME = 'recon'
RECON_PORT = 8420

DEFAULT_RECON_HEALTH_URL = 'http://127.0.0.1:8420/api/health'
DEFAULT_RECON_REPO_PATH = '/opt/recon'   # actual deploy path on VM 1130 (a git repo)
DEFAULT_FANOUT_TIMEOUT_S = 3.0


def recon_health_url():
    return os.environ.get('RECON_HEALTH_URL', DEFAULT_RECON_HEALTH_URL)


def recon_repo_path():
    return os.environ.get('RECON_REPO_PATH', DEFAULT_RECON_REPO_PATH)


def fanout_timeout():
    try:
        return float(os.environ.get('NAVI_ADMIN_FANOUT_TIMEOUT_S', DEFAULT_FANOUT_TIMEOUT_S))
    except (ValueError, TypeError):
        return DEFAULT_FANOUT_TIMEOUT_S


def service_info_url(name, port):
    return f'http://127.0.0.1:{port}/api/admin/{name}/info'


def recon_git_sha():
    """recon's deployed git SHA, or 'unknown'. Best-effort: a local `git -C`
    on the deploy clone (Phase A confirmed /opt/recon is a readable git repo)."""
    try:
        sha = subprocess.check_output(
            ['git', '-C', recon_repo_path(), 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        ).strip()
        return sha or 'unknown'
    except Exception:
        return 'unknown'


def _get_json(url, auth_user, timeout):
    """GET url, forwarding the auth header. Returns (json_or_None, latency_ms,
    error_or_None) where error is 'timeout' | 'HTTP <code>' | exception name."""
    headers = {'X-Authentik-Username': auth_user} if auth_user else {}
    start = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code != 200:
            return None, latency_ms, f'HTTP {resp.status_code}'
        return resp.json(), latency_ms, None
    except requests.Timeout:
        return None, round((time.monotonic() - start) * 1000, 1), 'timeout'
    except Exception as exc:
        return None, round((time.monotonic() - start) * 1000, 1), type(exc).__name__


def probe(name, url, auth_user, timeout=None):
    """One GET → (summary, full_json, error). summary is the {name, status,
    latency_ms[, error]} shape the per-service admin endpoints use for deps."""
    full, latency_ms, error = _get_json(url, auth_user, timeout or fanout_timeout())
    summary = {'name': name, 'status': 'ok' if error is None else 'error',
               'latency_ms': latency_ms}
    if error:
        summary['error'] = error
    return summary, full, error


def wrap_recon_health(health, error):
    """Wrap recon's /api/health into an admin-info-shaped dict (service:'recon').

    recon has no admin-info endpoint (Phase A §3); /api/health is the closest
    input. Its components become `dependencies`, its pipeline/status become
    `runtime`. Recon down → a degraded dict (never raises, never 5xx)."""
    version = recon_git_sha()
    if health is None:
        return {
            'service': RECON_SERVICE_NAME, 'version': version, 'port': RECON_PORT,
            'config': {}, 'env': [],
            'dependencies': [{'name': 'recon-health', 'status': 'error', 'error': error or 'unreachable'}],
            'filesystem': [],
            'runtime': {'recon_status': 'unreachable'},
        }
    components = health.get('components', {})
    dependencies = [
        {'name': cname, 'status': cval.get('status'), **{k: v for k, v in cval.items() if k != 'status'}}
        for cname, cval in components.items()
    ]
    return {
        'service': RECON_SERVICE_NAME, 'version': version, 'port': RECON_PORT,
        'config': {}, 'env': [],
        'dependencies': dependencies,
        'filesystem': [],
        'runtime': {
            'recon_status': health.get('status'),
            'recon_uptime': health.get('uptime'),
            'pipeline': health.get('pipeline', {}),
        },
    }


def build_fleet(auth_user):
    """Fan out to all navi-* services + recon in parallel; merge. Never raises.

    Returns {services: {<name>: <info>}, fetched_at: ISO8601, errors: [{service, error}]}.
    A service that times out / errors is omitted from `services` and recorded in
    `errors`; recon always appears in `services` (degraded dict when down) AND in
    `errors` when its health is unreachable."""
    timeout = fanout_timeout()
    targets = [(name, service_info_url(name, port)) for name, port in SERVICES]

    def _fetch(name, url):
        _, full, error = probe(name, url, auth_user, timeout)
        return name, full, error

    services = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(targets) + 1) as ex:
        futures = [ex.submit(_fetch, name, url) for name, url in targets]
        recon_future = ex.submit(probe, RECON_SERVICE_NAME, recon_health_url(), auth_user, timeout)
        for fut in futures:
            name, full, error = fut.result()
            if full is not None:
                services[name] = full
            if error:
                errors.append({'service': name, 'error': error})
        _, recon_health, recon_error = recon_future.result()
        services[RECON_SERVICE_NAME] = wrap_recon_health(recon_health, recon_error)
        if recon_error:
            errors.append({'service': RECON_SERVICE_NAME, 'error': recon_error})

    return {
        'services': services,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'errors': errors,
    }


def dependency_summaries(auth_user):
    """{name, status, latency_ms[, error]} for recon-health + each navi-* admin
    endpoint — the same probes the fleet runs, reused for navi-admin's own
    /info `dependencies`. Sequential (it's a rare, auth-gated call)."""
    timeout = fanout_timeout()
    summaries = []
    s, _, _ = probe('recon-health', recon_health_url(), auth_user, timeout)
    summaries.append(s)
    for name, port in SERVICES:
        s, _, _ = probe(name, service_info_url(name, port), auth_user, timeout)
        summaries.append(s)
    return summaries
