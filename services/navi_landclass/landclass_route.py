"""`/api/landclass` blueprint — port of recon's lib/api.py:api_landclass.

GET only. Validates lat/lon (400 on bad input). Returns the point's PAD-US
classifications + public/private verdict + most-specific-unit summary.

Diverges from recon by DROPPING the `has_landclass` profile-flag gate: the
frontend already gates on its own feature flag, and removing the cross-service
config dependency keeps navi-landclass self-contained (the service's existence
is the feature being available). See the commit body.
"""
from flask import Blueprint, request, jsonify

from .db import lookup_landclass, format_summary

bp = Blueprint('landclass', __name__)


@bp.route('/api/landclass', methods=['GET'])
def api_landclass():
    """PAD-US land classification lookup for a point."""
    try:
        lat = float(request.args.get('lat', ''))
        lon = float(request.args.get('lon', ''))
    except (ValueError, TypeError):
        return jsonify({'error': 'lat and lon required as numbers'}), 400

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'error': 'lat must be -90..90, lon must be -180..180'}), 400

    classifications = lookup_landclass(lat, lon)
    is_public = len(classifications) > 0
    is_private = len(classifications) == 0
    summary = format_summary(classifications)

    return jsonify({
        'lat': lat,
        'lon': lon,
        'classifications': classifications,
        'count': len(classifications),
        'is_public': is_public,
        'is_private': is_private,
        'summary': summary,
    })
