"""Declarative base whose metadata Alembic uses for autogenerate."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models. TODO: naming convention for constraints."""
