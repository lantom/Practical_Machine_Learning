"""Konfigurace z proměnných prostředí (.env)."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimalistické načtení .env bez další závislosti. Existující env má přednost."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    api_key: str
    access_token: str
    refresh_token: str
    token_file: Path
    appliance_id: str | None
    local_api_token: str | None
    poll_interval: int
    ntfy_url: str | None
    timezone: str = "Europe/Prague"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(Path(os.environ.get("ENV_FILE", ".env")))
        return cls(
            api_key=os.environ.get("ELX_API_KEY", ""),
            access_token=os.environ.get("ELX_ACCESS_TOKEN", ""),
            refresh_token=os.environ.get("ELX_REFRESH_TOKEN", ""),
            token_file=Path(os.environ.get("TOKEN_FILE", "data/tokens.json")),
            appliance_id=os.environ.get("APPLIANCE_ID") or None,
            local_api_token=os.environ.get("LOCAL_API_TOKEN") or None,
            poll_interval=int(os.environ.get("POLL_INTERVAL", "300")),
            ntfy_url=os.environ.get("NTFY_URL") or None,
            timezone=os.environ.get("TZ") or "Europe/Prague",
        )
