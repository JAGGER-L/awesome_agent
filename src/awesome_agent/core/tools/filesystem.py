from __future__ import annotations

import stat
from contextlib import suppress
from os import stat_result
from pathlib import Path

_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x400,
)


def is_link_or_reparse(status: stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def is_directory_link_or_reparse(path: Path, status: stat_result) -> bool:
    if not is_link_or_reparse(status):
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    if attributes & _FILE_ATTRIBUTE_DIRECTORY:
        return True
    if stat.S_ISLNK(status.st_mode):
        with suppress(OSError):
            return path.is_dir()
        return False
    return True
