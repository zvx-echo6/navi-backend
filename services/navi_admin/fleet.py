"""Fleet fan-out for navi-admin.

navi-admin is a stateless aggregator: it fans out over localhost to each
navi-* service's ``/api/admin/<svc>/info`` endpoint, merging them into one
fleet response. Every per-service admin endpoint is ``@require_auth``, so the
fan-out forwards the caller's validated ``X-Authentik-Username`` header —
otherwise the upstreams would 401.

Service discovery: a hardcoded module-level list (Option B). The set of navi-*
services changes only when we ship a new extraction — the same moment we'd be
editing this file to add it — so an env list would add a moving part with no
payoff. (One source of truth: the ports/names live here only.)
"""
import os
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

DEFAULT_FANOUT_TIMEOUT_S = 3.0


def fanout_timeout():
    try:
        return float(os.environ.get('NAVI_ADMIN_FANOUT_TIMEOUT_S', DEFAULT_FANOUT_TIMEOUT_S))
    except (ValueError, TypeError):
        return DEFAULT_FANOUT_TIMEOUT_S


def service_info_url(name, port):
    return f'http://127.0.0.1:{port}/api/admin/{name}/info'


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
    except ValueError:
        # 200 with a non-JSON body (e.g. a misrouted upstream serving HTML).
        # json.JSONDecodeError subclasses ValueError — report it plainly.
        return None, round((time.monotonic() - start) * 1000, 1), 'invalid JSON'
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


def _degraded_entry(name, port, error):
    """Uniform 'service was probed but failed' entry — matches the
    build_info_response shape so callers see the same keys whether the service is
    healthy or down. Used for every failure path, so there is exactly one
    degraded shape: runtime.status == 'unreachable'."""
    return {
        'service': name, 'version': 'unknown', 'port': port,
        'config': {}, 'env': [],
        'dependencies': [{'name': f'{name}-info', 'status': 'error', 'error': error}],
        'filesystem': [],
        'runtime': {'status': 'unreachable'},
    }


def build_fleet(auth_user):
    """Fan out to all navi-* services in parallel; merge. Never raises.

    Returns {services: {<name>: <info>}, fetched_at: ISO8601, errors: [{service, error}]}.
    Invariant: EVERY probed service appears in `services` — a full info dict when
    healthy, the uniform `_degraded_entry` shape (runtime.status == 'unreachable')
    when it fails — and `errors` is a parallel listing of which ones failed and
    why."""
    timeout = fanout_timeout()
    targets = [(name, port, service_info_url(name, port)) for name, port in SERVICES]

    def _fetch(name, port, url):
        _, full, error = probe(name, url, auth_user, timeout)
        return name, port, full, error

    services = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        futures = [ex.submit(_fetch, name, port, url) for name, port, url in targets]
        for fut in futures:
            name, port, full, error = fut.result()
            services[name] = full if full is not None else _degraded_entry(name, port, error)
            if error:
                errors.append({'service': name, 'error': error})

    return {
        'services': services,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'errors': errors,
    }


def dependency_summaries(auth_user):
    """{name, status, latency_ms[, error]} for each navi-* admin endpoint — the
    same probes the fleet runs, reused for navi-admin's own /info
    `dependencies`. Sequential (it's a rare, auth-gated call)."""
    timeout = fanout_timeout()
    summaries = []
    for name, port in SERVICES:
        s, _, _ = probe(name, service_info_url(name, port), auth_user, timeout)
        summaries.append(s)
    return summaries
