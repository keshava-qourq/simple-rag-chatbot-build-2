"""SQLAlchemy models.

The approved architecture declared no data model, so there are no tables
yet. Add them here: `Base` is already bound to the engine and `main.py`
imports this module before it creates the schema.
"""

from app.database import Base

__all__ = ["Base"]
