"""navi-offroute Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_offroute.app:create_app()' --bind 127.0.0.1:8428 --workers 2
"""
import time

from flask import Flask

from shared.git_sha import git_short_sha

from . import offroute_route, admin


def create_app():
    app = Flask(__name__)

    app.config['VERSION'] = git_short_sha()
    app.config['METRICS'] = {
        'start_time': time.time(),
        'request_count': 0,
        'last_error_at': None,
    }

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

    app.register_blueprint(offroute_route.bp)
    app.register_blueprint(admin.bp)
    return app
