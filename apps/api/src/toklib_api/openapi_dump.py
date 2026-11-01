"""Выгрузка OpenAPI-схемы в файл для генерации TS-типов (docs/09 §9.10).

Запуск: uv run --all-packages python -m toklib_api.openapi_dump apps/web/openapi.json
Затем в apps/web: npm run gen:api  (openapi-typescript → src/lib/schema.d.ts)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def dump(path: str) -> None:
    from .main import create_app

    app = create_app()
    schema = app.openapi()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAPI схема записана: {target} ({len(schema.get('paths', {}))} путей)")


if __name__ == "__main__":
    dump(sys.argv[1] if len(sys.argv) > 1 else "apps/web/openapi.json")
