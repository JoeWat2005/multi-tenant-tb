"""Initialise the database.

Dev mode: drops and recreates every table on startup so a fresh build picks
up the latest schema without manual migration steps. Before shipping LIVE
mode, replace this with Alembic migrations under `v2/db/migrations/`.
"""
from __future__ import annotations

import logging
import os

from app.db.session import Base, engine

logger = logging.getLogger(__name__)


def init_db() -> None:
    # Importing the models package registers every ORM class on Base.metadata.
    import app.models  # noqa: F401

    if os.getenv("RESET_DB_ON_STARTUP", "true").lower() == "true":
        Base.metadata.drop_all(bind=engine)
        logger.info("Dropped all tables (dev reset)")

    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised")
