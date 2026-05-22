"""`/api/config` route — mirrors recon `lib/api.py:api_config`.

Returns the entire deployment profile dict as JSON with
``Cache-Control: public, max-age=300`` — byte-for-byte the same contract recon
serves today, so the frontend sees no difference at cutover.
"""
from flask import Blueprint, jsonify

from .config_loader import get_deployment_config

bp = Blueprint('config', __name__)


@bp.route('/api/config')
def api_config():
    """Return deployment profile config for frontend consumption."""
    config = get_deployment_config()
    resp = jsonify(config)
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp
