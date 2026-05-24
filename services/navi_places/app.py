"""navi-places Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_places.app:create_app()' --bind 127.0.0.1:8425 --workers 2
"""
import time

from flask import Flask

from shared.git_sha import git_short_sha

from . import place_route, admin
from . import overture
from . import place_cache
from . import wiki_index
from . import wiki_rewrite
from . import config as places_config


def create_app():
    app = Flask(__name__)

    app.config['VERSION'] = git_short_sha()
    app.config['METRICS'] = {
        'start_time': time.time(),
        'request_count': 0,
        'last_error_at': None,
    }

    # Fresh PG pool + place_cache conn + profile per app instance, so each
    # gunicorn worker (and each test) picks up the current env.
    overture.reset_pool()
    place_cache.reset_cache()
    wiki_index.reset()
    wiki_rewrite.reset()
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
