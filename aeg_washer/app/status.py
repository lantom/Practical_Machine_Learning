"""Stručný stav pračky pro lidi a agenty: ikony, název programu česky a odhad konce.

Čistá funkce nad `reported` stavem z Electrolux API, bez sítě – snadno testovatelná.
"""
from datetime import datetime, timedelta
from typing import Any

ACTIVE_STATES = ("RUNNING", "PAUSED", "DELAYED_START")

STATES = {
    "OFF": ("⏻", "Vypnuto"), "IDLE": ("💤", "Nečinná"), "READY_TO_START": ("💤", "Připravena"),
    "RUNNING": ("▶️", "Běží"), "PAUSED": ("⏸️", "Pozastaveno"), "DELAYED_START": ("⏰", "Odložený start"),
    "END_OF_CYCLE": ("✅", "Hotovo"), "ALARM": ("⚠️", "Chyba"),
}
PHASES = {
    "PREWASH": ("🫧", "Předpírka"), "WASH": ("🫧", "Praní"), "RINSE": ("💧", "Máchání"),
    "SPIN": ("🌀", "Odstřeďování"), "DRAIN": ("🚿", "Vypouštění"), "STEAM": ("♨️", "Pára"),
    "DRY": ("🌬️", "Sušení"), "COOL": ("❄️", "Chlazení"), "ANTICREASE": ("👔", "Proti pomačkání"),
}
# programUID → (ikona, krátký český název); pořadí jako na panelu pračky, pak ostatní.
PROGRAMS = {
    "COTTON_PR_ECO40-60": ("🌱", "Eco 40-60"), "COTTON_PR_COTTONS": ("👕", "Bavlna"),
    "SYNTHETIC_PR_SYNTHETICS": ("🧵", "Syntetika"), "NON_STOP_3KG_3H_NONSTOP3H_3KG": ("🔁", "NonStop 3h/3kg"),
    "DELICATE_PR_DELICATES": ("🪶", "Jemné"), "WOOL_PR_WOOL_HANDWASH": ("🧶", "Vlna"),
    "SPORT_JACKETS_PR_OUTDOOR": ("🧥", "Outdoor"), "STEAM_REFRESH_PR_STEAM": ("♨️", "Pára"),
    "SOFTENER_PR_RINSE": ("💧", "Máchání"), "SPIN_PR_DRAIN_SPIN": ("🌀", "Odstředění"),
    "BLANKET_PR_DUVET": ("🛏️", "Přikrývky"), "COTTON_PR_TOWELS": ("🧻", "Ručníky"),
    "COTTON_PR_WORKINGCLOTHES": ("🦺", "Pracovní oděvy"), "DELICATE_PR_BABY": ("🍼", "Dětské"),
    "DENIM_PR_DENIM": ("👖", "Denim"), "DRUM_CLEAN_PR_MACHINECLEAN": ("🧼", "Čištění bubnu"),
    "JEANS_PR_DARKCLOTHES": ("🖤", "Tmavé"), "MINI_PR_SILK": ("🎀", "Hedvábí"),
    "QUICK_20_MIN_PR_20MIN3KG": ("⚡", "Rychlý 20 min"), "SANITISE60_PR_ANTIALLERGY": ("🦠", "Antialergie"),
    "SPORT_JACKETS_PR_DOWN_JACKET": ("🧥", "Péřové bundy"), "STEAM_DEWRINKLER_PR_STEAMCASHMERE": ("♨️", "Kašmír"),
    "SYNTHETIC_PR_BEDLINEN": ("🛏️", "Ložní prádlo"), "SYNTHETIC_PR_MICROFIBRE": ("🧵", "Mikrovlákno"),
    "SYNTHETIC_PR_SPORTWEAR": ("🏃", "Sport"),
}
HUMIDITY = {"CUPBOARD": "do skříně", "EXTRA": "extra suché", "IRON": "k žehlení"}


