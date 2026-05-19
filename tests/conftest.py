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
