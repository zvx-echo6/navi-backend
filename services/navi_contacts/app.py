"""navi-contacts Flask application factory + gunicorn entry.

Gunicorn entry:
    gunicorn 'services.navi_contacts.app:create_app()' --bind 127.0.0.1:8423 --workers 2

Serves two blueprints: contacts (10 routes, auth-gated) and address_book
(2 routes, public), plus the §4.5 admin-info endpoint.
"""
import time

from flask import Flask

from shared.git_sha import git_short_sha

from . import contacts_route, address_book_route, admin
from . import address_book as address_book_mod


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

    # Fresh DB handle + address-book cache per app instance, so each gunicorn
    # worker (and each test) picks up the current NAVI_CONTACTS_DB /
    # NAVI_ADDRESS_BOOK_YAML env. ContactsDB auto-creates the schema on open.
    contacts_route.reset_db()
    address_book_mod.reset_cache()

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

    app.register_blueprint(contacts_route.bp)
    app.register_blueprint(address_book_route.bp)
    app.register_blueprint(admin.bp)
    return app
