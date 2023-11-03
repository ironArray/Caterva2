###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import contextlib
from pathlib import Path

# Requirements
from fastapi import FastAPI
from fastapi import responses
from fastapi_websocket_pubsub import PubSubClient
import uvicorn
from watchfiles import Change, awatch

# Project
import utils


# Configuration
broker = None
name = None
root = None
nworkers = 1

# State
client = None


async def worker(queue):
    while True:
        path, change = await queue.get()
        subpath = Path(path).relative_to(root)
        dataset = f'{name}/{subpath}'

        if change == Change.added:
            metadata = utils.read_metadata(str(path))
            metadata = metadata.model_dump()
            topic = '@new'
            data = {dataset: metadata}
        else:
            topic = dataset
            data = {'change': change.name}

        await client.publish([topic], data=data)
        queue.task_done()


async def main(client):
    queue = asyncio.Queue()

    # Start workers
    tasks = []
    for i in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    # Notify the broker about available datasets
    for path in root.iterdir():
        queue.put_nowait((path, Change.added))

    # Watch directory for changes
    async for changes in awatch(root):
        for change, path in changes:
            queue.put_nowait((path, change))

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    print('MAIN EXIT')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    client.start_client(f'ws://{broker}/pubsub')
    #async with asyncio.timeout(5):
    await client.wait_until_ready() # wait before publishing

    asyncio.create_task(main(client))
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/api/list")
async def app_list():
    return (x.name for x in root.iterdir())

@app.get("/api/{name}/metadata")
async def app_metadata(name: str):
    filepath = root / name
    stat = filepath.stat()
    keys = ['mtime', 'size']
    return {key: getattr(stat, f'st_{key}') for key in keys}

@app.get("/api/{name}/download")
async def app_download(name: str, response_class=responses.PlainTextResponse):
    filepath = root / name
    with filepath.open() as file:
        return file.read()

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8001')
    parser.add_argument('name')
    parser.add_argument('root', default='data')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker
    client = PubSubClient()
    name = args.name
    root = Path(args.root).resolve()

    # Register
    host, port = args.http
    data = {'name': name, 'http': f'{host}:{port}'}
    utils.post(f'http://{broker}/api/publishers', json=data)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
