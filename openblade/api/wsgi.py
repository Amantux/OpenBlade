"""WSGI compatibility entrypoint for Flask-like Python deployments."""

from __future__ import annotations

from a2wsgi import ASGIMiddleware

from openblade.api.main import app

application = ASGIMiddleware(app)  # type: ignore[arg-type]
