"""Place API blueprint — port of recon's /api/place routes.

  GET /api/place/<osm_type>/<int:osm_id>   (Nominatim/Overpass + enrichment)
  GET /api/place/wikidata/<wikidata_id>    (Wikidata entity)

Public (no auth), matching recon. Same response shapes (200/400/404/502).
"""
from flask import Blueprint, jsonify

from .place_detail import get_place_detail, get_place_by_wikidata

bp = Blueprint('places', __name__)


@bp.route('/api/place/<osm_type>/<int:osm_id>')
def api_place_detail(osm_type, osm_id):
    result, status = get_place_detail(osm_type, osm_id)
    return jsonify(result), status


@bp.route('/api/place/wikidata/<wikidata_id>')
def api_place_wikidata(wikidata_id):
    result, status = get_place_by_wikidata(wikidata_id)
    return jsonify(result), status
