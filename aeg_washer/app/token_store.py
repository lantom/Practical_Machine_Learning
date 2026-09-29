"""Trvalé uložení tokenů.

Electrolux při každém refreshi vydá NOVÝ refresh token a starý zneplatní.
Proto je nutné nové tokeny ukládat, jinak by po restartu služby přihlášení selhalo.
"""
import json
import logging
import os
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str] | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _LOGGER.warning("Nelze načíst %s: %s", self.path, e)
            return None
        if all(data.get(k) for k in ("api_key", "access_token", "refresh_token")):
            return data
        return None

    def save(self, access_token: str, refresh_token: str, api_key: str, seed_refresh_token: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "api_key": api_key,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    # token z .env, ze kterého tento řetězec vznikl (pozná se ruční výměna tokenů)
                    "seed_refresh_token": seed_refresh_token,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
