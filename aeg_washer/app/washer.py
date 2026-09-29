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
SELECTION_PREFIX = "userSelections/"
PROGRAM_KEY = "userSelections/programUID"
WASHER_TYPES = ("WD", "WM", "TD")

# Režim praní/sušení (tlačítko MÓD) se na API skládá ze dvou přepínačů.
MODE_KEYS = ("dryMode", "wetMode")
MODES = {"WASH": (True, False), "WASH_DRY": (True, True), "DRY": (False, True)}  # (wetMode, dryMode)
DRYING_KEYS = ("humidityTarget", "dryingTime")

REMOTE_START_OK = "ENABLED"
FINISHED_STATES = ("END_OF_CYCLE",)
ACTIVE_STATES = ("RUNNING", "PAUSED", "DELAYED_START")


class WasherError(Exception):
    """Chyba srozumitelná pro uživatele (vrací se jako HTTP odpověď)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ClientFactory = Callable[[TokenManager], ApplianceClient]


def _editable(meta: dict, ignore_disabled: bool = False) -> bool:
    """Volba jde nastavit (není jen pro čtení a – pokud nás to zajímá – není vypnutá).

    `ignore_disabled` se hodí pro volby, které zapíná až jiná volba (sušení po dryMode=true).
    """
    return meta.get("access") != "read" and (ignore_disabled or not meta.get("disabled"))


def _enabled_values(meta: dict) -> list[str]:
    return [v for v, m in (meta.get("values") or {}).items()
            if not (m or {}).get("disabled") and v != "DISABLED"]


def _modes(dry: dict | None, wet: dict | None) -> list[str]:
    """Které kombinace praní/sušení program připouští."""
    if not dry:
        return ["WASH"]

    def choices(meta: dict | None, fallback: bool) -> list[bool]:
        if meta is None:
            return [fallback]
        if not _editable(meta):
            return [bool(meta.get("default", fallback))]
        return [True, False]

    wet_opts, dry_opts = choices(wet, True), choices(dry, False)
    return [mode for mode, (w, d) in MODES.items() if w in wet_opts and d in dry_opts]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_options(model: dict[str, Any], options: dict[str, Any]) -> None:
    """Zkontroluje volby proti modelu programu z `WasherService.programs()`; chyba → 422."""
    program, drying = model["id"], model["drying"] or {}

    def bad(text: str) -> WasherError:
        return WasherError(f"{program}: {text}", 422)

    for name, value in options.items():
        if name in model["options"]:
            if str(value) not in model["options"][name]:
                raise bad(f"hodnota {name}={value} není povolena: {model['options'][name]}")
        elif name in model["toggles"] or (name in MODE_KEYS and len(model["modes"]) > 1):
            if not isinstance(value, bool):
                raise bad(f"{name} musí být true/false")
        elif name == "humidityTarget":
            if value not in drying.get("humidity", []):
                raise bad(f"úroveň sušení {value} není povolena: {drying.get('humidity', [])}")
        elif name == "dryingTime":
            t = drying.get("time")
            if not t or not _is_int(value) or not t["min"] <= value <= t["max"] or value % t["step"]:
                raise bad(f"čas sušení {value} mimo rozsah {t}")
        else:
            raise bad(f"volbu {name} nelze u tohoto programu nastavit")

    if "humidityTarget" in options and "dryingTime" in options:
        raise bad("zvol buď úroveň sušení, nebo čas sušení, ne obojí")
    defaults = model["defaults"]
    wet = options.get("wetMode", defaults.get("wetMode", True))
    dry = options.get("dryMode", defaults.get("dryMode", False))
    if any(k in options for k in MODE_KEYS) and not any(
            (w, d) == (wet, dry) and mode in model["modes"] for mode, (w, d) in MODES.items()):
        raise bad(f"kombinace wetMode={wet}, dryMode={dry} není povolena, režimy: {model['modes']}")
    if not dry and any(k in options for k in DRYING_KEYS):
        raise bad("volby sušení vyžadují dryMode=true")


def _validate_delay(model: dict[str, Any], delay: Any) -> None:
    d = model["delay"]
    if not d:
        raise WasherError(f"{model['id']}: odložený start není u tohoto programu dostupný", 422)
    if not _is_int(delay) or not 0 < delay <= d["max"] or delay % d["step"]:
        raise WasherError(f"Odložený start {delay} s mimo rozsah (násobek {d['step']} s, max {d['max']} s)", 422)


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
        """Programy a pro každý program, co jde nastavit.

        - `options`: výčtové volby (teplota, otáčky, pára, rychlost …) → seznam hodnot
        - `toggles`: zapínací volby (skvrny, předpírka, proti pomačkání …)
        - `modes`: povolené režimy WASH / WASH_DRY / DRY (přes dryMode + wetMode)
        - `drying`: úrovně AutoDry (`humidityTarget`) a rozsah `dryingTime` v minutách
        - `delay`: odložený start (`startTime`) v sekundách
        - `defaults`: výchozí hodnoty voleb pro program

        Program v capabilities obvykle vyjmenovává všechny své volby; globální
        `userSelections/*` se použijí jen jako doplnění typu a hodnot, případně
        pro programy, které žádné vlastní volby nemají.
        """
        caps = self.capabilities
        global_sel = {k: v for k, v in caps.items()
                      if k.startswith(SELECTION_PREFIX) and k != PROGRAM_KEY and isinstance(v, dict)}
        result = []
        for program, meta in (caps.get(PROGRAM_KEY, {}).get("values") or {}).items():
            meta = meta or {}
            if meta.get("disabled"):
                continue
            own = {k: v for k, v in meta.items() if k.startswith(SELECTION_PREFIX) and isinstance(v, dict)}
            keys = own.keys() if own else global_sel.keys()
            merged = {k[len(SELECTION_PREFIX):]: {**global_sel.get(k, {}), **own.get(k, {})} for k in keys}
            start_time = ({**caps.get("startTime", {}), **meta["startTime"]} if "startTime" in meta
                          else {} if own else caps.get("startTime", {}))
            result.append(self._program_model(program, merged, start_time))
        return result

    @staticmethod
    def _program_model(program: str, sel: dict[str, dict], start_time: dict) -> dict[str, Any]:
        options: dict[str, list[str]] = {}
        toggles: list[str] = []
        defaults = {name: m["default"] for name, m in sel.items() if "default" in m}
        for name, m in sel.items():
            if name in MODE_KEYS or name in DRYING_KEYS or not _editable(m):
                continue
            if isinstance(m.get("values"), dict):
                values = _enabled_values(m)
                if values:
                    options[name] = values
            elif m.get("type") == "boolean":
                toggles.append(name)

        modes = _modes(sel.get("dryMode"), sel.get("wetMode"))
        drying = None
        if any(mode != "WASH" for mode in modes):
            humidity = [v for v in _enabled_values(sel.get("humidityTarget", {})) if v != "UNDEFINED"]
            dt = sel.get("dryingTime")
            drying = {
                "humidity": humidity if _editable(sel.get("humidityTarget", {}), ignore_disabled=True) else [],
                "time": ({"min": dt.get("step", 10), "max": dt["max"], "step": dt.get("step", 10)}
                         if dt and dt.get("max") and _editable(dt, ignore_disabled=True) else None),
            }
        delay = None
        if start_time and _editable(start_time) and start_time.get("max"):
            delay = {"max": start_time["max"], "step": start_time.get("step", 1800)}

        return {"id": program, "options": options, "toggles": toggles, "modes": modes,
                "drying": drying, "delay": delay, "defaults": defaults}

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

    async def start_cycle(self, program: str | None, options: dict[str, Any], delay: int | None = None) -> Any:
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

        if program or options or delay:
            program = program or (r.get(USER_SELECTIONS) or {}).get("programUID")
            valid = {p["id"]: p for p in self.programs()}
            if valid and program not in valid:
                raise WasherError(f"Neznámý program '{program}'.", 422)
            model = valid.get(program)
            if model:
                _validate_options(model, options)
                if delay:
                    _validate_delay(model, delay)
            if program or options:
                await self.send({USER_SELECTIONS: {"programUID": program, **options}})
            if delay:
                await self.send({"startTime": delay})
        return await self._execute("START")

    async def pause(self) -> Any:
        return await self._execute("PAUSE")

    async def resume(self) -> Any:
        return await self._execute("RESUME")

    async def stop_cycle(self) -> Any:
        return await self._execute("STOPRESET")
