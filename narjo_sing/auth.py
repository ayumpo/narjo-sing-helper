from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Header, HTTPException

KEY_FILE = "access-key.txt"


def load_or_create_key(stems_dir: Path) -> str:
    path = stems_dir / KEY_FILE
    if path.exists():
        key = path.read_text().strip()
        if key:
            return key
    stems_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(24)
    path.write_text(key + "\n")
    path.chmod(0o600)
    return key


def require_key(expected: str):
    expected_bytes = expected.encode("utf-8")

    def dependency(x_narjo_sing_key: str | None = Header(default=None)) -> None:
        # Bytes, not str: compare_digest rejects non-ASCII str (a non-ASCII header is a 401, not a 500).
        if x_narjo_sing_key is None or not secrets.compare_digest(x_narjo_sing_key.encode("utf-8"), expected_bytes):
            raise HTTPException(status_code=401, detail="Missing or wrong X-Narjo-Sing-Key")

    return dependency
