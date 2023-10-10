"""
uvicorn server:app --reload
"""

import asyncio
from pathlib import Path

# Requirements
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi_websocket_pubsub import PubSubEndpoint
from watchfiles import Change, awatch


root = Path('data')

app = FastAPI()

@app.get("/")
def read_root():
    return (x.name for x in root.iterdir())

@app.get("/{name}")
def read_item(name: str):
    file = root / name
    stat = file.stat()
    keys = ['mtime', 'size']
    return {key: getattr(stat, f'st_{key}') for key in keys}


# Pub/Sub interface
router = APIRouter()
endpoint = PubSubEndpoint()
endpoint.register_route(router)
app.include_router(router)

# Watchdog
async def main():
    async for changes in awatch(root):
        for change, path in changes:
            path = Path(path)
            name = path.name
            match change:
                case Change.added:
                    await endpoint.publish([name], data={'change': 'added', 'path': path})
                case Change.modified:
                    await endpoint.publish([name], data={'change': 'modified', 'path': path})
                case Change.deleted:
                    await endpoint.publish([name], data={'change': 'deleted', 'path': path})


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(main())
