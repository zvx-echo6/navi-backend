"""navi-admin Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_admin.app:create_app()' --bind 127.0.0.1:8427 --workers 2
"""
import subprocess
import time

from flask import Flask

from . import admin_route


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

    app.register_blueprint(admin_route.bp)
    return app
