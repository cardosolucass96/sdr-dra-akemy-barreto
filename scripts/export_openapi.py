from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "docs" / "api" / "openapi.json"
ENV_FILE = PROJECT_ROOT / ".env"


def _sort_openapi_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_openapi_schema(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_openapi_schema(item) for item in value]
    return value


def _load_local_secrets() -> None:
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=False)


def main() -> int:
    from app.main import create_app

    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path where the OpenAPI JSON file should be written.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Server URL to include in the OpenAPI spec.",
    )
    args = parser.parse_args()

    _load_local_secrets()
    app = create_app()
    schema = app.openapi()
    schema["servers"] = [
        {
            "url": args.base_url,
            "description": "Local server using the production configuration",
        }
    ]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_sort_openapi_schema(schema), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI schema exported to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
