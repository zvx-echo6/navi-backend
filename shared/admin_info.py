"""Helpers for the uniform ``/api/admin/<service>/info`` endpoint (handoff §4.5).

Every navi-backend service exposes one admin-info endpoint with the same shape so
a future admin panel can fan out to all of them. These helpers keep each
service's handler down to a few lines.
"""
import time

import requests


def mask_key(value):
    """Mask a secret for display, matching recon's ``api_keys_admin._mask_key``.

    Pattern: ``first4 + '...' + last4`` (e.g. ``"tk_1...CDEF"``). Values of 8
    chars or fewer are fully masked as ``'****'`` so short strings don't reveal
    their endpoints. Returns ``None`` for empty/None input.
    """
    if not value:
        return None
    if len(value) <= 8:
        return '****'
    return value[:4] + '...' + value[-4:]


def time_dependency(name, url, method='HEAD', timeout=5):
    """Health-check an upstream dependency.

    Returns ``{name, status, latency_ms}`` (plus ``error`` on failure), where
    ``status`` is ``"ok"`` for any response below HTTP 500, else ``"error"``.
    """
    start = time.monotonic()
    try:
        resp = requests.request(method, url, timeout=timeout)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code < 500:
            return {'name': name, 'status': 'ok', 'latency_ms': latency_ms}
        return {
            'name': name,
            'status': 'error',
            'latency_ms': latency_ms,
            'error': f'HTTP {resp.status_code}',
        }
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            'name': name,
            'status': 'error',
            'latency_ms': latency_ms,
            'error': str(exc),
        }


def build_info_response(service, version, port, config, env, dependencies,
                        filesystem, runtime):
    """Assemble the uniform admin-info dict (handoff §4.5).

    - ``service``      short name, e.g. ``"navi-traffic"``
    - ``version``      git SHA (or semver if tagged)
    - ``port``         listening port
    - ``config``       loaded config dict, secrets masked
    - ``env``          list of ``{name, value}`` with values masked via mask_key
    - ``dependencies`` list of time_dependency() results
    - ``filesystem``   list of /mnt/nav paths with existence + read checks
    - ``runtime``      ``{uptime_s, request_count, last_error_at}``
    """
    return {
        'service': service,
        'version': version,
        'port': port,
        'config': config,
        'env': env,
        'dependencies': dependencies,
        'filesystem': filesystem,
        'runtime': runtime,
    }
