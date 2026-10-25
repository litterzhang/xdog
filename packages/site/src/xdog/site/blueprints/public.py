"""Versioned first-party reading API, not a proxy for HaveFun or Blog."""
from __future__ import annotations

import hashlib
from typing import Any

from flask import Blueprint, Response, abort, jsonify, request
from xdog.site.content import public

bp = Blueprint('public_content', __name__, url_prefix='/api/public/v1')


def respond(value: dict[str, Any] | None) -> Response:
    if value is None:
        abort(404)
    response = jsonify(data=value)
    response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
    response.cache_control.public = True
    response.cache_control.max_age = 60
    response.make_conditional(request)
    return response


@bp.get('/home')
def home() -> Response:
    return respond(public.home())


@bp.get('/packages')
def packages() -> Response:
    return respond(public.catalog())


@bp.get('/packages/<name>/<section>')
def package(name: str, section: str) -> Response:
    return respond(public.package_document(name, section))


@bp.get('/faq')
def faq() -> Response:
    return respond(public.faq())


@bp.errorhandler(404)
def missing(error: Any) -> tuple[Response, int]:
    return jsonify(error={'code': 'CONTENT_NOT_FOUND', 'message': 'Content not found'}), 404
