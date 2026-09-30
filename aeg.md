# AEG pračka – předávací dokumentace session

> **Pro Clauda (nová session):** Tento soubor je kompletní předávka rozpracované práce z jiné session.
> Přečti ho celý, pak pokračuj od sekce **„8. Co zbývá udělat“**. Komunikuj s uživatelem **česky**,
> jednoduše a **krok po kroku**. Uživatel výslovně chtěl vést postupně: jeden krok, pak počkat na výsledek.
> Nikdy po uživateli nechtěj, aby ti klíče nebo tokeny vkládal do chatu, a nikdy nevypisuj obsah `.env`
> ani `data/tokens.json`.
>
> **Aktualizace 2026-09-30:** aplikace je přestavěná **jen na sledování stavu** (bez ovládání, programů a web UI).
> Pračka nepovolí dálkový start bez potvrzení na panelu, takže ovládání nedávalo smysl. Zbylo:
> `GET /api/status` (JSON pro Hermes Agent), `GET /api/status.txt` a průběžná ntfy notifikace na Androidu
> (`NTFY_URL`). Aktuální popis je v `aeg_washer/README.md`; části níže o ovládání a UI jsou historické.

---

## 1. Cíl

Uživatel (Tomáš) chce ovládat svou pračku se sušičkou **AEG** **bez oficiální aplikace AEG**, přes
**vlastní API a vlastní webové ovládání** (hlavně z mobilu).

**Spotřebič** (podle fotky ovládacího panelu):
- AEG **WASH · DRY · STEAM 9000 SERIES, AbsoluteCare 10/6 kg**, HeatPump, Wi‑Fi. Česky popsaný panel.
- Programy na panelu: Eco 40‑60, Bavlna, Syntetika, NonStop 3h/3kg, Jemné prádlo, Vlna/Ruční praní, Outdoor,
  Pára, Máchání, Odstřeďování/Vypouštění.
- Tlačítka: Teplota, Odstřeďování, Skvrny/Předpírka, Odložený start, MÓD (Praní/Sušení), Čas sušení,
  AutoDry, Proti pomačkání, Rychlý, Start/Pauza.
- V Electrolux API se očekává typ spotřebiče **`WD`** (washer‑dryer).

## 2. Klíčová zjištění a rozhodnutí

1. **Pračka nemá lokální API.** Wi‑Fi modul drží jen šifrované spojení do cloudu Electrolux a na LAN nic
   neposlouchá. Lokální ovládání by vyžadovalo hardwarový zásah, a to jsme zavrhli.
2. **Řešení: oficiální Electrolux Group Developer API**
   (`https://api.developer.electrolux.one`, portál `https://developer.electrolux.one`) přes oficiální Python SDK
   **`electrolux-group-developer-sdk==0.7.0`** (PyPI, Apache‑2.0).
3. Pračka **musí být spárovaná s účtem AEG** v aplikaci *My AEG Care*. ✅ **Uživatel to má hotové**: účet má
   a pračka je připojená na Wi‑Fi (síť „airplug“).
4. **Nasazení:** uživatel chce službu provozovat na **VPS přes Tailscale**. Je to vhodné, protože služba stejně
   mluví jen s cloudem a nemusí běžet doma. Port je navázaný jen na `127.0.0.1` a ven se publikuje přes
   `tailscale serve`, takže je dostupný jen v tailnetu a ne z internetu.
5. Původní cloudová session se na VPS připojit nemohla (izolovaný kontejner mimo tailnet). Proto se práce
   přenáší do session, která běží na počítači uživatele nebo přímo na VPS.

## 3. Infrastruktura uživatele

Tailnet (výstup `tailscale status` z VPS):

| Tailscale IP | Hostname | OS | Poznámka |
|---|---|---|---|
| 100.126.66.60 | `claudius-vps` | linux | **cílová VPS**, uživatel `tomas`, shell prompt `tomas@claudius` |
| 100.116.223.107 | `dgx-spark` | linux | |
| 100.73.64.2 | `tompc` | windows | PC uživatele |
| 100.127.161.13 | `tom-fold8` | android | telefon, byl **offline**. Pro přístup z mobilu musí mít zapnutou aplikaci Tailscale |
| 100.83.90.5 | `tom-s24u` | android | starý telefon, offline 48 dní |

