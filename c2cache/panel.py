import asyncio
import pathlib

import jinja2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = pathlib.Path(__file__).resolve().parent


def make_router(provider):
    from c2cache import peercache
    from caterva2.services.server import custom_filesizeformat
    from caterva2.services.server import templates as srv_templates

    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.loader = jinja2.ChoiceLoader([templates.env.loader, srv_templates.env.loader])
    router = APIRouter()

    @router.get("/panel", response_class=HTMLResponse)
    async def panel(request: Request):
        """Read-only peers status fragment. Re-probes on each render (no
        background timer, MVP scope); probes run in parallel threads."""

        def peer_entry(peer):
            provider.registry._handshake(peer)
            cachedir = peercache.pool_dir / peer.name if peercache.pool_dir else None
            cached_bytes = (
                sum(f.stat().st_size for f in cachedir.rglob("*") if f.is_file())
                if cachedir and cachedir.is_dir()
                else 0
            )
            return {
                "name": peer.name,
                "urlbase": peer.urlbase,
                "online": peer.online,
                "cached": custom_filesizeformat(cached_bytes),
            }

        entries = []
        if provider.registry is not None:
            entries = await asyncio.gather(
                *(asyncio.to_thread(peer_entry, p) for p in provider.registry.peers.values())
            )
        return templates.TemplateResponse(request, "peers_panel.html", {"peers": entries})

    return router
