"""Apply db/schema.sql. Idempotent.

    python -m pipeline.migrate
"""

from core.db import migrate

if __name__ == "__main__":
    migrate()
    print("schema applied")
