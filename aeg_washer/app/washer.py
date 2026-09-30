"""Sledování stavu pračky přes oficiální Electrolux Group Developer API (jen čtení).

Drží aktuální stav v paměti (livestream přes SSE + občasný polling jako záloha),
takže lokální API neposílá do cloudu dotaz při každém načtení, a při změně
aktualizuje notifikaci na telefonu.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from electrolux_group_developer_sdk.auth.token_manager import TokenManager
from electrolux_group_developer_sdk.client.appliance_client import ApplianceClient, apply_sse_update
from electrolux_group_developer_sdk.client.dto.appliance_details import ApplianceDetails
from electrolux_group_developer_sdk.client.dto.appliance_state import ApplianceState

from .config import Settings
from .notify import Notifier
from .status import build_status
from .token_store import TokenStore

_LOGGER = logging.getLogger(__name__)

WASHER_TYPES = ("WD", "WM", "TD")


class WasherError(Exception):
    """Chyba srozumitelná pro uživatele (vrací se jako HTTP odpověď)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ClientFactory = Callable[[TokenManager], ApplianceClient]


class WasherService:
    def __init__(self, settings: Settings, client_factory: ClientFactory | None = None,
                 notifier: Notifier | None = None, clock: Callable[[], datetime] | None = None):
        self.settings = settings
        self._client_factory = client_factory or (
            lambda tm: ApplianceClient(tm, external_user_agent="aeg-washer-local")
        )
        self._store = TokenStore(settings.token_file)
        self._notifier = notifier or Notifier(settings.ntfy_url)
        tz = ZoneInfo(settings.timezone)
        self._clock = clock or (lambda: datetime.now(tz))
        self.client: ApplianceClient | None = None
        self.appliance_id: str | None = None
        self.appliance_name: str | None = None
        self.details: ApplianceDetails | None = None
        self.state: ApplianceState | None = None
        self.last_update: float | None = None
        self.stream_connected = False
        self._tasks: list[asyncio.Task] = []
        self._notify_tasks: set[asyncio.Task] = set()

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
        await asyncio.gather(*self._tasks, *self._notify_tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------ state sync
    async def refresh_state(self) -> None:
        assert self.client and self.appliance_id
        self._set_state(await self.client.get_appliance_state(self.appliance_id))

    def _set_state(self, new_state: ApplianceState) -> None:
        old = self._reported().get("applianceState")
        self.state = new_state
        self.last_update = time.time()
        new = self._reported().get("applianceState")
        if old != new:
            _LOGGER.info("Stav pračky: %s -> %s", old, new)
        task = asyncio.get_running_loop().create_task(self._notifier.update(self.status()))
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)

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

    # ----------------------------------------------------------- read models
    def _reported(self) -> dict[str, Any]:
        if not self.state:
            return {}
        return self.state.properties.get("reported") or {}

    def status(self) -> dict[str, Any]:
        s = build_status(self._reported(), self.state.connectionState if self.state else None, self._clock())
        s["updatedAt"] = (datetime.fromtimestamp(self.last_update, self._clock().tzinfo).isoformat(timespec="seconds")
                          if self.last_update else None)
        return s
