###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import contextlib
import logging
import pathlib
import pickle

# Requirements
import blosc2
from fastapi import FastAPI, responses
import httpx
import uvicorn

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils


logger = logging.getLogger('sub')

# Configuration
broker = None

# State
cache = None
clients = {}       # topic: <PubSubClient>
database = None    # <Database> instance
locks = {}


async def download_chunk(path, schunk, nchunk):
    root, name = path.split('/', 1)
    host = database.roots[root].http

    url = f'http://{host}/api/download/{name}'
    params = {'nchunk': nchunk}

    client = httpx.AsyncClient()
    async with client.stream('GET', url, params=params, timeout=5) as resp:
        buffer = []
        async for chunk in resp.aiter_bytes():
            buffer.append(chunk)
        chunk = b''.join(buffer)
        schunk.update_chunk(nchunk, chunk)


async def new_root(data, topic):
    logger.info(f'NEW root {topic} {data=}')
    root = models.Root(**data)
    database.roots[root.name] = root
    database.save()


def init_b2(abspath, metadata):
    suffix = abspath.suffix
    if suffix == '.b2nd':
        metadata = models.Metadata(**metadata)
        srv_utils.init_b2nd(metadata, abspath)
    elif suffix == '.b2frame':
        metadata = models.SChunk(**metadata)
        srv_utils.init_b2frame(metadata, abspath)
    else:
        abspath = pathlib.Path(f'{abspath}.b2')
        metadata = models.SChunk(**metadata)
        srv_utils.init_b2frame(metadata, abspath)


async def updated_dataset(data, topic):
    name = topic
    relpath = data['path']

    rootdir = cache / name
    abspath = rootdir / relpath
    metadata = data.get('metadata')
    if metadata is None:
        if abspath.suffix not in {'.b2nd', '.b2frame'}:
            abspath = pathlib.Path(f'{abspath}.b2')
        if abspath.is_file():
            abspath.unlink()
    else:
        init_b2(abspath, metadata)


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
    data = api_utils.get(f'http://{root.http}/api/list')
    for relpath in data:
        # If-None-Match header
        key = f'{name}/{relpath}'
        val = database.etags.get(key)
        headers = None if val is None else {'If-None-Match': val}

        # Call API
        response = httpx.get(f'http://{root.http}/api/info/{relpath}', headers=headers)
        if response.status_code == 304:
            continue

        response.raise_for_status()
        metadata = response.json()

        # Save metadata
        abspath = rootdir / relpath
        init_b2(abspath, metadata)

        # Save etag
        database.etags[key] = response.headers['etag']
        database.save()

    # Subscribe to changes in the dataset
    if name not in clients:
        client = srv_utils.start_client(f'ws://{broker}/pubsub')
        client.subscribe(name, updated_dataset)
        clients[name] = client


def lookup_path(path):
    path = pathlib.Path(path)
    if path.suffix not in {'.b2frame', '.b2nd'}:
        path = f'{path}.b2'

    return srv_utils.get_abspath(cache, path)


#
# HTTP API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize roots from the broker
    try:
        data = api_utils.get(f'http://{broker}/api/roots')
    except httpx.ConnectError:
        logger.warning('Broker not available')
        client = None
    else:
        changed = False
        # Deleted
        for name, root in database.roots.items():
            if name not in data:
                del database.roots[name]
                changed = True

        # New or updated
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
        client = srv_utils.start_client(f'ws://{broker}/pubsub')
        client.subscribe('@new', new_root)

        # Resume following
        for path in cache.iterdir():
            if path.is_dir():
                follow(path.name)

    yield

    # Disconnect from worker
    if client is not None:
        await srv_utils.disconnect_client(client)

app = FastAPI(lifespan=lifespan)

@app.get('/api/roots')
async def get_roots():
    return database.roots

def get_root(name):
    root = database.roots.get(name)
    if root is None:
        srv_utils.raise_not_found(f'{name} not known by the broker')

    return root

@app.post('/api/subscribe/{name}')
async def post_subscribe(name: str):
    get_root(name)  # Not Found
    follow(name)
    return 'Ok'