Na VPS je ověřeno: **Tailscale běží**, **Docker 29.7.2**, **Docker Compose v5.5.0**. Na VPS je nainstalovaný
i Claude Code CLI.

Z PC (`tompc`) se na VPS připojíš `ssh tomas@claudius-vps` (nebo `ssh tomas@100.126.66.60`), pokud je PC
v tailnetu a SSH je povolené.

## 4. Kód – kde je a co obsahuje

- **Repo:** `https://github.com/lantom/Practical_Machine_Learning` (veřejné; zbytek repa je nesouvisející
  ML write‑up)
- **Větev:** `ccr-363d87d8-hsud5y`, na ni pushuj. Do `master` bez výslovného souhlasu nepushuj a PR nezakládej,
  dokud o něj uživatel nepožádá.
- **Složka:** `aeg_washer/`

```
aeg_washer/
├── app/
│   ├── config.py        # Settings z env / .env (vlastní mini loader)
│   ├── token_store.py   # trvalé uložení rotujících tokenů do data/tokens.json (chmod 600)
│   ├── washer.py        # WasherService – jádro: SDK klient, stav, SSE, příkazy, validace
│   ├── main.py          # FastAPI app (create_app), REST endpointy, volitelná auth X-API-Token
│   ├── cli.py           # python -m app.cli status|programs|raw|start|pause|resume|stop
│   └── static/index.html# mobilní web UI (vanilla JS, světlý/tmavý režim)
├── tests/test_api.py    # 13 testů s FakeClient (bez cloudu) – všechny prochází
├── Dockerfile           # python:3.12-slim, uvicorn na :8080
├── docker-compose.yml   # port 127.0.0.1:8080, volume ./data, env_file .env
├── requirements.txt / requirements-dev.txt
├── .env.example         # šablona konfigurace
├── .gitignore           # .env, data/ …
└── README.md            # uživatelská dokumentace (česky)
```

Commity na větvi:
- `a77d28d` Add local REST API and web UI for AEG washer-dryer via Electrolux Developer API
- `017afb0` aeg_washer: bind to localhost and document Tailscale VPS setup
- (+ commit s tímto souborem `aeg.md`)

## 5. Jak to funguje (technicky)

### Electrolux Developer API (ověřeno ze zdrojáků SDK 0.7.0)
- Base: `https://api.developer.electrolux.one`
- Hlavičky: `Authorization: Bearer <access_token>`, `x-api-key: <api_key>`
- `POST /api/v1/token/refresh` s body `{"refreshToken": "..."}` vrátí `{accessToken, refreshToken}`.
  **Refresh token rotuje.** Starý přestane platit, proto nové tokeny ukládáme (`TokenStore`).
- `GET /api/v1/appliances` vrátí seznam `{applianceId, applianceName, applianceType, created}`
- `GET /api/v1/appliances/{id}/info` vrátí `{applianceInfo{serialNumber,pnc,brand,deviceType,model,variant,colour}, capabilities{...}}`
- `GET /api/v1/appliances/{id}/state` vrátí `{applianceId, connectionState, status, properties{reported{...}}}`
- `PUT /api/v1/appliances/{id}/command`, body např. `{"executeCommand":"START"}`
- `GET /api/v1/configurations/livestream` vrátí URL pro SSE stream (události `{applianceId, property, value}`)
- SDK limit: 10 req/s, 5 souběžně, retry na 429/504.

### Příkazy pro WD (z `wd_config.py` v SDK)
- `{"executeCommand": "START" | "PAUSE" | "RESUME" | "STOPRESET"}`
- Nastavení programu a voleb: `{"userSelections": {"programUID": "<PROGRAM>", "analogTemperature": "40_CELSIUS", "analogSpinSpeed": "1200_RPM", ...}}`,
  vždy **včetně `programUID`**.
- Reported stav: `applianceState` (OFF, IDLE, READY_TO_START, RUNNING, PAUSED, DELAYED_START, END_OF_CYCLE, …),
  `cyclePhase`, `timeToEnd` (s), `doorState`, `remoteControl`, `alerts`, `userSelections{...}`, `startTime`, `stopTime`.
- `remoteControl`: `ENABLED` | `NOT_SAFETY_RELEVANT_ENABLED` | `DISABLED` | `TEMPORARY_LOCKED`.
  **START projde jen při `ENABLED`.** Uživatel musí na pračce zapnout „Dálkové spuštění“ (bezpečnostní norma).
  Přesnou kombinaci tlačítek pro tento model neznáme, je v návodu k pračce.
