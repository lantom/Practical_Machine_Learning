# AEG pračka – stav do telefonu a pro agenty

Malá služba, která jen **sleduje** pračku se sušičkou AEG (bez ovládání) a nabízí:

- **průběžnou notifikaci na Androidu** (aplikace ntfy): během programu ukazuje ikony programu a voleb,
  aktuální fázi a odhad konce, po dopraní se změní na „✅ Hotovo“ (se zvukem) a po vypnutí pračky zmizí,
- **JSON stav** `GET /api/status` pro agenty (např. Hermes) a jednořádkový souhrn `GET /api/status.txt`.

Ovládání (start, pauza, programy) záměrně není: pračka dálkový start bez fyzického potvrzení na panelu
stejně nepovolí (`remoteControl` je v API jen pro čtení).

Testováno proti oficiálnímu SDK `electrolux-group-developer-sdk` 0.7.0 (typ `WD`, AEG LWR98165XC).

## Jak to funguje

Pračky AEG/Electrolux **nemají lokální API**, jen spojení do cloudu. Služba poslouchá oficiální
**Electrolux Group Developer API** (livestream + záložní polling) a stav drží v paměti.

```
pračka → cloud Electrolux → tato služba ─┬→ GET /api/status   (Hermes, curl)
                                          └→ ntfy.sh → notifikace na Androidu
```

## 1. Příprava (jednorázově)

1. **Pračka na účtu** – spárovaná v aplikaci *My AEG Care* (Wi‑Fi 2,4 GHz).
2. **API klíč a tokeny** z <https://developer.electrolux.one> (stejný účet): *API Key*, *Access Token*, *Refresh Token*.
3. `cp .env.example .env` a vyplnit `ELX_API_KEY`, `ELX_ACCESS_TOKEN`, `ELX_REFRESH_TOKEN`.

> Refresh token se při každé obnově mění. Služba si nové tokeny ukládá do `data/tokens.json`
> (práva 600). Když v `.env` vložíš nové tokeny, automaticky dostanou přednost.

## 2. Notifikace na telefonu (Android)

1. Vymysli téma s náhodným názvem (kdo ho zná, vidí stav pračky), např. `pracka-3f9c1a7b2e`.
2. Do `.env`: `NTFY_URL=https://ntfy.sh/pracka-3f9c1a7b2e`
3. V telefonu nainstaluj **ntfy** (Google Play / F‑Droid) → **+** → stejné téma.
4. V Nastavení Androidu → Aplikace → ntfy → Oznámení můžeš kanál „Low priority“ nechat tichý;
   ikona v liště se zobrazuje, zvuk jen u „Hotovo“.

Notifikace se přepisuje (hlavička `X-Sequence-ID`) jen při změně fáze nebo posunu odhadu konce o ≥ 3 min,
takže telefon nezahlcuje.

## 3. Spuštění

```bash
docker compose up -d --build              # port 8080 je navázaný jen na 127.0.0.1
sudo tailscale serve --bg 8080            # HTTPS jen v tailnetu: https://<vps>.<tailnet>.ts.net/
```

Rychlé ověření bez serveru: `docker compose run --rm aeg-washer python -m app.cli status`

## API

| Metoda | Cesta | Popis |
|---|---|---|
| GET | `/api/status` | kompaktní stav (viz níže) |
| GET | `/api/status.txt` | jen jednořádkový souhrn |
| GET | `/api/health` | `{"ok": true, "stream": true}` |

Při nastaveném `LOCAL_API_TOKEN` je potřeba hlavička `X-API-Token` nebo `?token=`.

Příklad během praní:

```json
{
  "running": true,
  "state": "RUNNING",
  "stateIcon": "▶️",
  "stateText": "Běží",
  "online": true,
  "program": {"id": "COTTON_PR_COTTONS", "icon": "👕", "name": "Bavlna"},
  "icons": ["🌡️40°", "🌀1200", "🌬️"],
  "drying": "do skříně",
  "phase": {"id": "WASH", "icon": "🫧", "name": "Praní"},
  "remainingMin": 75,
  "endsAt": "2026-09-30T14:35:00+02:00",
  "startsAt": null,
  "alerts": [],
  "text": "👕 Bavlna 🌡️40° 🌀1200 🌬️ · 🫧 Praní · 🏁 14:35",
  "updatedAt": "2026-09-30T13:20:10+02:00"
}
```

Mimo praní je `running: false` a `text` např. `💤 Nečinná`, `✅ Hotovo` nebo `📴 Pračka offline`.

### Hermes Agent

Stačí mu dát URL (je v tailnetu, takže Hermes musí běžet na stroji s Tailscale):

```
Stav pračky zjistíš: curl -s https://claudius-vps.<tailnet>.ts.net/api/status
Pole `text` je hotový souhrn, `endsAt` odhad konce, `running` zda pere.
```

## Vývoj a testy

```bash
pip install -r requirements-dev.txt
pytest -q
```
