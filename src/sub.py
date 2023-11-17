###############################################################################
# PubSub for Blosc2 - Access Blosc2 (and others) datasets via a Pub/Sub pattern
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import json
import logging
import pathlib

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

# World view, propagated through the broker, with information from the publihsers
publishers = {} # name: <Publisher>
datasets = None # name/path: {}

subscribed = {} # name/path: <PubSubClient>

queue = asyncio.Queue()

def download_chunk(host, name, nchunk, schunk):
    with httpx.stream('GET', f'http://{host}/api/download/{name}?{nchunk=}') as resp:
        buffer = []
        for chunk in resp.iter_bytes():
            buffer.append(chunk)
        chunk = b''.join(buffer)
        schunk.insert_chunk(nchunk, chunk)

async def worker(queue):
    while True:
        name = await queue.get()
        with utils.log_exception(logger, 'Download failed'):
            urlpath = cache / name
            urlpath.parent.mkdir(exist_ok=True, parents=True)

            dataset = datasets[name]
            src, name = name.split('/', 1)
            host = publishers[src].http

            suffix = urlpath.suffix
            if suffix == '.b2nd':
                metadata = models.Metadata(**dataset)
                nchunks = metadata.schunk.nchunks

                array = blosc2.open(str(urlpath))
                schunk = array.schunk
                for nchunk in range(nchunks):
                    download_chunk(host, name, nchunk, schunk)
            elif suffix == '.b2frame':
                metadata = models.SChunk(**dataset)
                nchunks = metadata.nchunks

                schunk = blosc2.open(str(urlpath))
                for nchunk in range(nchunks):
                    download_chunk(host, name, nchunk, schunk)
            else:
                with urlpath.open('wb') as file:
                    with httpx.stream('GET', f'http://{host}/api/download/{name}') as resp:
                        for chunk in resp.iter_bytes():
                            file.write(chunk)

        queue.task_done()


async def new_dataset(data, topic):
    logger.info(f'NEW dataset {topic} {data=}')
    datasets.update(data)

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')

#
# The "database" is used to persist the subscriber state, so it survives restarts
#

database = None

class Database:

    def __init__(self, path):
        self.path = path

        if path.exists():
            self.load()
        else:
            self.init()

    def init(self):
        self.data = {
            'following': [], # List of datasets we are subscribed to
        }
        self.save()

    def load(self):
        with self.path.open() as file:
            self.data = json.load(file)

    def save(self):
        with self.path.open('w') as file:
            json.dump(self.data, file)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


#
# Internal API
#

def follow(datasets_list: list[str]):
    errors = {}

    for name in datasets_list:
        if name not in datasets:
            errors[name] = 'This dataset does not exist in the network'
            continue

        # Initialize the dataset in the filesystem (cache)
        urlpath = cache / name
        if not urlpath.exists():
            suffix = urlpath.suffix
            dataset = datasets[name]
            if suffix == '.b2nd':
                metadata = models.Metadata(**dataset)

                dtype = getattr(np, metadata.dtype)
                urlpath.parent.mkdir(exist_ok=True, parents=True)
                blosc2.uninit(metadata.shape, dtype, urlpath=str(urlpath))
            elif suffix == '.b2frame':
                metadata = models.SChunk(**dataset)
                urlpath.parent.mkdir(exist_ok=True, parents=True)
                utils.init_b2frame(urlpath, metadata)

        # Subscribe to changes in the dataset
        if name not in subscribed:
            client = utils.start_client(f'ws://{broker}/pubsub')
            client.subscribe(name, updated_dataset)
            subscribed[name] = client

        following = database['following']
        if name not in following:
            following.append(name)
            database.save()

        # Get the publisher hostname and port for later downloading
        src = name.split('/', 1)[0]
        if src not in publishers:
            url = f'http://{broker}/api/publishers/{src}'
            publishers[src] = utils.get(url, model=models.Publisher)

    return errors


#
# HTTP API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize datasets from the broker
    global datasets
    datasets = utils.get(f'http://{broker}/api/datasets')
    # Follow the @new channel to know when a new dataset is added
    client = utils.start_client(f'ws://{broker}/pubsub')
    client.subscribe('@new', new_dataset)

    # Resume following
    follow(database['following'])

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
async def get_list():
    return list(datasets.keys())

@app.post('/api/follow')
async def post_follow(add: list[str]):
    return follow(add)

@app.get('/api/following')
async def get_following():
    return database['following']

@app.post('/api/unfollow')
async def post_unfollow(delete: list[str]):
    for name in delete:
        client = subscribed.pop(name, None)
        await utils.disconnect_client(client)

@app.post("/api/download")
async def post_download(datasets: list[str]):
    for dataset in datasets:
        queue.put_nowait(dataset)


#
# Command line interface
#

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Create cache directory
    cache = pathlib.Path('cache').resolve()
    cache.mkdir(exist_ok=True)

    # Open or create database file
    database = Database(cache / 'db.json')

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