- Capabilities: `capabilities["userSelections/programUID"]["values"][PROGRAM]` obsahuje per‑program přepisy
  `userSelections/*` (`values`, `disabled`). Globální volby jsou v `capabilities["userSelections/<volba>"]`.

### Naše služba
- `WasherService.start()`:
  1. načte tokeny (uložené mají přednost, pokud se `ELX_REFRESH_TOKEN` v `.env` nezměnil, podle `seed_refresh_token`)
  2. vybere první spotřebič typu WD/WM/TD, nebo ten z `APPLIANCE_ID`
  3. stáhne info a stav
  4. spustí SSE livestream a záložní polling (`POLL_INTERVAL`, default 300 s)
- `programs()` obecně skládá volby ze všech `userSelections/*`, takže se volby sušení objeví, pokud je API nabízí.
- `start_cycle()` zkontroluje `remoteControl == ENABLED` (jinak 409), zavřená dvířka a platnost programu i voleb
  (jinak 422). Pak pošle `userSelections` a nakonec `executeCommand: START`.
- Při přechodu RUNNING/PAUSED/DELAYED_START → END_OF_CYCLE pošle notifikaci na `NTFY_URL` (volitelné).
- REST: `GET /api/health`, `GET /api/status`, `POST /api/status/refresh`, `GET /api/programs`, `GET /api/raw`,
  `POST /api/start` (`{"program":..., "options":{...}}`), `POST /api/pause|resume|stop`, `POST /api/command`
  (raw). Swagger je na `/docs`.
- `LOCAL_API_TOKEN` (volitelné): zapne povinnou hlavičku `X-API-Token` pro `/api/*` kromě `/api/health`.

### Konfigurace (`.env`)
```
ELX_API_KEY=          # z developer.electrolux.one
ELX_ACCESS_TOKEN=     # z developer.electrolux.one
ELX_REFRESH_TOKEN=    # z developer.electrolux.one
APPLIANCE_ID=         # volitelné
LOCAL_API_TOKEN=      # volitelné
TOKEN_FILE=data/tokens.json
POLL_INTERVAL=300
NTFY_URL=             # volitelné, např. https://ntfy.sh/<tajny-kanal>
```

## 6. Ověřeno vs. neověřeno

✅ Ověřeno:
- 13 unit testů (FakeClient) prochází: výběr pračky, programy a volby, start/pause/resume/stop, 409 bez
  dálkového startu, 422 u neplatné volby, přeposlání chyby API, lokální token, SSE update, rotace a persistence tokenů
- Web UI vyrenderované v Chromiu (390 px, mobil) bez JS chyb, tlačítka fungují

❌ Neověřeno (zatím chybí reálné klíče):
- volání proti skutečnému Electrolux API a skutečné pračce
- skutečné názvy `programUID` a voleb (hlavně sušení) pro tento model
- zda SSE livestream na tomto účtu funguje
- sestavení Docker image na VPS

## 7. Kde jsme s uživatelem skončili (postup krok po kroku)

- **Krok 1: pračka na účtu AEG** ✅ hotovo
- **Krok 2: klíče z developer.electrolux.one** ❓ Návod dostal (přihlásit se stejným účtem jako v aplikaci →
  Dashboard → vytvořit API Key → Generate token → uložit API Key, Access Token, Refresh Token). **Nepotvrdil,
  že je má.** Nejdřív se na to zeptej.
- **Krok 3: příprava VPS** ✅ hotovo (Tailscale + Docker ověřeny)
- **Krok 4: stáhnout kód na VPS a vyplnit `.env`** ⏳ další krok, ještě neprovedeno
- **Krok 5: spustit a publikovat přes Tailscale** ⏳
- **Krok 6: otevřít v mobilu** ⏳

## 8. Co zbývá udělat

Pracuj krok po kroku a po každém kroku počkej na uživatele. Pokud běžíš **přímo na VPS**, příkazy spouštěj sám.
Pokud běžíš na **PC uživatele** (Windows `tompc`), zkus nejdřív `ssh tomas@claudius-vps`. Když to nejde, dávej
uživateli příkazy ke spuštění.

