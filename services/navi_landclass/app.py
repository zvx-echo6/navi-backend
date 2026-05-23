"""navi-landclass Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_landclass.app:create_app()' --bind 127.0.0.1:8424 --workers 2
"""
import time

from flask import Flask

from shared.git_sha import git_short_sha

from . import landclass_route, admin
from . import db


def create_app():
    app = Flask(__name__)

    # Version + lightweight runtime metrics, read by the admin-info endpoint.
    # NOTE: with gunicorn --workers 2 these counters are per-worker.
    app.config['VERSION'] = git_short_sha()
    app.config['METRICS'] = {
        'start_time': time.time(),
        'request_count': 0,
        'last_error_at': None,
    }

    # Fresh PG pool per app instance, so each gunicorn worker (and each test)
    # picks up the current PADUS_DB_* env. The pool is lazily re-created on the
    # next query; if PG is down it degrades to empty results, never crashes.
    db.reset_pool()

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

    app.register_blueprint(landclass_route.bp)
    app.register_blueprint(admin.bp)
    return app
