"""Příkazová řádka pro rychlé ověření bez spuštění serveru.

  python -m app.cli status
  python -m app.cli programs
  python -m app.cli raw > raw.json
  python -m app.cli start COTTON_PR_COTTONSECO analogTemperature=40_CELSIUS analogSpinSpeed=1200_RPM
  python -m app.cli pause | resume | stop
"""
import asyncio
import json
import sys

from .config import Settings
from .washer import WasherError, WasherService


async def run(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    washer = WasherService(Settings.from_env())
    await washer.start(background=False)

    if cmd == "status":
        out = washer.status()
    elif cmd == "programs":
        out = washer.programs()
    elif cmd == "raw":
        out = {"details": washer.details.model_dump(mode="json") if washer.details else None,
               "state": washer.state.model_dump(mode="json") if washer.state else None}
    elif cmd == "start":
        program = args[0] if args and "=" not in args[0] else None
        options = dict(a.split("=", 1) for a in args if "=" in a)
        out = await washer.start_cycle(program, options)
    elif cmd in ("pause", "resume"):
        out = await getattr(washer, cmd)()
    elif cmd == "stop":
        out = await washer.stop_cycle()
    else:
        print(__doc__)
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(run(sys.argv[1:])))
    except WasherError as e:
        print(f"Chyba: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
