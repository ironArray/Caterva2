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
from fastapi.staticfiles import StaticFiles
import httpx
import uvicorn

# Project
from caterva2 import utils, api_utils, b2_utils, models
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
        b2_utils.init_b2nd(metadata, abspath)
    elif suffix == '.b2frame':
        metadata = models.SChunk(**metadata)
        b2_utils.init_b2frame(metadata, abspath)
    else:
        abspath = pathlib.Path(f'{abspath}.b2')
        metadata = models.SChunk(**metadata)
        b2_utils.init_b2frame(metadata, abspath)


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


async def partial_download(abspath, path, slice_):
    # Build the list of chunks we need to download from the publisher
    array, schunk = b2_utils.open_b2(abspath)
    if slice_:
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
    else:
        nchunks = range(schunk.nchunks)

    # Fetch the chunks
    lock = locks.setdefault(path, asyncio.Lock())
    async with lock:
        for n in nchunks:
            if not b2_utils.chunk_is_available(schunk, n):
                await download_chunk(path, schunk, n)


@app.get('/api/download/{path:path}')
async def download_data(path: str, slice_: str = None, download: bool = False):
    abspath = lookup_path(path)
    suffix = abspath.suffix

    # Download and update the schunk in cache
    await partial_download(abspath, path, slice_)

    download_path = None
    if download:
        # Let's store the data in the downloads directory
        download_path = cache / pathlib.Path('downloads') / pathlib.Path(path)
        if slice_:
            download_path = download_path.with_suffix('')
            download_path = pathlib.Path(f'{download_path}[{slice_}]{suffix}')
        else:
            # By here, we already have the complete schunk in cache
            download_path = abspath
        download_path.parent.mkdir(parents=True, exist_ok=True)

    # Interesting data has been downloaded, let's use it
    array, schunk = b2_utils.open_b2(abspath)
    slice_ = api_utils.parse_slice(slice_)
    if slice_:
        if array:
            if download_path:
                # We want to save the slice to a file
                array.slice(slice_, urlpath=download_path, mode="w", contiguous=True,
                            cparams=schunk.cparams)
            else:
                array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if isinstance(slice_, int):
                # TODO: make SChunk support integer as slice
                slice_ = slice(slice_, slice_ + 1)
            if download_path:
                # TODO: fix the upstream bug in python-blosc2 that prevents this from working
                #  when not specifying chunksize (uses `data.size` instead of `len(data)`).
                blosc2.SChunk(data=schunk[slice_], mode="w", urlpath=download_path,
                              chunksize=schunk.chunksize,
                              cparams=schunk.cparams)
            else:
                schunk = schunk[slice_]

    if download:
        if suffix == '.b2':
            # Decompress before delivering
            # TODO: support context manager in blosc2.open()
            schunk = blosc2.open(download_path, 'wb')
            data = schunk[:]
            downloader = b2_utils.iterchunk(data)
            return responses.StreamingResponse(downloader)
        return responses.FileResponse(download_path)

    # Pickle and stream response of the NumPy array
    data = array if array is not None else schunk
    if not slice_:
        data = data[:]
    data = pickle.dumps(data, protocol=-1)
    # TODO: compress data is not working. HTTPX does this automatically?
    # data = zlib.compress(data)
    downloader = b2_utils.iterchunk(data)
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
    app.mount("/files", StaticFiles(directory=cache), name="files")

    # Init database
    model = models.Subscriber(roots={}, etags={})
    database = srv_utils.Database(statedir / 'db.json', model)

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
