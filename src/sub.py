###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import logging
import pathlib

# Requirements
import asyncio
import blosc2
from fastapi import FastAPI
import httpx
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
roots = {}       # name: <Root>
database = None  # <Database> instance
clients = {}     # topic: <PubSubClient>

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
        path = await queue.get()
        with utils.log_exception(logger, 'Download failed'):
            urlpath = cache / path
            urlpath.parent.mkdir(exist_ok=True, parents=True)

            root, relpath = path.split('/', 1)
            host = roots[root].http

            suffix = urlpath.suffix
            if suffix == '.b2nd':
                array = blosc2.open(str(urlpath))
                for nchunk in range(array.schunk.nchunks):
                    download_chunk(host, relpath, nchunk, array.schunk)
            elif suffix == '.b2frame':
                schunk = blosc2.open(str(urlpath))
                for nchunk in range(schunk.nchunks):
                    download_chunk(host, relpath, nchunk, schunk)
            else:
                with urlpath.open('wb') as file:
                    with httpx.stream('GET', f'http://{host}/api/download/{relpath}') as resp:
                        for chunk in resp.iter_bytes():
                            file.write(chunk)

        queue.task_done()


async def new_root(data, topic):
    logger.info(f'NEW root {topic} {data=}')
    root = models.Root(**data)
    roots[root.name] = root

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')


#
# Internal API
#

def follow(name: str):
    root = roots.get(name)
    if root is None:
        errors = {}
        errors[name] = 'This dataset does not exist in the network'
        return errors

    # Create root directory in the cache
    rootdir = cache / name
    if not rootdir.exists():
        rootdir.mkdir(exist_ok=True)

    # Initialize the datasets in the cache
    data = utils.get(f'http://{root.http}/api/list')
    for relpath in data:
        metadata = utils.get(f'http://{root.http}/api/info/{relpath}')
        abspath = rootdir / relpath
        if not abspath.exists():
            suffix = abspath.suffix
            if suffix == '.b2nd':
                metadata = models.Metadata(**metadata)
                utils.init_b2nd(abspath, metadata)
            elif suffix == '.b2frame':
                metadata = models.SChunk(**metadata)
                utils.init_b2frame(abspath, metadata)
            else:
                metadata = models.File(**metadata)
                abspath.touch()

    # Subscribe to changes in the dataset
    if name not in clients:
        client = utils.start_client(f'ws://{broker}/pubsub')
        client.subscribe(name, updated_dataset)
        clients[name] = client


#
# HTTP API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize roots from the broker
    data = utils.get(f'http://{broker}/api/roots')
    for name, data in data.items():
        root = models.Root(**data)
        roots[root.name] = root

    # Follow the @new channel to know when a new root is added
    client = utils.start_client(f'ws://{broker}/pubsub')
    client.subscribe('@new', new_root)

    # Resume following
    for path in cache.iterdir():
        if path.is_dir():
            follow(path.name)

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

@app.get('/api/roots')
async def get_roots():
    return sorted(roots)

def get_root(name):
    root = roots.get(name)
    if root is None:
        utils.raise_not_found(f'{name} not known by the broker')

    return root

@app.get('/api/list/{name}')
async def get_list(name: str):
    root = get_root(name)

    rootdir = cache / root.name
    if not rootdir.exists():
        utils.raise_not_found(f'Not subscribed to {name}')

    return [relpath for path, relpath in utils.walk_files(rootdir)]

@app.post('/api/subscribe/{name}')
async def post_subscribe(name: str):
    get_root(name)
    return follow(name)

@app.get('/api/info/{path:path}')
async def get_info(path: str):
    try:
        return utils.read_metadata(cache / path)
    except FileNotFoundError:
        utils.raise_not_found()


@app.get('/api/get/{path:path}')
async def get_get(path: str):
    queue.put_nowait(path)


#
# Command line interface
#

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Init cache and database
    var = pathlib.Path('var/sub').resolve()
    database = utils.Database(var / 'db.json', default={})
    cache = var / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
