"""Testy s falešným Electrolux klientem (nevolají cloud)."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from electrolux_group_developer_sdk.client.client_exception import ApplianceClientException
from electrolux_group_developer_sdk.client.dto.appliance import Appliance
from electrolux_group_developer_sdk.client.dto.appliance_details import ApplianceDetails
from electrolux_group_developer_sdk.client.dto.appliance_state import ApplianceState

from app.config import Settings
from app.main import create_app
from app.washer import WasherService

APPLIANCE_ID = "914xxxxxx_00:12345678-443E07ABCDEF"

CAPABILITIES = {
    "userSelections/programUID": {
        "values": {
            "COTTON_PR_COTTONSECO": {
                "userSelections/analogTemperature": {"values": {"40_CELSIUS": {}, "60_CELSIUS": {}}},
            },
            "WOOL_PR_WOOLSILK": {
                "userSelections/analogSpinSpeed": {"values": {"400_RPM": {}, "800_RPM": {}, "DISABLED": {}}},
                "userSelections/steamValue": {"disabled": True},
            },
            "SERVICE_PR": {"disabled": True},
        }
    },
    "userSelections/analogTemperature": {"values": {"20_CELSIUS": {}, "40_CELSIUS": {}, "90_CELSIUS": {}}},
    "userSelections/analogSpinSpeed": {"values": {"1200_RPM": {}, "1600_RPM": {}}},
    "userSelections/steamValue": {"values": {"STEAM_OFF": {}, "STEAM_MAX": {}}},
    "applianceState": {"values": {"IDLE": {}}},
}


def reported(**overrides):
    base = {
        "applianceState": "READY_TO_START",
        "remoteControl": "ENABLED",
        "doorState": "CLOSED",
        "cyclePhase": "UNAVAILABLE",
        "timeToEnd": 0,
        "userSelections": {"programUID": "COTTON_PR_COTTONSECO", "analogTemperature": "40_CELSIUS"},
    }
    base.update(overrides)
    return base


class FakeClient:
    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.commands: list[dict] = []
        self.reported = reported()
        self.fail_with: int | None = None

    async def get_appliances(self):
        return [
            Appliance(applianceId="oven-1", applianceName="Trouba", applianceType="OV", created=datetime.now()),
            Appliance(applianceId=APPLIANCE_ID, applianceName="Pračka", applianceType="WD", created=datetime.now()),
        ]

    async def get_appliance_details(self, appliance_id):
        return ApplianceDetails(
            applianceInfo=dict(serialNumber="1", pnc="914", brand="AEG", deviceType="WASHER_DRYER",
                               model="LWR9W1606X", variant="", colour="WHITE"),
            capabilities=CAPABILITIES,
        )

    async def get_appliance_state(self, appliance_id):
        return ApplianceState(applianceId=appliance_id, connectionState="Connected", status="enabled",
                              properties={"reported": self.reported})

    async def send_command(self, appliance_id, command):
        if self.fail_with:
            raise ApplianceClientException("rejected", status=self.fail_with)
        assert appliance_id == APPLIANCE_ID
        self.commands.append(command)
        return {"ok": True}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "none.env"))
    return Settings(api_key="k", access_token="a", refresh_token="r", token_file=tmp_path / "tokens.json",
                    appliance_id=None, local_api_token=None, poll_interval=300, ntfy_url=None)


def make(settings):
    holder = {}

    def factory(tm):
        holder["client"] = FakeClient(tm)
        return holder["client"]

    service = WasherService(settings, client_factory=factory)
    client = TestClient(create_app(service, start_background=False))
    return client, holder, service


def test_status_picks_washer_and_reports_state(env):
    client, holder, _ = make(env)
    with client:
        s = client.get("/api/status").json()
    assert s["applianceId"] == APPLIANCE_ID
    assert s["applianceState"] == "READY_TO_START"
    assert s["remoteStartAllowed"] is True
    assert s["program"] == "COTTON_PR_COTTONSECO"


def test_programs_merge_global_and_program_options(env):
    client, _, _ = make(env)
    with client:
        progs = {p["id"]: p["options"] for p in client.get("/api/programs").json()}
    assert set(progs) == {"COTTON_PR_COTTONSECO", "WOOL_PR_WOOLSILK"}  # disabled program skryt
    assert progs["COTTON_PR_COTTONSECO"]["analogTemperature"] == ["40_CELSIUS", "60_CELSIUS"]
    assert progs["COTTON_PR_COTTONSECO"]["analogSpinSpeed"] == ["1200_RPM", "1600_RPM"]
    assert progs["WOOL_PR_WOOLSILK"]["analogSpinSpeed"] == ["400_RPM", "800_RPM"]  # bez DISABLED
    assert "steamValue" not in progs["WOOL_PR_WOOLSILK"]


def test_start_sends_selections_then_start(env):
    client, holder, _ = make(env)
    with client:
        r = client.post("/api/start", json={"program": "COTTON_PR_COTTONSECO",
                                            "options": {"analogTemperature": "60_CELSIUS"}})
    assert r.status_code == 200, r.text
    assert holder["client"].commands == [
        {"userSelections": {"programUID": "COTTON_PR_COTTONSECO", "analogTemperature": "60_CELSIUS"}},
        {"executeCommand": "START"},
    ]


def test_start_without_program_only_starts(env):
    client, holder, _ = make(env)
    with client:
        assert client.post("/api/start").status_code == 200
    assert holder["client"].commands == [{"executeCommand": "START"}]


def test_start_rejects_invalid_option(env):
    client, holder, _ = make(env)
    with client:
        r = client.post("/api/start", json={"program": "COTTON_PR_COTTONSECO",
                                            "options": {"analogTemperature": "90_CELSIUS"}})
    assert r.status_code == 422
    assert holder["client"].commands == []


def test_start_blocked_without_remote_start(env):
    client, holder, service = make(env)
    with client:
        holder["client"].reported = reported(remoteControl="NOT_SAFETY_RELEVANT_ENABLED")
        client.post("/api/status/refresh")
        r = client.post("/api/start")
    assert r.status_code == 409
    assert "Dálkové spuštění" in r.json()["detail"]
    assert holder["client"].commands == []


@pytest.mark.parametrize("path,cmd", [("/api/pause", "PAUSE"), ("/api/resume", "RESUME"), ("/api/stop", "STOPRESET")])
def test_simple_commands(env, path, cmd):
    client, holder, _ = make(env)
    with client:
        assert client.post(path).status_code == 200
    assert holder["client"].commands == [{"executeCommand": cmd}]


def test_api_rejection_is_forwarded(env):
    client, holder, _ = make(env)
    with client:
        holder["client"].fail_with = 406
        r = client.post("/api/pause")
    assert r.status_code == 406


def test_local_token_required(env):
    settings = Settings(**{**env.__dict__, "local_api_token": "tajne"})
    client, _, _ = make(settings)
    with client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers={"X-API-Token": "tajne"}).status_code == 200
        assert client.get("/api/health").status_code == 200


def test_sse_event_updates_state(env):
    client, _, service = make(env)
    with client:
        service._on_event({"applianceId": APPLIANCE_ID, "property": "timeToEnd", "value": 3600})
        service._on_event({"applianceId": APPLIANCE_ID, "property": "userSelections/analogSpinSpeed",
                           "value": "1600_RPM"})
        s = client.get("/api/status").json()
    assert s["timeToEnd"] == 3600
    assert s["selections"]["analogSpinSpeed"] == "1600_RPM"


def test_rotated_tokens_are_persisted_and_reused(env):
    client, holder, _ = make(env)
    with client:
        holder["client"].token_manager.update("a2", "r2", "k")  # simulace refreshe
    saved = json.loads(env.token_file.read_text())
    assert saved["refresh_token"] == "r2" and saved["seed_refresh_token"] == "r"

    # po restartu se použijí uložené (novější) tokeny
    _, holder2, _ = make(env)
    with TestClient(create_app(WasherService(env, client_factory=lambda tm: holder2.setdefault("c", FakeClient(tm))),
                               start_background=False)):
        pass
    assert holder2["c"].token_manager._auth_data.refresh_token == "r2"

    # nové tokeny v .env mají přednost před uloženými
    fresh = Settings(**{**env.__dict__, "access_token": "a3", "refresh_token": "r3"})
    holder3 = {}
    with TestClient(create_app(WasherService(fresh, client_factory=lambda tm: holder3.setdefault("c", FakeClient(tm))),
                               start_background=False)):
        pass
    assert holder3["c"].token_manager._auth_data.refresh_token == "r3"
