"""Contacts API blueprint — behavior-identical port of recon's ``lib/contacts_api.py``.

10 routes, all ``@require_auth``. ``request.user_id`` (set by the shared
require_auth from ``X-Authentik-Username``) partitions every query. Same JSON
shapes and status codes as recon (200/201/400/404/409).
"""
from flask import Blueprint, request, jsonify

from shared.auth import require_auth

from .contacts_db import ContactsDB

bp = Blueprint('contacts', __name__)

_db = None


def _get_db():
    global _db
    if _db is None:
        _db = ContactsDB()
    return _db


def reset_db():
    """Drop the cached ContactsDB so the next access reopens at the current
    NAVI_CONTACTS_DB path. Called by create_app() so each worker/test is fresh."""
    global _db
    _db = None


@bp.route('/api/contacts', methods=['GET'])
@require_auth
def list_contacts():
    db = _get_db()
    category = request.args.get('category')
    search = request.args.get('search')
    return jsonify(db.list_all(request.user_id, category=category, search=search))


@bp.route('/api/contacts', methods=['POST'])
@require_auth
def create_contact():
    db = _get_db()
    data = request.get_json(force=True)
    contact, err = db.create(request.user_id, **data)
    if err == 'conflict':
        return jsonify({'error': 'You already have a Home/Work contact'}), 409
    return jsonify(contact), 201


@bp.route('/api/contacts/nearby', methods=['GET'])
@require_auth
def nearby_contacts():
    db = _get_db()
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)
    radius_m = request.args.get('radius_m', 75, type=float)
    if lat is None or lon is None:
        return jsonify({'error': 'lat and lon required'}), 400
    return jsonify(db.find_nearby(request.user_id, lat, lon, radius_m))


@bp.route('/api/contacts/deleted', methods=['GET'])
@require_auth
def list_deleted():
    db = _get_db()
    return jsonify(db.list_deleted(request.user_id))


@bp.route('/api/contacts/<int:contact_id>', methods=['GET'])
@require_auth
def get_contact(contact_id):
    db = _get_db()
    contact = db.get(request.user_id, contact_id)
    if not contact:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(contact)


@bp.route('/api/contacts/<int:contact_id>', methods=['PATCH'])
@require_auth
def update_contact(contact_id):
    db = _get_db()
    data = request.get_json(force=True)
    contact = db.update(request.user_id, contact_id, **data)
    if not contact:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(contact)


@bp.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
@require_auth
def delete_contact(contact_id):
    db = _get_db()
    contact = db.soft_delete(request.user_id, contact_id)
    if not contact:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(contact)


@bp.route('/api/contacts/<int:contact_id>/restore', methods=['POST'])
@require_auth
def restore_contact(contact_id):
    db = _get_db()
    contact, err = db.restore(request.user_id, contact_id)
    if err == 'not_found':
        return jsonify({'error': 'Not found'}), 404
    if err == 'conflict':
        return jsonify({'error': 'You already have a Home/Work contact'}), 409
    return jsonify(contact)


@bp.route('/api/contacts/<int:contact_id>/restore-as', methods=['POST'])
@require_auth
def restore_as_contact(contact_id):
    db = _get_db()
    data = request.get_json(force=True)
    new_label = data.get('label', '').strip()
    if not new_label:
        return jsonify({'error': 'label is required'}), 400
    contact, err = db.restore_as(request.user_id, contact_id, new_label)
    if err == 'not_found':
        return jsonify({'error': 'Not found'}), 404
    if err == 'invalid_label':
        return jsonify({'error': 'Invalid label'}), 400
    if err == 'conflict':
        return jsonify({'error': 'Label conflict'}), 409
    return jsonify(contact)


@bp.route('/api/contacts/<int:contact_id>/purge', methods=['DELETE'])
@require_auth
def purge_contact(contact_id):
    db = _get_db()
    ok, err = db.purge(request.user_id, contact_id)
    if err == 'not_found':
        return jsonify({'error': 'Not found'}), 404
    if err == 'not_deleted':
        return jsonify({'error': 'Contact must be deleted before purging'}), 400
    return jsonify({'ok': True})
