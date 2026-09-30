"""Testy s falešným Electrolux klientem a falešným ntfy (nevolají cloud)."""
import base64
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from electrolux_group_developer_sdk.client.dto.appliance import Appliance
from electrolux_group_developer_sdk.client.dto.appliance_details import ApplianceDetails
from electrolux_group_developer_sdk.client.dto.appliance_state import ApplianceState
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.notify import SEQUENCE_ID, Notifier
from app.status import PROGRAMS, build_status
from app.washer import WasherService

APPLIANCE_ID = "914xxxxxx_00:12345678-443E07ABCDEF"
NTFY = "https://ntfy.example/pracka-test"
NOW = datetime(2026, 9, 30, 13, 20, 10, tzinfo=ZoneInfo("Europe/Prague"))
FIXTURE = Path(__file__).parent / "fixtures" / "capabilities_lwr98165xc.json"


def reported(**overrides):
    base = {
        "applianceState": "RUNNING",
        "remoteControl": "NOT_SAFETY_RELEVANT_ENABLED",
        "doorState": "CLOSED",
        "cyclePhase": "WASH",
        "timeToEnd": 4500,
        "userSelections": {"programUID": "COTTON_PR_COTTONS", "analogTemperature": "40_CELSIUS",
                           "analogSpinSpeed": "1200_RPM", "steamValue": "STEAM_OFF",
                           "wetMode": True, "dryMode": False},
    }
    base.update(overrides)
    return base


class FakeClient:
    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.reported = reported()

    async def get_appliances(self):
        return [
            Appliance(applianceId="oven-1", applianceName="Trouba", applianceType="OV", created=NOW),
            Appliance(applianceId=APPLIANCE_ID, applianceName="Pračka", applianceType="WD", created=NOW),
        ]

    async def get_appliance_details(self, appliance_id):
        return ApplianceDetails(
            applianceInfo={"serialNumber": "1", "pnc": "914", "brand": "AEG", "deviceType": "WASHER_DRYER",
                           "model": "LWR98165XC", "variant": "", "colour": "WHITE"},
            capabilities={},
        )

    async def get_appliance_state(self, appliance_id):
        return ApplianceState(applianceId=appliance_id, connectionState="Connected", status="enabled",
                              properties={"reported": self.reported})


class FakeNtfy:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, str]] = []

    async def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body.decode("utf-8")))

    def titles(self):
        return [base64.b64decode(h["X-Title"][10:-2]).decode() for _, _, h, _ in self.calls if "X-Title" in h]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "none.env"))
    return Settings(api_key="k", access_token="a", refresh_token="r", token_file=tmp_path / "tokens.json",
                    appliance_id=None, local_api_token=None, poll_interval=300, ntfy_url=NTFY)


def make(settings, ntfy=None):
    holder = {}

    def factory(tm):
        holder["client"] = FakeClient(tm)
        return holder["client"]

    service = WasherService(settings, client_factory=factory, notifier=Notifier(settings.ntfy_url, ntfy or FakeNtfy()),
                            clock=lambda: NOW)
    client = TestClient(create_app(service, start_background=False))
    return client, holder, service


# ------------------------------------------------------------------ build_status
def test_running_summary_with_icons_and_end_time():
    s = build_status(reported(), "Connected", NOW)
    assert s["running"] is True
    assert s["program"] == {"id": "COTTON_PR_COTTONS", "icon": "👕", "name": "Bavlna"}
    assert s["icons"] == ["🌡️40°", "🌀1200"]
    assert s["phase"] == {"id": "WASH", "icon": "🫧", "name": "Praní"}
    assert s["remainingMin"] == 75
    assert s["endsAt"] == "2026-09-30T14:35:00+02:00"
    assert s["text"] == "👕 Bavlna 🌡️40° 🌀1200 · 🫧 Praní · 🏁 14:35"