1. **Zeptej se, jestli má klíče** (krok 2). Pokud ne, proveď ho portálem.
2. **Stažení kódu na VPS:**
   ```bash
   git clone -b ccr-363d87d8-hsud5y https://github.com/lantom/Practical_Machine_Learning.git ~/pracka-src
   cd ~/pracka-src/aeg_washer
   cp .env.example .env
   ```
   (Pokud už klon existuje: `cd ~/pracka-src && git pull`.)
3. **`.env` vyplní uživatel sám** (`nano .env`, uložit Ctrl+O, Enter, Ctrl+X), pak `chmod 600 .env`.
   Do `.env` nesahej a nevypisuj ho.
4. **Test:**
   ```bash
   docker compose build
   docker compose run --rm aeg-washer python -m app.cli status
   docker compose run --rm aeg-washer python -m app.cli programs
   docker compose run --rm aeg-washer python -m app.cli raw > data/raw.json   # pro ladění, jen lokálně
   ```
   (`permission denied` u Dockeru vyřešíš přes `sudo` nebo `sudo usermod -aG docker tomas` a znovu se přihlásit.)
5. **Spuštění a publikace:**
   ```bash
   docker compose up -d
   sudo tailscale serve --bg 8080
   tailscale serve status        # ukáže https://claudius-vps.<tailnet>.ts.net/
   ```
6. **Mobil:** zapnout Tailscale na `tom-fold8`, otevřít adresu ze `serve status`, přidat na plochu.
7. **Doladění podle reálných dat** (po výstupu `programs` / `raw`):
   - v `app/static/index.html` doplnit české názvy programů (mapování `programUID` → „Bavlna“, „Eco 40‑60“,
     „Syntetika“, „NonStop 3h/3kg“, „Jemné prádlo“, „Vlna/Ruční praní“, „Outdoor“, „Pára“, „Máchání“,
     „Odstřeďování/Vypouštění“) a popisky voleb (sušení, pára, skvrny, …) v `OPT_LABELS`
   - ověřit, jak API reprezentuje sušení (MÓD Praní/Sušení, Čas sušení, AutoDry) a případně přidat UI
   - podle reálných dat upravit `tests/test_api.py` (`CAPABILITIES`) a spustit `pytest -q`
   - commit a `git push -u origin ccr-363d87d8-hsud5y`; na VPS pak `git pull && docker compose up -d --build`
8. **Volitelně:** nastavit `NTFY_URL` (notifikace „dopráno“), `LOCAL_API_TOKEN`, a otestovat start z UI.
   Uživatel musí mít na pračce povolené dálkové spuštění.

## 9. Známá rizika a tipy

- **Rotace refresh tokenu:** když `data/tokens.json` smažeš nebo se ztratí a tokeny v `.env` jsou staré, přihlášení
  selže. Je potřeba vygenerovat nové na portálu, dát je do `.env` a restartovat. Nové tokeny v `.env` mají
  automaticky přednost před uloženými.
- `docker compose run` a `up` sdílí `./data`, takže rotované tokeny z testu použije i běžící služba. Neběž paralelně
  dvě instance se stejnými tokeny, jinak si navzájem zneplatní refresh token.
- API mění klíče a hodnoty mezi verzemi SDK. Proto je SDK pevně na `0.7.0`.
- `tailscale serve` vyžaduje v tailnetu zapnuté HTTPS certifikáty (admin konzole → DNS → HTTPS). Pokud nejdou,
  alternativa je `http://100.126.66.60:8080`, ale jen po změně portu v compose na `100.126.66.60:8080:8080`
  (nikdy ne `0.0.0.0` na VPS).
- Do commitů, PR ani kódu nepiš identifikátory modelu. Commity končí řádkem `Co-Authored-By: Claude <noreply@anthropic.com>`.

## 10. Odkazy

- Developer portál: https://developer.electrolux.one
- SDK: https://pypi.org/project/electrolux-group-developer-sdk/
- Inspirace a HA integrace: https://github.com/TTLucian/ha-electrolux, https://github.com/albaintor/homeassistant_electrolux_status
- Původní cloudová session: https://claude.ai/code/session_0132jVkJNAv2otda6RnxsZGw

---

### Jak tento soubor použít (pro uživatele)

Na PC nebo na VPS:
```bash
git clone -b ccr-363d87d8-hsud5y https://github.com/lantom/Practical_Machine_Learning.git ~/pracka-src
cd ~/pracka-src
claude "Přečti aeg.md a pokračuj v práci podle něj."
```
