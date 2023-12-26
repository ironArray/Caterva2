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
from fastapi import FastAPI, responses
import httpx
import uvicorn

# Project
import models
import utils


logger = logging.getLogger('sub')

# Configuration
broker = None
nworkers = 100

# State
cache = None
clients = {}       # topic: <PubSubClient>
database = None    # <Database> instance
downloads = set()  # Downloads in progress

queue = asyncio.Queue()

async def download_chunk(path, schunk, nchunk):
    root, name = path.split('/', 1)
    host = database.roots[root].http

    url = f'http://{host}/api/download/{name}'
    params = {'nchunk': nchunk}

    client = httpx.AsyncClient()
    async with client.stream('GET', url, params=params) as resp:
        buffer = []
        async for chunk in resp.aiter_bytes():
            buffer.append(chunk)
        chunk = b''.join(buffer)
        schunk.update_chunk(nchunk, chunk)

async def worker(queue):
    while True:
        path, nchunk, abspath = await queue.get()
        with utils.log_exception(logger, 'Download failed'):
            abspath.parent.mkdir(exist_ok=True, parents=True)

            suffix = abspath.suffix
            if suffix == '.b2nd':
                array = blosc2.open(abspath)
                await download_chunk(path, array.schunk, nchunk)
            elif suffix in {'.b2frame', '.b2'}:
                schunk = blosc2.open(abspath)
                await download_chunk(path, schunk, nchunk)
            else:
                raise NotImplementedError()

        queue.task_done()
        downloads.remove((path, nchunk))


async def new_root(data, topic):
    logger.info(f'NEW root {topic} {data=}')
    root = models.Root(**data)
    database.roots[root.name] = root
    database.save()

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')


#
# Internal API
#

def follow(name: str):
    root = database.roots.get(name)
    if root is None:
        errors = {}
        errors[name] = 'This dataset does not exist in the network'
        return errors

    if not root.subscribed:
        root.subscribed = True
        database.save()

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
                utils.init_b2nd(metadata, abspath)
            elif suffix == '.b2frame':
                metadata = models.SChunk(**metadata)
                utils.init_b2frame(metadata, abspath)
            else:
                abspath = rootdir / f'{relpath}.b2'
                metadata = models.SChunk(**metadata)
                utils.init_b2frame(metadata, abspath)

    # Subscribe to changes in the dataset
    if name not in clients:
        client = utils.start_client(f'ws://{broker}/pubsub')
        client.subscribe(name, updated_dataset)
        clients[name] = client

def parse_slice(string):
    obj = []
    for segment in string.split(','):
        segment = [int(x) if x else None for x in segment.split(':')]
        segment = slice(*segment)
        obj.append(segment)

    return obj

def lookup_path(path):
    path = pathlib.Path(path)
    if path.suffix not in {'.b2frame', '.b2nd'}:
        path = f'{path}.b2'

    return utils.get_abspath(cache, path)


#
# HTTP API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize roots from the broker
    try:
        data = utils.get(f'http://{broker}/api/roots')
    except httpx.ConnectError:
        logger.warning('Broker not available')
        client = None
    else:
        changed = False
        # Deleted
        for name, root in database.roots.items():
            if name not in data:
                if root.subscribed:
                    pass # TODO mark the root as stale
                else:
                    del database.roots[name]
                    changed = True

        # New or updadted
        for name, data in data.items():
            root = models.Root(**data)
            if name not in database.roots:
                database.roots[root.name] = root
                changed = True
            elif database.roots[root.name].http != root.http:
                database.roots[root.name].http = root.http
                changed = True

        if changed:
            database.save()


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
    if client is not None:
        await utils.disconnect_client(client)

        # Cancel worker tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(lifespan=lifespan)

@app.get('/api/roots')
async def get_roots():
    return database.roots

def get_root(name):
    root = database.roots.get(name)
    if root is None:
        utils.raise_not_found(f'{name} not known by the broker')

    return root

@app.post('/api/subscribe/{name}')
async def post_subscribe(name: str):
    get_root(name)
    return follow(name)

@app.get('/api/list/{name}')
async def get_list(name: str):
    root = get_root(name)

    rootdir = cache / root.name
    if not rootdir.exists():
        utils.raise_not_found(f'Not subscribed to {name}')

    return [
        relpath.with_suffix('') if relpath.suffix == '.b2' else relpath
        for path, relpath in utils.walk_files(rootdir)
    ]

@app.get('/api/url/{name}')
async def get_url(name: str):
    return get_root(name).http

@app.get('/api/info/{path:path}')
async def get_info(path: str, slice: str = None):
    abspath = lookup_path(path)

    if slice is None:
        return utils.read_metadata(abspath)

    # Read metadata
    slice_obj = parse_slice(slice)
    array, schunk = utils.open_b2(abspath)
    if array is not None:
        array = array.__getitem__(*slice_obj)
        array = blosc2.asarray(array)
        metadata = utils.read_metadata(array)
    else:
        schunk = schunk.__getitem__(*slice_obj)
        schunk = blosc2.asarray(schunk)
        metadata = utils.read_metadata(schunk)

    return metadata


@app.get('/api/download/{path:path}')
async def get_download(path: str, nchunk: int, slice: str = None):
    abspath = lookup_path(path)

    # Build the list of chunks we need to download from the publisher
    array, schunk = utils.open_b2(abspath)
    if slice is None:
        nchunks = [nchunk]
    else:
        slice_obj = parse_slice(slice)
        nchunks = utils.get_nchunks_from_slice(array or schunk, slice_obj)

    # Fetch the chunks
    for n in nchunks:
        if not utils.chunk_is_available(schunk, n):
            key = (path, n)
            if key not in downloads:
                downloads.add(key)
                queue.put_nowait((path, n, abspath))

            # Wait until the chunk is available
            while True: # TODO timeout
                await asyncio.sleep(1)
                array, schunk = utils.open_b2(abspath)
                if utils.chunk_is_available(schunk, n):
                    break

    # With slice
    if slice is not None:
        if array is not None:
            array = array.__getitem__(*slice_obj)
            array = blosc2.asarray(array)
            schunk = array.schunk
        else:
            schunk = schunk.__getitem__(*slice_obj)
            schunk = blosc2.asarray(schunk)

    # Stream response
    chunk = schunk.get_chunk(nchunk)
    downloader = utils.iterchunk(chunk)
    return responses.StreamingResponse(downloader)


#
# Command line interface
#

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Init cache
    var = pathlib.Path('var/sub').resolve()
    cache = var / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Init database
    model = models.Subscriber(roots={})
    database = utils.Database(var / 'db.json', model)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
