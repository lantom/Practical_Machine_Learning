"""Stav pračky AEG jako JSON (pro agenty, např. Hermes) + notifikace na telefon přes ntfy.

Spuštění:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings
from .washer import WasherError, WasherService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(service: WasherService | None = None, start_background: bool = True) -> FastAPI:
    settings = service.settings if service else Settings.from_env()
    washer = service or WasherService(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await washer.start(background=start_background)
        yield
        await washer.stop()

    app = FastAPI(title="AEG Washer – stav", version="2.0.0", lifespan=lifespan)

    def auth(x_api_token: str | None = Header(None), token: str | None = None) -> None:
        """Volitelná ochrana: pokud je nastaven LOCAL_API_TOKEN, vyžaduje hlavičku X-API-Token."""
        expected = settings.local_api_token
        if expected and expected not in (x_api_token, token):
            raise HTTPException(status_code=401, detail="Neplatný nebo chybějící X-API-Token")

    @app.exception_handler(WasherError)
    async def washer_error_handler(_req: Request, exc: WasherError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health():
        return {"ok": washer.state is not None, "stream": washer.stream_connected}

    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status():
        """Kompaktní stav: běží?, program (ikona + název), ikony voleb, fáze, odhad konce, jednořádkový `text`."""
        return washer.status()

    @app.get("/api/status.txt", dependencies=[Depends(auth)], response_class=PlainTextResponse)
    async def status_text():
        """Jen jednořádkový souhrn, např. „👕 Bavlna 🌡️40° 🌀1200 · 🫧 Praní · 🏁 14:35“."""
        return washer.status()["text"]

    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` – aplikace se vytvoří až při importu atributu `app`,
    # takže testy mohou modul importovat bez přihlašovacích údajů.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
