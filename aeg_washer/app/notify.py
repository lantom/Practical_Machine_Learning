"""Jedna průběžně aktualizovaná notifikace na telefonu přes ntfy (Android).

Během programu se notifikace se stejným `X-Sequence-ID` jen přepisuje (tichá priorita),
po dokončení se změní na „Hotovo“ se zvukem a po otevření dvířek / vypnutí pračky zmizí.
"""
import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

SEQUENCE_ID = "aeg-washer"
PRIO_QUIET, PRIO_DONE = "2", "4"
ENDS_AT_TOLERANCE_S = 180  # kvůli drobným posunům odhadu nepřepisujeme notifikaci každou minutu

Sender = Callable[[str, str, dict[str, str], bytes], Awaitable[None]]


def _rfc2047(text: str) -> str:
    """HTTP hlavičky nesnesou emoji/diakritiku; ntfy umí RFC 2047."""
    return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


async def _http_send(method: str, url: str, headers: dict[str, str], body: bytes) -> None:
    async with (aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s,
                s.request(method, url, headers=headers, data=body) as r):
        if r.status >= 400:
            raise RuntimeError(f"ntfy {method} {r.status}: {await r.text()}")


class Notifier:
    def __init__(self, url: str | None, sender: Sender | None = None):
        self.url = url.rstrip("/") if url else None
        self._send = sender or _http_send
        self._shown: dict[str, Any] | None = None  # co je právě na telefonu (klíč posledního odeslání)
        self._lock = asyncio.Lock()  # aktualizace ze streamu musí dorazit v pořadí

    async def update(self, status: dict[str, Any]) -> None:
        if not self.url:
            return
        async with self._lock:
            try:
                await self._update(status)
            except Exception as e:  # noqa: BLE001 - notifikace nesmí shodit sledování stavu
                _LOGGER.warning("Notifikace selhala: %s", e)

    async def _update(self, s: dict[str, Any]) -> None:
        state = s["state"]
        if s["running"]:
            key = {"state": state, "phase": (s["phase"] or {}).get("id"), "program": (s["program"] or {}).get("id"),
                   "startsAt": s["startsAt"], "endsAt": s["endsAt"]}
            if self._shown is None or not self._same(self._shown, key):
                await self._publish(s, PRIO_QUIET)
                self._shown = key
        elif state == "END_OF_CYCLE":
            if self._shown is not None and self._shown.get("state") != state:
                await self._publish(s, PRIO_DONE)
                self._shown = {"state": state}
        elif self._shown is not None:
            # vypnuto / nečinná / zrušeno → notifikaci z lišty odstraníme
            await self._send("DELETE", f"{self.url}/{SEQUENCE_ID}", {}, b"")
            self._shown = None

    @staticmethod
    def _same(old: dict[str, Any], new: dict[str, Any]) -> bool:
        if {k: v for k, v in old.items() if k != "endsAt"} != {k: v for k, v in new.items() if k != "endsAt"}:
            return False
        if bool(old["endsAt"]) != bool(new["endsAt"]):
            return False
        if not new["endsAt"]:
            return True
        drift = abs((datetime.fromisoformat(new["endsAt"]) - datetime.fromisoformat(old["endsAt"])).total_seconds())
        return drift < ENDS_AT_TOLERANCE_S

    async def _publish(self, s: dict[str, Any], priority: str) -> None:
        program = s["program"] or {"icon": "🧺", "name": "Pračka"}
        title = " ".join([program["icon"], program["name"], *s["icons"]])
        phase = s["phase"]
        parts = [f"{phase['icon']} {phase['name']}" if phase else f"{s['stateIcon']} {s['stateText']}"]
        if s["startsAt"]:
            parts.append(f"⏰ {s['startsAt'][11:16]}")
        if s["endsAt"]:
            parts.append(f"🏁 {s['endsAt'][11:16]}")
        body = " · ".join(parts)
        headers = {"X-Sequence-ID": SEQUENCE_ID, "X-Title": _rfc2047(title), "X-Priority": priority}
        await self._send("POST", self.url, headers, body.encode("utf-8"))
