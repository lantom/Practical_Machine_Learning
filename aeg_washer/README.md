# AEG pračka – vlastní API a ovládání (bez aplikace AEG)

Malá služba, která běží doma (PC, Raspberry Pi, NAS) a nabízí:

- **REST API** (`/api/...`) pro stav a ovládání pračky se sušičkou,
- **webové ovládání pro mobil** na `http://<ip>:8080/`,
- živou aktualizaci stavu (livestream z cloudu), notifikaci „dopráno“ (ntfy),
- CLI pro rychlé testy.

Testováno proti oficiálnímu SDK `electrolux-group-developer-sdk` 0.7.0 (typ spotřebiče `WD` = pračka se sušičkou,
např. AEG 9000 AbsoluteCare 10/6 kg).

## Jak to funguje (a proč přes cloud)

Pračky AEG/Electrolux **nemají lokální API**. Wi‑Fi modul udržuje jen šifrované spojení do cloudu Electrolux a na
lokální síti na nic neodpovídá. To, že je pračka připojená na domácí Wi‑Fi, tedy pro ovládání nestačí:
musí být **zaregistrovaná na účtu Electrolux/AEG**. Tahle služba pak mluví s oficiálním
**Electrolux Group Developer API** (`api.developer.electrolux.one`) místo aplikace.

```
[mobil/prohlížeč] → [tato služba doma :8080] → api.developer.electrolux.one → [pračka]
```

## 1. Příprava (jednorázově)

1. **Pračka na účtu.** Pokud jsi ji nikdy nepároval v aplikaci *My AEG Care*, udělej to jednou (přidat spotřebič →
   Wi‑Fi 2,4 GHz). Pouhé připojení k Wi‑Fi bez spárování s účtem nestačí. Potom můžeš aplikaci smazat.
2. **API klíč a tokeny.** Přihlas se na <https://developer.electrolux.one> **stejným účtem** jako v aplikaci,
   vytvoř *API Key* a vygeneruj *Access Token* + *Refresh Token*.
3. `cp .env.example .env` a vlož tam `ELX_API_KEY`, `ELX_ACCESS_TOKEN`, `ELX_REFRESH_TOKEN`.

> Refresh token se při každé obnově mění. Služba si nové tokeny ukládá do `data/tokens.json`
> (práva 600). Když v `.env` vložíš nové tokeny, automaticky dostanou přednost.

## 2. Ověření z příkazové řádky

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m app.cli status      # stav pračky
python -m app.cli programs    # programy a povolené volby
python -m app.cli raw > raw.json   # surová data (názvy voleb pro sušení atd.)
```

## 3. Spuštění serveru

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
# nebo
docker compose up -d --build
```

- Ovládání: `http://<ip-počítače>:8080/`
- Dokumentace API (Swagger): `http://<ip-počítače>:8080/docs`

### Na VPS přes Tailscale (doporučeno)

Služba mluví jen s cloudem Electrolux, takže nemusí běžet doma. Na VPS s Tailscale je dostupná jen z tvých
zařízení v tailnetu a ne z internetu:

```bash
docker compose up -d --build              # port 8080 je navázaný jen na 127.0.0.1
sudo tailscale serve --bg 8080            # HTTPS jen v tailnetu: https://<vps>.<tailnet>.ts.net/
```

## API

| Metoda | Cesta | Popis |
|---|---|---|
| GET | `/api/status` | stav, fáze, zbývající čas, dvířka, povolení dálkového startu |
| POST | `/api/status/refresh` | vynutí načtení stavu z cloudu |
| GET | `/api/programs` | programy + volby pro každý program (teplota, otáčky, pára, sušení…) |
| GET | `/api/raw` | surové `capabilities` a `state` z Electrolux API |
| POST | `/api/start` | `{"program": "...", "options": {"analogTemperature": "40_CELSIUS"}}`, obojí volitelné |
| POST | `/api/pause`, `/api/resume`, `/api/stop` | ovládání běžícího cyklu |
| POST | `/api/command` | libovolný příkaz 1:1 do Electrolux API (pro pokročilé) |

Příklad:

```bash
curl -X POST http://localhost:8080/api/start -H 'Content-Type: application/json' \
  -d '{"program":"COTTON_PR_COTTONSECO","options":{"analogTemperature":"40_CELSIUS","analogSpinSpeed":"1200_RPM"}}'
```

Přesné názvy programů a voleb se liší podle modelu. Vždy je vezmi z `/api/programs`.
Volby sušení se objeví automaticky, pokud je model v API nabízí (klíče `userSelections/*`).

Pokud nastavíš `LOCAL_API_TOKEN`, všechna `/api/*` volání (kromě `/api/health`) vyžadují hlavičku
`X-API-Token`. Ve webovém UI ho zadáš v sekci *Nastavení*.

## Dálkový start – důležité

Z bezpečnostních důvodů pračka přijme **START** jen tehdy, když na ní máš **povolené dálkové spuštění**
(`remoteControl = ENABLED`). Obvykle: naplnit, zavřít dvířka a na panelu zapnout *Dálkové spuštění* (přesný postup
je v návodu k pračce). Bez toho API vrátí `409` s vysvětlením. Pauza a stop fungují podle stavu pračky.

## Notifikace

Nastav `NTFY_URL=https://ntfy.sh/<tvůj-tajný-kanál>`, nainstaluj si aplikaci ntfy a odebírej stejný kanál.
Při přechodu z běžícího programu do stavu `END_OF_CYCLE` přijde zpráva.

## Vývoj a testy

```bash
pip install -r requirements-dev.txt
pytest -q
```

Testy používají falešného klienta a do cloudu se nepřipojují.
