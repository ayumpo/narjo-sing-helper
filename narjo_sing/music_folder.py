from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from .status import HelperStatus

log = logging.getLogger(__name__)

# Short enough for Narjo's two-line toast and the mic's caption bubble.
MUSIC_FOLDER_GONE = "The helper can't see your music folder. Reconnect it, then restart the helper."
MUSIC_FOLDER_EMPTY = "Your music folder looks empty. Reconnect it, then restart the helper."


def music_folder_problem(music_dir: Path, indexed_files: Callable[[], int]) -> str | None:
    """Why the helper can't use `music_dir` right now, or None. An empty folder is a problem only once songs were
    indexed from it: a dropped NAS connection can leave an empty mount point behind."""
    try:
        with os.scandir(music_dir) as entries:
            empty = next(entries, None) is None
    except OSError:
        return MUSIC_FOLDER_GONE
    return MUSIC_FOLDER_EMPTY if empty and indexed_files() > 0 else None


def check_music_folder(status: HelperStatus, music_dir: Path, indexed_files: Callable[[], int]) -> str | None:
    """Records the current problem on `status`, logging only when one starts or ends."""
    problem = music_folder_problem(music_dir, indexed_files)
    if problem != status.music_problem:
        if problem:
            log.warning("Music folder %s: %s", music_dir, problem)
        else:
            log.info("Music folder %s is available again", music_dir)
        status.music_problem = problem
    return problem
