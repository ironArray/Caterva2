import argparse
import asyncio
from pathlib import Path

# Requirements
from fastapi import FastAPI
from fastapi_websocket_pubsub import PubSubClient
import uvicorn
from watchfiles import Change, awatch


# Configuration
broker = None
client = None
name = None
root = None


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

async def on_connect(client, channel):
    if name:
        await client.publish(['register'], {'name': name}, sync=False)

async def main(client):
    # Watch directory for changes
    async for changes in awatch(root):
        for change, path in changes:
            path = Path(path)
            name = path.name
            match change:
                case Change.added:
                    data = {'change': 'added', 'path': path}
                    await client.publish(['new'], data=data)
                case Change.modified:
                    data = {'change': 'modified', 'path': path}
                    await client.publish([name], data=data)
                case Change.deleted:
                    data = {'change': 'deleted', 'path': path}
                    await client.publish([name], data=data)

@app.on_event("startup")
async def startup_event():
    client = PubSubClient(on_connect=[on_connect])
    client.start_client(f'ws://{broker}/pubsub')
    await client.wait_until_ready() # wait before publishing

    asyncio.create_task(main(client))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--broker', default='localhost:8000')
    parser.add_argument('-l', '--listen', default='localhost:8001')
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = parser.parse_args()

    # Configuration is global
    broker = args.broker
    name = args.name
    root = Path(args.root)

    # Run
    host, port = args.listen.split(':')
    port = int(port)
    uvicorn.run(app, host=host, port=port)
