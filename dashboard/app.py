"""FastAPI entry point for the plain HTML dashboard."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from blockchain.chain import ValidationError as ChainValidationError
from .orchestrator import DashboardOrchestrator


STATIC_DIR = Path(__file__).resolve().parent / "static"
STATE_PUSH_INTERVAL_S = 0.5


class WalletRequest(BaseModel):
    name: str | None = None
    initial_balance: int = 0


class PeerRequest(BaseModel):
    start_mining: bool = False
    miner_wallet_id: int | None = None


class TransactionRequest(BaseModel):
    peer_id: int
    sender_wallet_id: int
    recipient_wallet_id: int
    amount: int


def create_app(orchestrator: DashboardOrchestrator | None = None) -> FastAPI:
    dashboard = orchestrator or DashboardOrchestrator()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.orchestrator = dashboard
        try:
            yield
        finally:
            await dashboard.stop()

    app = FastAPI(title="Blockchain Dashboard", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def get_state() -> dict:
        return dashboard.snapshot()

    @app.post("/api/start")
    async def start_tracker() -> dict:
        return await dashboard.start_tracker()

    @app.post("/api/wallets")
    async def create_wallet(req: WalletRequest) -> dict:
        try:
            return dashboard.create_wallet(req.name, req.initial_balance)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/peers")
    async def create_peer(req: PeerRequest) -> dict:
        try:
            return await dashboard.create_peer(
                miner_wallet_id=req.miner_wallet_id,
                start_mining=req.start_mining,
            )
        except (AssertionError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/api/transactions")
    async def submit_transaction(req: TransactionRequest) -> dict:
        try:
            return await dashboard.submit_transaction(
                peer_id=req.peer_id,
                sender_wallet_id=req.sender_wallet_id,
                recipient_wallet_id=req.recipient_wallet_id,
                amount=req.amount,
            )
        except (ChainValidationError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.websocket("/ws/state")
    async def state_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(dashboard.snapshot())
                await asyncio.sleep(STATE_PUSH_INTERVAL_S)
        except WebSocketDisconnect:
            return

    return app


app = create_app()


def main() -> None:
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
