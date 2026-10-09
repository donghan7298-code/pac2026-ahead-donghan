from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

from aiohttp import WSMsgType, web

from .models import BoxSpec
from .simulator import AheadLiveSimulator


class AheadWebServer:
    def __init__(
        self,
        simulator: AheadLiveSimulator,
        project_root: Path,
    ) -> None:
        self.simulator = simulator
        self.project_root = project_root
        self.viewer_dir = project_root / "viewer" / "ahead_live"
        self.clients: Set[web.WebSocketResponse] = set()
        self._physics_task: Optional[asyncio.Task] = None
        self._auto_task: Optional[asyncio.Task] = None

    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/app.js", self.app_js)
        app.router.add_get("/style.css", self.style_css)
        app.router.add_get("/ws", self.ws_handler)
        app.router.add_get("/api/state", self.api_state)
        app.router.add_post("/api/reset", self.api_reset)
        app.router.add_post("/api/demo/next", self.api_demo_next)
        app.router.add_post("/api/demo/auto", self.api_demo_auto)
        app.router.add_post("/api/place", self.api_place)
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        return app

    async def index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.viewer_dir / "index.html")

    async def app_js(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.viewer_dir / "app.js")

    async def style_css(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.viewer_dir / "style.css")

    async def api_state(self, request: web.Request) -> web.Response:
        return web.json_response(self.simulator.snapshot())

    async def api_reset(self, request: web.Request) -> web.Response:
        self.simulator.reset()
        await self.broadcast(self.simulator.snapshot())
        return web.json_response({"ok": True})

    async def api_demo_next(self, request: web.Request) -> web.Response:
        spec = self.simulator.add_next_demo_box()
        await self.broadcast(self.simulator.snapshot())
        return web.json_response(
            {"ok": True, "placed": spec.as_dict() if spec else None}
        )

    async def api_demo_auto(self, request: web.Request) -> web.Response:
        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._run_auto_sequence())
        return web.json_response({"ok": True})

    async def api_place(self, request: web.Request) -> web.Response:
        """Place one AHEAD-selected box in the live Bullet world.

        JSON:
        {
          "id": "B101",
          "size_m": [0.4, 0.3, 0.2],
          "mass_kg": 7.2,
          "target_position_m": [0.1, -0.2, 0.1],
          "yaw_rad": 0.0
        }
        """
        try:
            payload = await request.json()
            spec = BoxSpec.from_mapping(payload)
            self.simulator.place_box(spec)
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=400
            )

        await self.broadcast(self.simulator.snapshot())
        return web.json_response({"ok": True, "placed": spec.as_dict()})

    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json(self.simulator.snapshot())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "state":
                    await ws.send_json(self.simulator.snapshot())
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        return ws

    async def on_startup(self, app: web.Application) -> None:
        self._physics_task = asyncio.create_task(self._physics_loop())

    async def on_cleanup(self, app: web.Application) -> None:
        for task in (self._auto_task, self._physics_task):
            if task and not task.done():
                task.cancel()
        self.simulator.close()

    async def _run_auto_sequence(self) -> None:
        while True:
            spec = self.simulator.add_next_demo_box()
            if spec is None:
                return
            await self.broadcast(self.simulator.snapshot())
            await asyncio.sleep(1.35)

    async def _physics_loop(self) -> None:
        ph = self.simulator.config.physics
        steps_per_loop = max(
            1, int(round(ph.physics_hz / max(1, ph.server_loop_hz)))
        )
        broadcast_every = max(
            1, int(round(ph.server_loop_hz / max(1, ph.viewer_hz)))
        )
        loop_period = 1.0 / float(max(1, ph.server_loop_hz))
        tick = 0

        while True:
            started = asyncio.get_running_loop().time()
            self.simulator.step(steps_per_loop)
            tick += 1
            if tick % broadcast_every == 0:
                await self.broadcast(self.simulator.snapshot())

            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, loop_period - elapsed))

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        if not self.clients:
            return
        dead = []
        text = json.dumps(payload, separators=(",", ":"))
        for ws in tuple(self.clients):
            try:
                await ws.send_str(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
