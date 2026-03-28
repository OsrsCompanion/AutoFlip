from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import ai, market, player, screen, settings

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(settings.router)
app.include_router(screen.router)
app.include_router(ai.router)
app.include_router(player.router)

UI_DIR = Path(__file__).resolve().parent / "ui"
app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
