"""Služba nad oficiálním Electrolux Group Developer API pro pračku se sušičkou (typ WD).

Drží aktuální stav v paměti (livestream přes SSE + občasný polling jako záloha),
takže lokální API neposílá do cloudu dotaz při každém načtení stránky.
"""
import asyncio
import logging
import time
from typing import Any, Callable

import aiohttp
from electrolux_group_developer_sdk.auth.token_manager import TokenManager
from electrolux_group_developer_sdk.client.appliance_client import ApplianceClient, apply_sse_update
from electrolux_group_developer_sdk.client.client_exception import ApplianceClientException
from electrolux_group_developer_sdk.client.dto.appliance_details import ApplianceDetails
from electrolux_group_developer_sdk.client.dto.appliance_state import ApplianceState

from .config import Settings
from .token_store import TokenStore

_LOGGER = logging.getLogger(__name__)

USER_SELECTIONS = "userSelections"
PROGRAM_KEY = "userSelections/programUID"
WASHER_TYPES = ("WD", "WM", "TD")

REMOTE_START_OK = "ENABLED"
FINISHED_STATES = ("END_OF_CYCLE",)
ACTIVE_STATES = ("RUNNING", "PAUSED", "DELAYED_START")


class WasherError(Exception):
    """Chyba srozumitelná pro uživatele (vrací se jako HTTP odpověď)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ClientFactory = Callable[[TokenManager], ApplianceClient]


class WasherService:
    def __init__(self, settings: Settings, client_factory: ClientFactory | None = None):
        self.settings = settings
        self._client_factory = client_factory or (
            lambda tm: ApplianceClient(tm, external_user_agent="aeg-washer-local")
        )
        self._store = TokenStore(settings.token_file)
        self.client: ApplianceClient | None = None
        self.appliance_id: str | None = None
        self.appliance_name: str | None = None
        self.details: ApplianceDetails | None = None
        self.state: ApplianceState | None = None
        self.last_update: float | None = None
        self.stream_connected = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    def _initial_credentials(self) -> tuple[str, str, str]:
        """Uložené (novější) tokeny mají přednost, pokud uživatel v .env nezadal nové."""
        s = self.settings
        stored = self._store.load()
        if stored and (not s.refresh_token or stored.get("seed_refresh_token") in (None, s.refresh_token)):
            return stored["access_token"], stored["refresh_token"], stored["api_key"]
        if not (s.api_key and s.access_token and s.refresh_token):
            raise WasherError(
                "Chybí ELX_API_KEY / ELX_ACCESS_TOKEN / ELX_REFRESH_TOKEN (viz .env.example).", 500
            )
        return s.access_token, s.refresh_token, s.api_key

    def _save_tokens(self, access_token: str, refresh_token: str, api_key: str) -> None:
        self._store.save(access_token, refresh_token, api_key, seed_refresh_token=self.settings.refresh_token)

    async def start(self, background: bool = True) -> None:
        access, refresh, api_key = self._initial_credentials()
        token_manager = TokenManager(access, refresh, api_key, on_token_update=self._save_tokens)
        self.client = self._client_factory(token_manager)

        appliances = await self.client.get_appliances()
        if not appliances:
            raise WasherError("Účet nemá žádné spotřebiče. Spáruj pračku v aplikaci My AEG.", 500)
        if self.settings.appliance_id:
            match = [a for a in appliances if a.applianceId == self.settings.appliance_id]
            if not match:
                raise WasherError(f"APPLIANCE_ID {self.settings.appliance_id} nenalezeno.", 500)
            appliance = match[0]
        else:
            washers = [a for a in appliances if a.applianceType in WASHER_TYPES]
            appliance = (washers or appliances)[0]
        self.appliance_id = appliance.applianceId
        self.appliance_name = appliance.applianceName
        _LOGGER.info("Používám spotřebič %s (%s, typ %s)",
                     appliance.applianceName, appliance.applianceId, appliance.applianceType)

        self.details = await self.client.get_appliance_details(self.appliance_id)
        await self.refresh_state()

        if background:
            self.client.add_listener(self.appliance_id, self._on_event)
            self._tasks = [
                asyncio.create_task(self.client.start_event_stream(
                    do_on_livestream_opening_list=[self._on_stream_open],
                    do_on_livestream_closing_list=[self._on_stream_close],
                )),
                asyncio.create_task(self._poll_loop()),
            ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------ state sync
    async def refresh_state(self) -> None:
        assert self.client and self.appliance_id
        new_state = await self.client.get_appliance_state(self.appliance_id)
        self._set_state(new_state)

    def _set_state(self, new_state: ApplianceState) -> None:
        old = self._reported().get("applianceState")
        self.state = new_state
        self.last_update = time.time()
        new = self._reported().get("applianceState")
        if old != new:
            _LOGGER.info("Stav pračky: %s -> %s", old, new)
            if old in ACTIVE_STATES and new in FINISHED_STATES:
                asyncio.get_running_loop().create_task(self._notify("Pračka dokončila program 🧺"))

    def _on_event(self, event: dict[str, Any]) -> None:
        if self.state is not None:
            self._set_state(apply_sse_update(self.state, event))

    async def _on_stream_open(self) -> None:
        self.stream_connected = True
        await self.refresh_state()

    def _on_stream_close(self, _error: BaseException | None = None) -> None:
        self.stream_connected = False

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.poll_interval)
            try:
                await self.refresh_state()
            except Exception as e:  # noqa: BLE001 - záložní smyčka nesmí spadnout
                _LOGGER.warning("Polling stavu selhal: %s", e)

    async def _notify(self, message: str) -> None:
        if not self.settings.ntfy_url:
            return
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(self.settings.ntfy_url, data=message.encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Notifikace selhala: %s", e)

    # ----------------------------------------------------------- read models
    def _reported(self) -> dict[str, Any]:
        if not self.state:
            return {}
        return self.state.properties.get("reported") or {}

    @property
    def capabilities(self) -> dict[str, Any]:
        return self.details.capabilities if self.details else {}

    def status(self) -> dict[str, Any]:
        r = self._reported()
        selections = r.get(USER_SELECTIONS) or {}
        info = self.details.applianceInfo.model_dump() if self.details else {}
        return {
            "applianceId": self.appliance_id,
            "name": self.appliance_name,
            "model": info.get("model"),
            "pnc": info.get("pnc"),
            "connectionState": self.state.connectionState if self.state else None,
            "applianceState": r.get("applianceState"),
            "cyclePhase": r.get("cyclePhase"),
            "timeToEnd": r.get("timeToEnd"),
            "doorState": r.get("doorState"),
            "remoteControl": r.get("remoteControl"),
            "remoteStartAllowed": r.get("remoteControl") == REMOTE_START_OK,
            "program": selections.get("programUID"),
            "selections": selections,
            "alerts": r.get("alerts") or [],
            "stream": self.stream_connected,
            "lastUpdate": self.last_update,
        }

    def programs(self) -> list[dict[str, Any]]:
        """Programy a pro každý program dostupné volby (teplota, otáčky, sušení, …).

        Volby se berou obecně ze všech capabilities `userSelections/*`, takže se
        automaticky objeví i volby sušení, páry apod., které daný model podporuje.
        """
        caps = self.capabilities
        global_opts = {
            key.split("/", 1)[1]: list(meta["values"].keys())
            for key, meta in caps.items()
            if key.startswith("userSelections/") and key != PROGRAM_KEY
            and isinstance(meta, dict) and isinstance(meta.get("values"), dict)
        }
        result = []
        for program, meta in (caps.get(PROGRAM_KEY, {}).get("values") or {}).items():
            meta = meta or {}
            if meta.get("disabled"):
                continue
            options = dict(global_opts)
            for key, sub in meta.items():
                if key.startswith("userSelections/") and isinstance(sub, dict):
                    name = key.split("/", 1)[1]
                    if sub.get("disabled"):
                        options.pop(name, None)
                    elif isinstance(sub.get("values"), dict):
                        options[name] = list(sub["values"].keys())
            options = {k: [v for v in vals if v != "DISABLED"] for k, vals in options.items()}
            result.append({"id": program, "options": {k: v for k, v in options.items() if v}})
        return result

    # --------------------------------------------------------------- commands
    async def send(self, command: dict[str, Any]) -> Any:
        assert self.client and self.appliance_id
        async with self._lock:
            try:
                return await self.client.send_command(self.appliance_id, command)
            except ApplianceClientException as e:
                raise WasherError(f"Electrolux API odmítlo příkaz: {e}", e.status or 502) from e

    async def _execute(self, action: str) -> Any:
        return await self.send({"executeCommand": action})

    async def start_cycle(self, program: str | None, options: dict[str, Any]) -> Any:
        r = self._reported()
        rc = r.get("remoteControl")
        if rc is not None and rc != REMOTE_START_OK:
            raise WasherError(
                f"Dálkové spuštění není na pračce povoleno (remoteControl={rc}). "
                "Naplň pračku, zavři dvířka a zapni na ní 'Dálkové spuštění'.",
                409,
            )
        if r.get("doorState") == "OPEN":
            raise WasherError("Dvířka jsou otevřená.", 409)

        if program or options:
            program = program or (r.get(USER_SELECTIONS) or {}).get("programUID")
            valid = {p["id"]: p for p in self.programs()}
            if valid and program not in valid:
                raise WasherError(f"Neznámý program '{program}'.", 422)
            allowed = valid.get(program, {}).get("options", {})
            for name, value in options.items():
                if allowed and name in allowed and str(value) not in allowed[name]:
                    raise WasherError(
                        f"Hodnota {name}={value} není pro {program} povolena: {allowed[name]}", 422
                    )
            await self.send({USER_SELECTIONS: {"programUID": program, **options}})
        return await self._execute("START")

    async def pause(self) -> Any:
        return await self._execute("PAUSE")

    async def resume(self) -> Any:
        return await self._execute("RESUME")

    async def stop_cycle(self) -> Any:
        return await self._execute("STOPRESET")