def test_wash_and_dry_shows_drying_and_steam():
    sel = {"programUID": "SYNTHETIC_PR_SYNTHETICS", "analogTemperature": "60_CELSIUS", "analogSpinSpeed": "0_RPM",
           "steamValue": "STEAM_MAX", "wetMode": True, "dryMode": True, "humidityTarget": "IRON"}
    s = build_status(reported(userSelections=sel, cyclePhase="DRY"), "Connected", NOW)
    assert s["icons"] == ["🌡️60°", "♨️", "🌬️"]  # 0 ot/min se nezobrazuje
    assert s["drying"] == "k žehlení"
    assert s["text"].startswith("🧵 Syntetika 🌡️60° ♨️ 🌬️ · 🌬️ Sušení")


def test_dry_only_hides_wash_options_and_shows_drying_time():
    sel = {"programUID": "COTTON_PR_COTTONS", "analogTemperature": "40_CELSIUS", "analogSpinSpeed": "1600_RPM",
           "wetMode": False, "dryMode": True, "dryingTime": 90}
    s = build_status(reported(userSelections=sel), "Connected", NOW)
    assert s["icons"] == ["🌬️90′"]
    assert s["drying"] == "90 min"


def test_delayed_start_shows_start_and_state():
    s = build_status(reported(applianceState="DELAYED_START", cyclePhase="UNAVAILABLE", startTime=7200,
                              timeToEnd=12600), "Connected", NOW)
    assert s["phase"] is None
    assert s["startsAt"] == "2026-09-30T15:20:00+02:00"
    assert s["text"] == "👕 Bavlna 🌡️40° 🌀1200 · ⏰ Odložený start · ⏰ 15:20 · 🏁 16:50"


@pytest.mark.parametrize("state,connection,text", [
    ("OFF", "Disconnected", "📴 Pračka offline"),
    ("IDLE", "Connected", "💤 Nečinná"),
    ("END_OF_CYCLE", "Connected", "👕 Bavlna 🌡️40° 🌀1200 · ✅ Hotovo"),
])
def test_idle_states(state, connection, text):
    s = build_status(reported(applianceState=state, timeToEnd=0), connection, NOW)
    assert s["running"] is False and s["endsAt"] is None
    assert s["text"] == text


def test_unknown_program_gets_readable_fallback():
    s = build_status(reported(userSelections={"programUID": "NEW_PR_SUPER_CLEAN"}), "Connected", NOW)
    assert s["program"] == {"id": "NEW_PR_SUPER_CLEAN", "icon": "🧺", "name": "Super clean"}


def test_every_real_program_has_czech_name():
    caps = json.loads(FIXTURE.read_text(encoding="utf-8"))
    caps = caps.get("capabilities", caps)
    real = {p for p in caps["userSelections/programUID"]["values"] if "HIDDEN" not in p}  # servisní program
    assert real <= set(PROGRAMS), real - set(PROGRAMS)


# ------------------------------------------------------------------------- API
def test_status_endpoint(env):
    client, _, _ = make(env)
    with client:
        s = client.get("/api/status").json()
        text = client.get("/api/status.txt").text
    assert s["running"] is True and s["endsAt"] == "2026-09-30T14:35:00+02:00"
    assert s["updatedAt"]
    assert text == "👕 Bavlna 🌡️40° 🌀1200 · 🫧 Praní · 🏁 14:35"


@pytest.mark.parametrize("path", ["/", "/api/programs", "/api/raw"])
def test_control_endpoints_are_gone(env, path):
    client, _, _ = make(env)
    with client:
        assert client.get(path).status_code == 404
        assert client.post("/api/start").status_code == 404
        assert client.post("/api/command", json={}).status_code == 404


def test_local_token_required(env):
    settings = Settings(**{**env.__dict__, "local_api_token": "tajne"})
    client, _, _ = make(settings)
    with client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers={"X-API-Token": "tajne"}).status_code == 200
        assert client.get("/api/status.txt?token=tajne").status_code == 200
        assert client.get("/api/health").status_code == 200


def test_sse_event_updates_state(env):
    client, _, service = make(env)
    with client:
        # SDK volá listener uvnitř event loopu
        client.portal.call(service._on_event, {"applianceId": APPLIANCE_ID, "property": "timeToEnd", "value": 600})
        client.portal.call(service._on_event, {"applianceId": APPLIANCE_ID, "property": "cyclePhase", "value": "SPIN"})
        s = client.get("/api/status").json()
    assert s["remainingMin"] == 10
    assert s["phase"]["name"] == "Odstřeďování"


