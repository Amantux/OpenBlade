from __future__ import annotations

from wsgiref.util import setup_testing_defaults

from openblade.api.wsgi import application


def test_wsgi_health_endpoint() -> None:
    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = "GET"
    environ["PATH_INFO"] = "/health"

    response_status: dict[str, str] = {}

    def start_response(
        status: str, _headers: list[tuple[str, str]], _exc_info: object | None = None
    ) -> None:
        response_status["value"] = status

    body = b"".join(application(environ, start_response))
    assert response_status["value"].startswith("200")
    assert b'"status":"ok"' in body
    assert b'"backend":"' in body
