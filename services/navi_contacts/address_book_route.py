"""Address Book API blueprint — port of recon's ``lib/address_book_api.py``.

2 public routes (no auth). Same JSON shapes and status codes (400/404).
"""
from flask import Blueprint, request, jsonify

from . import address_book

bp = Blueprint('address_book', __name__)


@bp.route('/api/address_book/lookup')
def api_address_book_lookup():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Missing q parameter'}), 400

    result = address_book.lookup(q)
    if result is None:
        return '', 404

    return jsonify(result)


@bp.route('/api/address_book/list')
def api_address_book_list():
    entries = address_book.list_all()
    return jsonify(entries)
