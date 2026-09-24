"""Bounded startup probe for the configured PostgreSQL database."""

from __future__ import annotations

import sys

import psycopg

from novel_writer.core.config import get_settings


def main() -> int:
    database_url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(database_url, connect_timeout=2):
            return 0
    except psycopg.Error:
        return 1


if __name__ == "__main__":
    sys.exit(main())