def test_rotated_tokens_are_persisted_and_reused(env):
    client, holder, _ = make(env)
    with client:
        holder["client"].token_manager.update("a2", "r2", "k")  # simulace refreshe
    saved = json.loads(env.token_file.read_text())
    assert saved["refresh_token"] == "r2" and saved["seed_refresh_token"] == "r"

    # po restartu se použijí uložené (novější) tokeny
    holder2 = {}
    with TestClient(create_app(WasherService(env, client_factory=lambda tm: holder2.setdefault("c", FakeClient(tm)),
                                             notifier=Notifier(None)), start_background=False)):
        pass
    assert holder2["c"].token_manager._auth_data.refresh_token == "r2"

    # nové tokeny v .env mají přednost před uloženými
    fresh = Settings(**{**env.__dict__, "access_token": "a3", "refresh_token": "r3"})
    holder3 = {}
    with TestClient(create_app(WasherService(fresh, client_factory=lambda tm: holder3.setdefault("c", FakeClient(tm)),
                                             notifier=Notifier(None)), start_background=False)):
        pass
    assert holder3["c"].token_manager._auth_data.refresh_token == "r3"


# ---------------------------------------------------------------------- ntfy
@pytest.fixture
def anyio_backend():
    return "asyncio"


def st(**kw):
    return build_status(reported(**kw), "Connected", NOW)


@pytest.mark.anyio
async def test_notification_lifecycle():
    ntfy = FakeNtfy()
    n = Notifier(NTFY + "/", ntfy)
    await n.update(st())                                   # start → tichá notifikace
    await n.update(st(timeToEnd=4440))                     # konec se posunul o 1 min → nic
    await n.update(st(cyclePhase="RINSE", timeToEnd=1800))  # nová fáze → přepis
    await n.update(st(cyclePhase="RINSE", timeToEnd=2400))  # konec +10 min → přepis
    await n.update(st(applianceState="END_OF_CYCLE", timeToEnd=0))  # hotovo → se zvukem
    await n.update(st(applianceState="END_OF_CYCLE", timeToEnd=0))  # opakovaně → nic
    await n.update(st(applianceState="OFF", timeToEnd=0))  # vypnuto → smazat

    methods = [(m, u) for m, u, _, _ in ntfy.calls]
    assert methods == [("POST", NTFY)] * 4 + [("DELETE", f"{NTFY}/{SEQUENCE_ID}")]
    assert all(h["X-Sequence-ID"] == SEQUENCE_ID for m, _, h, _ in ntfy.calls if m == "POST")
    assert [h["X-Priority"] for m, _, h, _ in ntfy.calls if m == "POST"] == ["2", "2", "2", "4"]
    assert ntfy.titles()[0] == "👕 Bavlna 🌡️40° 🌀1200"
    assert [b for m, _, _, b in ntfy.calls if m == "POST"] == [
        "🫧 Praní · 🏁 14:35", "💧 Máchání · 🏁 13:50", "💧 Máchání · 🏁 14:00", "✅ Hotovo"]


@pytest.mark.anyio
async def test_no_notification_when_idle_or_finished_before_start():
    ntfy = FakeNtfy()
    n = Notifier(NTFY, ntfy)
    await n.update(st(applianceState="END_OF_CYCLE", timeToEnd=0))  # restart služby po dopraní
    await n.update(st(applianceState="OFF", timeToEnd=0))
    assert ntfy.calls == []


@pytest.mark.anyio
async def test_notifier_disabled_and_errors_swallowed():
    await Notifier(None).update(st())  # bez NTFY_URL nic

    async def boom(*_):
        raise RuntimeError("síť")

    await Notifier(NTFY, boom).update(st())  # chyba se jen zaloguje


def test_service_pushes_notification_on_start(env):
    ntfy = FakeNtfy()
    client, _, _ = make(env, ntfy)
    with client:
        client.get("/api/health")
    assert [m for m, *_ in ntfy.calls] == ["POST"]
