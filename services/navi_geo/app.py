"""navi-geo Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_geo.app:create_app()' --bind 127.0.0.1:8426 --workers 2
"""
import subprocess
import time

from flask import Flask

from . import geo_route, admin
from . import geocode as geocode_mod
from . import netsyms
from . import address_book


def _git_sha():
    try:
        sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return sha or 'unknown'
    except Exception:
        return 'unknown'


def create_app():
    app = Flask(__name__)

    app.config['VERSION'] = _git_sha()
    app.config['METRICS'] = {
        'start_time': time.time(),
        'request_count': 0,
        'last_error_at': None,
    }

    # Fresh per-worker (and per-test) state, so each picks up current env.
    geo_route.reset_cache()
    netsyms.reset_conn()
    address_book.reset_cache()
    geocode_mod._setup_trace_logger()   # re-read NAVI_GEO_RERANK_TRACE_LOG

    @app.before_request
    def _count_request():
        app.config['METRICS']['request_count'] += 1

    @app.after_request
    def _track_errors(response):
        if response.status_code >= 500:
            app.config['METRICS']['last_error_at'] = time.strftime(
                '%Y-%m-%dT%H:%M:%SZ', time.gmtime()
            )
        return response

    app.register_blueprint(geo_route.bp)
    app.register_blueprint(admin.bp)
    return app
