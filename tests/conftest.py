import os
from pathlib import Path

import pytest

from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def app_context(tmp_path: Path):
    config = OpenBladeConfig(db_url=f"sqlite+aiosqlite:///{tmp_path / 'openblade.db'}")
    context = create_context(config)
    reset_context(context)
    return context


@pytest.fixture
def service_token_headers():
    token = os.environ.get("OPENBLADE_SERVICE_TOKEN", "openblade-controller-dev-token-do-not-expose")
    return {"X-Openblade-Service-Token": token}
