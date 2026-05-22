"""navi-places Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_places.app:create_app()' --bind 127.0.0.1:8425 --workers 2
"""
import subprocess
import time

from flask import Flask

from . import place_route, admin
from . import overture
from . import place_cache
from . import config as places_config


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

    # Fresh PG pool + place_cache conn + profile per app instance, so each
    # gunicorn worker (and each test) picks up the current env.
    overture.reset_pool()
    place_cache.reset_cache()
    places_config.reset_config()

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

    app.register_blueprint(place_route.bp)
    app.register_blueprint(admin.bp)
    return app
