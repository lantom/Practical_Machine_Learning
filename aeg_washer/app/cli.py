"""Příkazová řádka pro rychlé ověření bez spuštění serveru.

  python -m app.cli status
"""
import asyncio
import json
import sys

from .config import Settings
from .notify import Notifier
from .washer import WasherError, WasherService


async def run(argv: list[str]) -> int:
    if argv != ["status"]:
        print(__doc__)
        return 1
    washer = WasherService(Settings.from_env(), notifier=Notifier(None))  # CLI na telefon nic neposílá
    await washer.start(background=False)
    print(json.dumps(washer.status(), indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(run(sys.argv[1:])))
    except WasherError as e:
        print(f"Chyba: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
