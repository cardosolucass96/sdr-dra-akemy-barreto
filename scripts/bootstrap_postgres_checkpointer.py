from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main() -> int:
    from app.agent.runtime import bootstrap_postgres_checkpointer

    parser = argparse.ArgumentParser(description="Run LangGraph Postgres checkpointer migrations.")
    parser.add_argument(
        "--env-file",
        action="append",
        help="Optional environment file to load before bootstrapping.",
    )
    parser.add_argument(
        "--database-url",
        help="Explicit database URL. Overrides DATABASE_URL from the environment.",
    )
    args = parser.parse_args()

    for env_file in args.env_file or []:
        load_dotenv(env_file, override=True)

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required to bootstrap the Postgres checkpointer.")
        return 1

    bootstrap_postgres_checkpointer(database_url)
    print("Postgres checkpointer schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
