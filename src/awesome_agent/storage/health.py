from __future__ import annotations

import sqlite3
import stat
from pathlib import Path


def sqlite_database_health(path: Path) -> bool | None:
    """Return a bounded, read-only SQLite health result.

    ``True`` means SQLite completed ``quick_check`` successfully, ``False`` means
    the target is definitely missing, invalid, or corrupt, and ``None`` means the
    check could not establish a result without mutating or waiting on state.
    """

    database_path = path.expanduser().resolve()
    try:
        status = database_path.stat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if not stat.S_ISREG(status.st_mode):
        return False

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=0.25,
        )
        row = connection.execute("PRAGMA quick_check(1)").fetchone()
    except sqlite3.OperationalError:
        return None
    except sqlite3.DatabaseError:
        return False
    except (OSError, TypeError, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()
    return row is not None and len(row) > 0 and row[0] == "ok"
