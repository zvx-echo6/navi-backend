"""navi-config Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_config.app:create_app()' --bind 127.0.0.1:8422 --workers 2
"""
import time

from flask import Flask

from shared.git_sha import git_short_sha

from . import config_route, admin
from .config_loader import reset_cache


def create_app():
    app = Flask(__name__)

    # Version + lightweight runtime metrics, read by the admin-info endpoint.
    # NOTE: with gunicorn --workers 2 these counters are per-worker (each worker
    # is its own process); they are indicative, not cluster-wide totals.
    app.config['VERSION'] = git_short_sha()
    app.config['METRICS'] = {
        'start_time': time.time(),
        'request_count': 0,
        'last_error_at': None,
    }

    # Fresh profile load per app instance — each gunicorn worker (and each test)
    # picks up the current RECON_PROFILE / NAVI_CONFIG_PROFILES_DIR env.
    reset_cache()

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

    app.register_blueprint(config_route.bp)
    app.register_blueprint(admin.bp)
    return app
