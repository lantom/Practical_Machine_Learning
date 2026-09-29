"""Lokální REST API + webové ovládání pro pračku AEG přes Electrolux Group Developer API.

Spuštění:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .washer import WasherError, WasherService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"


class StartRequest(BaseModel):
    program: str | None = Field(None, description="programUID, např. COTTON_PR_COTTONSECO")
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Další userSelections, např. {\"analogTemperature\": \"40_CELSIUS\", \"analogSpinSpeed\": \"1200_RPM\", "
                    "\"dryMode\": true, \"humidityTarget\": \"CUPBOARD\"}",
    )
    delay: int | None = Field(None, description="Odložený start v sekundách (násobek kroku z /api/programs → delay)")


def create_app(service: WasherService | None = None, start_background: bool = True) -> FastAPI:
    settings = service.settings if service else Settings.from_env()
    washer = service or WasherService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await washer.start(background=start_background)
        yield
        await washer.stop()

    app = FastAPI(title="AEG Washer – vlastní API", version="1.0.0", lifespan=lifespan)

    def auth(x_api_token: str | None = Header(None), token: str | None = None) -> None:
        """Volitelná ochrana: pokud je nastaven LOCAL_API_TOKEN, vyžaduje hlavičku X-API-Token."""
        expected = settings.local_api_token
        if expected and expected not in (x_api_token, token):
            raise HTTPException(status_code=401, detail="Neplatný nebo chybějící X-API-Token")

    @app.exception_handler(WasherError)
    async def washer_error_handler(_req: Request, exc: WasherError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        return {"ok": washer.state is not None, "stream": washer.stream_connected}

    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status():
        return washer.status()

    @app.post("/api/status/refresh", dependencies=[Depends(auth)])
    async def refresh():
        await washer.refresh_state()
        return washer.status()

    @app.get("/api/programs", dependencies=[Depends(auth)])
    async def programs():
        return washer.programs()

    @app.get("/api/raw", dependencies=[Depends(auth)])
    async def raw():
        """Surová data z API – užitečné pro zjištění názvů programů a voleb (např. sušení)."""
        return {
            "details": washer.details.model_dump() if washer.details else None,
            "state": washer.state.model_dump() if washer.state else None,
        }

    @app.post("/api/start", dependencies=[Depends(auth)])
    async def start(req: StartRequest | None = None):
        req = req or StartRequest()
        return {"result": await washer.start_cycle(req.program, req.options, req.delay)}

    @app.post("/api/pause", dependencies=[Depends(auth)])
    async def pause():
        return {"result": await washer.pause()}

    @app.post("/api/resume", dependencies=[Depends(auth)])
    async def resume():
        return {"result": await washer.resume()}

    @app.post("/api/stop", dependencies=[Depends(auth)])
    async def stop():
        return {"result": await washer.stop_cycle()}

    @app.post("/api/command", dependencies=[Depends(auth)])
    async def command(body: dict[str, Any]):
        """Pokročilé: pošle libovolný příkaz 1:1 do Electrolux API."""
        return {"result": await washer.send(body)}

    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` – aplikace se vytvoří až při importu atributu `app`,
    # takže testy mohou modul importovat bez přihlašovacích údajů.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
