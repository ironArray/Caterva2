import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import httpx
import uvicorn
from watchfiles import Change, awatch


logger = logging.getLogger(__name__)

# Configuration
broker = None
client = None
name = None
root = None

def post(url, json):
    logger.info(f'POST {url} {json}')
    response = httpx.post(url, json=json)
    response.raise_for_status()
    json = response.json()
    logger.info(f'Response {json}')
    return json

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    client.start_client(f'ws://{broker}/pubsub')
    #async with asyncio.timeout(5):
    await client.wait_until_ready() # wait before publishing

    asyncio.create_task(main(client))
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return (x.name for x in root.iterdir())

@app.get("/{name}")
def read_item(name: str):
    file = root / name
    stat = file.stat()
    keys = ['mtime', 'size']
    return {key: getattr(stat, f'st_{key}') for key in keys}

async def main(client):
    # Watch directory for changes
    async for changes in awatch(root):
        for change, path in changes:
            path = Path(path).relative_to(root)
            topic = name if change == Change.added else f'{name}/{path}'
            data = {'change': change.name}
            logger.info(f'PUBLISH {topic} {data}')
            await client.publish([topic], data=data)

def socket(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--broker', default='localhost:8000')
    parser.add_argument('--http', default='localhost:8001', type=socket)
    parser.add_argument('--loglevel', default='warning')
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    # Global configuration
    broker = args.broker
    client = PubSubClient()
    name = args.name
    root = Path(args.root).resolve()

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    post(f'http://{broker}/publishers', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