def _program(uid: str | None) -> dict[str, Any] | None:
    if not uid:
        return None
    icon, name = PROGRAMS.get(uid, ("🧺", uid.split("_PR_")[-1].replace("_", " ").capitalize()))
    return {"id": uid, "icon": icon, "name": name}


def _option_icons(sel: dict[str, Any]) -> list[str]:
    """Hlavní volby jako krátké ikony: teplota, otáčky, pára, sušení."""
    icons = []
    washing = sel.get("wetMode") is not False
    temp = str(sel.get("analogTemperature") or "")
    if washing and temp.endswith("_CELSIUS"):
        icons.append(f"🌡️{temp.split('_')[0]}°")
    elif washing and temp == "COLD":
        icons.append("🌡️❄️")
    spin = str(sel.get("analogSpinSpeed") or "")
    if washing and spin.endswith("_RPM") and spin != "0_RPM":
        icons.append(f"🌀{spin.split('_')[0]}")
    if washing and sel.get("steamValue") not in (None, "STEAM_OFF", "DISABLED"):
        icons.append("♨️")
    if sel.get("dryMode"):
        icons.append(f"🌬️{sel['dryingTime']}′" if sel.get("dryingTime") else "🌬️")
    return icons


def _drying(sel: dict[str, Any]) -> str | None:
    if not sel.get("dryMode"):
        return None
    if sel.get("dryingTime"):
        return f"{sel['dryingTime']} min"
    return HUMIDITY.get(sel.get("humidityTarget"), "automaticky")


def build_status(reported: dict[str, Any], connection: str | None, now: datetime) -> dict[str, Any]:
    """Kompaktní stav. `now` musí být časově lokalizovaný (kvůli `endsAt`)."""
    state = reported.get("applianceState")
    running = state in ACTIVE_STATES
    sel = reported.get("userSelections") or {}
    state_icon, state_name = STATES.get(state, ("❔", state or "neznámý"))
    phase = PHASES.get(reported.get("cyclePhase")) if state == "RUNNING" else None
    program = _program(sel.get("programUID")) if running or state == "END_OF_CYCLE" else None
    options = _option_icons(sel) if program else []

    remaining = reported.get("timeToEnd") if running else None
    remaining = remaining if isinstance(remaining, int) and remaining > 0 else None
    ends_at = (now + timedelta(seconds=remaining)).replace(second=0, microsecond=0) if remaining else None
    starts_at = None
    if state == "DELAYED_START" and isinstance(reported.get("startTime"), int) and reported["startTime"] > 0:
        starts_at = (now + timedelta(seconds=reported["startTime"])).replace(second=0, microsecond=0)

    # Jednořádkový souhrn: „👕 Bavlna 🌡️40° 🌀1200 · 🫧 Praní · 🏁 14:35“
    parts = []
    if program:
        parts.append(" ".join([program["icon"], program["name"], *options]))
    if phase:
        parts.append(" ".join(phase))
    elif state != "RUNNING":
        parts.append(f"{state_icon} {state_name}")
    if starts_at:
        parts.append(f"⏰ {starts_at:%H:%M}")
    if ends_at:
        parts.append(f"🏁 {ends_at:%H:%M}")

    online = (connection or "").lower() == "connected"
    return {
        "running": running,
        "state": state,
        "stateIcon": state_icon,
        "stateText": state_name,
        "online": online,
        "program": program,
        "icons": options,
        "drying": _drying(sel) if program else None,
        "phase": {"id": reported.get("cyclePhase"), "icon": phase[0], "name": phase[1]} if phase else None,
        "remainingMin": round(remaining / 60) if remaining else None,
        "endsAt": ends_at.isoformat() if ends_at else None,
        "startsAt": starts_at.isoformat() if starts_at else None,
        "alerts": [a.get("code", a) if isinstance(a, dict) else a for a in reported.get("alerts") or []],
        "text": " · ".join(parts) if online or running else "📴 Pračka offline",
    }
