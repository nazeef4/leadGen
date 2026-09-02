"""Local credential vault.

Email app-passwords are never stored in clear text.  A per-installation key is
generated on first run inside the (git-ignored) state directory and used with
Fernet symmetric encryption.  Everything stays on the user's machine.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings, get_settings


class Vault:
    def __init__(self, key_path: Path):
        self.key_path = key_path
        self._fernet: Fernet | None = None

    def _load(self) -> Fernet:
        if self._fernet is None:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            if self.key_path.exists():
                key = self.key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                self.key_path.write_bytes(key)
                # Best effort: exotic filesystems may reject chmod.  # pragma: no cover
                with contextlib.suppress(OSError):
                    self.key_path.chmod(0o600)
            self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        return self._load().encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._load().decrypt(token.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            # Key was regenerated or the value was tampered with.
            return ""


_vault: Vault | None = None


def get_vault(settings: Settings | None = None) -> Vault:
    global _vault
    settings = settings or get_settings()
    if _vault is None or _vault.key_path != settings.key_file:
        _vault = Vault(settings.key_file)
    return _vault


def reset_vault() -> None:
    global _vault
    _vault = None
