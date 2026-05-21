#!/usr/bin/env python3
"""
Generate endpoint inventory from OpenBlade route files.
Run from repo root: python3 docs/generate_inventory.py
"""

from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.routing import APIRoute

from openblade.api.main import app


def _source_name(route: APIRoute) -> str:
    source = inspect.getsourcefile(route.endpoint)
    if source is None:
        return "<unknown>"
    try:
        return str(Path(source).relative_to(Path.cwd()))
    except ValueError:
        return Path(source).name


def main() -> int:
    routes: list[tuple[str, str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = sorted(method for method in route.methods if method not in {"HEAD", "OPTIONS"})
        for method in methods:
            routes.append((_source_name(route), method, route.path))

    print(f"{'File':<45} {'Method':<8} Path")
    print("-" * 110)
    for source, method, path in sorted(routes, key=lambda item: (item[2], item[1], item[0])):
        print(f"{source:<45} {method:<8} {path}")
    print(f"\nTotal: {len(routes)} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
