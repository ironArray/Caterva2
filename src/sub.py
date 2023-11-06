###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import logging
from pathlib import Path

# Requirements
import asyncio
import blosc2
from fastapi import FastAPI
import httpx
import numpy as np
import uvicorn

# Project
import models
import utils


logger = logging.getLogger('sub')

# Configuration
broker = None
cache = None
nworkers = 100

# State
publishers = {} # name: <Publisher>
datasets = None # name/path: {}
subscribed = {} # name/path: <PubSubClient>
queue = asyncio.Queue()


async def worker(queue):
    while True:
        path = await queue.get()

        try:
            array = blosc2.open(str(cache / path))
            src, path = path.split('/', 1)
            host = publishers[src].http
            print('WORKER', host, path)
            with httpx.stream('GET', f'http://{host}/api/{path}/download') as resp:
                i = 0
                for chunk in resp.iter_bytes():
                    print(i)
                    print('CHUNK', type(chunk), len(chunk), chunk[:10])
                    array.append_data(chunk)
                    i += 1
        except Exception:
            logger.exception('Download failed')

        queue.task_done()


async def new_dataset(data, topic):
    logger.info(f'NEW dataset {topic} {data=}')
    datasets.update(data)

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')


#
# API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize datasets from the broker
    global datasets
    datasets = utils.get(f'http://{broker}/api/datasets')
    # Follow the @new channel to know when a new dataset is added
    client = utils.start_client(f'ws://{broker}/pubsub')
    client.subscribe('@new', new_dataset)

    # Start workers
    tasks = []
    for i in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    yield

    # Disconnect from worker
    await utils.disconnect_client(client)

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(lifespan=lifespan)

@app.get('/api/list')
async def get_list(all: bool = False):
    keys = datasets.keys() if all else subscribed.keys()
    return list(keys)

@app.post('/api/follow')
async def post_follow(add: list[str]):
    for name in add:
        if name not in subscribed:
            # Initialize
            dataset = datasets[name]
            metadata = models.Metadata(**dataset)
            dtype = getattr(np, metadata.dtype)
            array = blosc2.uninit(metadata.shape, dtype)
            # Save to disk
            urlpath = cache / name
            urlpath.parent.mkdir(exist_ok=True)
            blosc2.save_array(array, str(urlpath))
            # Subscribe
            client = utils.start_client(f'ws://{broker}/pubsub')
            client.subscribe(name, updated_dataset)
            subscribed[name] = client
            # Get port
            src = name.split('/', 1)[0]
            publishers[src] = utils.get(f'http://{broker}/api/publishers/{src}',
                                        model=models.Publisher)

@app.post('/api/unfollow')
async def post_unfollow(delete: list[str]):
    for name in delete:
        client = subscribed.pop(name, None)
        await utils.disconnect_client(client)

@app.post("/api/download")
async def post_download(datasets: list[str]):
    for dataset in datasets:
        queue.put_nowait(dataset)


if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Create cache directory
    cache = Path('cache').resolve()
    cache.mkdir(exist_ok=True)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
