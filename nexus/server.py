"""FastAPI server for the live NEXUS Control Center."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demo import WarehouseDemo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "dashboard"


class FleetCommandRequest(BaseModel):
    package_id: str
    destination_id: str
    category: str | None = None
    priority: int | None = Field(default=None, ge=1, le=10)


class ControlCenterRuntime:
    """One authoritative demo plus one throttled simulation task."""

    def __init__(self, demo_speed: float = 0.65) -> None:
        self.demo_speed = demo_speed
        self.demo: WarehouseDemo | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._subscribers: set[WebSocket] = set()
        self._last_broadcast_tick = -1
        self._last_chain_state: tuple[int, int, int] | None = None

    def ensure_demo(self) -> WarehouseDemo:
        if self.demo is None:
            self.demo = WarehouseDemo(self.demo_speed, mode="manual")
        return self.demo

    async def start_background(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._simulation_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None
        if self.demo is not None:
            self.demo.close()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return self.ensure_demo().snapshot()

    async def command(self, action: str, robot_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            demo = self.ensure_demo()
            if action == "start":
                demo.start()
            elif action == "pause":
                demo.pause()
            elif action == "reset":
                demo.reset()
            elif action == "fleet":
                demo.reset_warehouse()
            elif action == "priority":
                if not demo.submit_priority_job(manual=True):
                    raise ValueError("priority job has already been submitted")
            elif action == "failure":
                if robot_id is None or not demo.inject_failure(robot_id, manual=True):
                    raise ValueError("selected robot is already unavailable or does not exist")
            else:
                raise ValueError(f"unknown action: {action}")
            state = demo.snapshot()
        await self.broadcast(state)
        return state

    async def dispatch_command(
        self,
        package_id: str,
        destination_id: str,
        category: str | None,
        priority: int | None,
    ) -> dict[str, Any]:
        async with self._lock:
            demo = self.ensure_demo()
            demo.submit_fleet_command(
                package_id,
                destination_id,
                category=category,
                priority=priority,
            )
            state = demo.snapshot()
        await self.broadcast(state)
        return state

    async def _simulation_loop(self) -> None:
        frame_interval = 1.0 / 8.0
        while not self._stop_event.is_set():
            demo = self.ensure_demo()
            if not demo.running:
                state = await self.snapshot()
                solana = state["solana"]
                chain_state = (solana["pending"], solana["confirmed"], solana["failed"])
                if chain_state != self._last_chain_state:
                    self._last_chain_state = chain_state
                    await self.broadcast(state)
                await asyncio.sleep(0.25)
                continue

            started = time.perf_counter()
            async with self._lock:
                rate = 200.0 * demo.demo_speed
                ticks_this_frame = max(1, round(rate * frame_interval))
                for _ in range(ticks_this_frame):
                    demo.step()
                    if demo.finished:
                        break
                state = demo.snapshot()
                self._last_broadcast_tick = demo.fleet.steps
                solana = state["solana"]
                self._last_chain_state = (
                    solana["pending"],
                    solana["confirmed"],
                    solana["failed"],
                )
            await self.broadcast(state)

            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.0, frame_interval - elapsed))

    async def broadcast(self, state: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in tuple(self._subscribers):
            try:
                await websocket.send_json(state)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self._subscribers.discard(websocket)

    def subscribe(self, websocket: WebSocket) -> None:
        self._subscribers.add(websocket)

    def unsubscribe(self, websocket: WebSocket) -> None:
        self._subscribers.discard(websocket)


def create_app(runtime: ControlCenterRuntime | None = None) -> FastAPI:
    control = runtime or ControlCenterRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await control.start_background()
        try:
            yield
        finally:
            await control.shutdown()

    app = FastAPI(title="NEXUS Control Center", lifespan=lifespan)
    app.state.runtime = control
    app.mount("/static", StaticFiles(directory=DASHBOARD_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(DASHBOARD_ROOT / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, Any]:
        return await control.snapshot()

    @app.post("/api/demo/{action}")
    async def demo_command(action: str) -> dict[str, Any]:
        try:
            return await control.command(action)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/demo/failure/{robot_id}")
    async def inject_failure(robot_id: str) -> dict[str, Any]:
        try:
            return await control.command("failure", robot_id.upper())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/commands/dispatch")
    async def dispatch_fleet(request: FleetCommandRequest) -> dict[str, Any]:
        try:
            return await control.dispatch_command(
                request.package_id,
                request.destination_id,
                request.category,
                request.priority,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.websocket("/ws")
    async def websocket_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        control.subscribe(websocket)
        try:
            await websocket.send_json(await control.snapshot())
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            control.unsubscribe(websocket)

    return app


runtime = ControlCenterRuntime()
app = create_app(runtime)
