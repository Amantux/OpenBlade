from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "deploy" / "emulator" / "ui"


def test_index_embeds_docs_panel_without_custom_table() -> None:
    html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    section = re.search(r'<section class="card">\s*<h2>API docs</h2>(.*?)</section>', html, flags=re.DOTALL)
    assert section is not None
    assert '<iframe id="api-docs-frame"' in section.group(1)
    assert "<table" not in section.group(1)


def test_index_keeps_api_playground_controls() -> None:
    html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    for element_id in ("playground-method", "playground-path", "playground-body", "playground-send", "playground-response"):
        assert f'id="{element_id}"' in html


def test_app_js_mirrors_docs_links_and_listens_for_connection_updates() -> None:
    app_js = (UI_ROOT / "app.js").read_text(encoding="utf-8")
    assert 'elements.docsLink.href = buildUrl("/docs");' in app_js
    assert 'elements.redocLink.href = buildUrl("/redoc");' in app_js
    assert 'elements.openapiLink.href = buildUrl("/openapi.json");' in app_js
    assert 'elements.docsFrame.src = buildUrl(docsPath);' in app_js
    assert 'elements.baseUrl.addEventListener("input", updateDocsLinks);' in app_js
    assert 'elements.prefix.addEventListener("input", updateDocsLinks);' in app_js