@app.get('/api/list/{name}')
async def get_list(name: str):
    root = get_root(name)

    rootdir = cache / root.name
    if not rootdir.exists():
        srv_utils.raise_not_found(f'Not subscribed to {name}')

    return [
        relpath.with_suffix('') if relpath.suffix == '.b2' else relpath
        for path, relpath in utils.walk_files(rootdir)
    ]

@app.get('/api/url/{path:path}')
async def get_url(path: str):
    root, *dataset = path.split('/', 1)
    scheme = 'http'
    http = get_root(root).http
    http = f'{scheme}://{http}'
    if dataset:
        dataset = dataset[0]
        return [
            f'{http}/api/info/{dataset}',
            f'{http}/api/download/{dataset}',
        ]

    return [http]

@app.get('/api/info/{path:path}')
async def get_info(path: str):
    abspath = lookup_path(path)
    return srv_utils.read_metadata(abspath)


@app.get('/api/download/{path:path}')
async def get_download(path: str, nchunk: int, slice_: str = None):
    abspath = lookup_path(path)

    chunk = await partial_download(abspath, nchunk, path, slice_)
    # Stream response
    downloader = srv_utils.iterchunk(chunk)
    return responses.StreamingResponse(downloader)


async def partial_download(abspath, nchunk, path, slice_):
    # Build the list of chunks we need to download from the publisher
    array, schunk = srv_utils.open_b2(abspath)
    if slice_ is None:
        nchunks = [nchunk]
    else:
        slice_obj = api_utils.parse_slice(slice_)
        if not array:
            if isinstance(slice_obj[0], slice):
                # TODO: support schunk.nitems to avoid computations like these
                nitems = schunk.nbytes // schunk.typesize
                start, stop, _ = slice_obj[0].indices(nitems)
            else:
                start, stop = slice_obj[0], slice_obj[0] + 1
            # get_slice_nchunks() does not support slices for schunks yet
            # TODO: support slices for schunks in python-blosc2
            nchunks = blosc2.get_slice_nchunks(schunk, (start, stop))
        else:
            nchunks = blosc2.get_slice_nchunks(array, slice_obj)
    # Fetch the chunks
    lock = locks.setdefault(path, asyncio.Lock())
    async with lock:
        for n in nchunks:
            if not srv_utils.chunk_is_available(schunk, n):
                await download_chunk(path, schunk, n)
    chunk = schunk.get_chunk(nchunk)
    return chunk


@app.get('/api/fetch/{path:path}')
async def fetch_data(path: str, slice_: str = None):
    abspath = lookup_path(path)
    metadata = srv_utils.read_metadata(abspath)

    # Create array/schunk in memory
    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = srv_utils.init_b2nd(metadata, urlpath=None)
        schunk = array.schunk
    elif suffix == '.b2frame':
        schunk = srv_utils.init_b2frame(metadata, urlpath=None)
        array = None
    else:
        schunk = srv_utils.init_b2frame(metadata, urlpath=None)
        array = None

    # Download and update schunk in-memory
    for nchunk in range(schunk.nchunks):
        chunk = await partial_download(abspath, nchunk, path, slice_)
        schunk.update_chunk(nchunk, chunk)

    if slice_:
        # Additional massage for slices
        slice_ = api_utils.parse_slice(slice_)
        if array:
            array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if isinstance(slice_, int):
                slice_ = slice(slice_, slice_ + 1)
            # TODO: make SChunk support integer as slice
            schunk = schunk[slice_]

    data = array if array is not None else schunk

    # Pickle and stream response
    data = pickle.dumps(data, protocol=-1)
    # TODO: compress data is not working. HTTPX does this automatically?
    # data = zlib.compress(data)
    downloader = srv_utils.iterchunk(data)
    return responses.StreamingResponse(downloader)

#
# Command line interface
#

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    parser.add_argument('--statedir', default='_caterva2/sub', type=pathlib.Path)
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Init cache
    statedir = args.statedir.resolve()
    cache = statedir / 'cache'
    cache.mkdir(exist_ok=True, parents=True)

    # Init database
    model = models.Subscriber(roots={}, etags={})
    database = srv_utils.Database(statedir / 'db.json', model)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
