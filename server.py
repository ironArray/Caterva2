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


clients = {}
root = Path('data')

app = FastAPI()

@app.get("/clients")
def read_clients():
    return clients

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
async def on_connect(channel):
    clients[channel.id] = None

async def on_disconnect(channel):
    del clients[channel.id]

router = APIRouter()
endpoint = PubSubEndpoint(on_connect=[on_connect], on_disconnect=[on_disconnect])
endpoint.register_route(router)
app.include_router(router)

async def on_register(subscription, data):
    print(f'{subscription} {data=}')

# Watchdog
async def main():
    async for changes in awatch(root):
        for change, path in changes:
            path = Path(path)
            name = path.name
            match change:
                case Change.added:
                    await endpoint.publish(['new'], data={'change': 'added', 'path': path})
                case Change.modified:
                    await endpoint.publish([name], data={'change': 'modified', 'path': path})
                case Change.deleted:
                    await endpoint.publish([name], data={'change': 'deleted', 'path': path})


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(main())
    await endpoint.subscribe(['register'], on_register)
