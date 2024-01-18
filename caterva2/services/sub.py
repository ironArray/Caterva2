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
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils

# Logging
logger = logging.getLogger('sub')

# Configuration
broker = None

# State
cache = None
clients = {}       # topic: <PubSubClient>
database = None    # <Database> instance
host = None
locks = {}
port = None


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
        srv_utils.init_b2(abspath, metadata)


#
# Internal API
#

def follow(name: str):
    root = database.roots.get(name)
    if root is None:
        errors = {name: 'This dataset does not exist in the network'}
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
        srv_utils.init_b2(abspath, metadata)

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
async def get_roots() -> dict:
    """
    Get a dict of roots, with root names as keys and properties as values.

    Returns
    -------
    dict
        The dict of roots.
    """
    return database.roots


def get_root(name):
    root = database.roots.get(name)
    if root is None:
        srv_utils.raise_not_found(f'{name} not known by the broker')

    return root


@app.post('/api/subscribe/{name}')
async def post_subscribe(name: str):
    """
    Subscribe to a root.

    Parameters
    ----------
    name : str
        The name of the root.

    Returns
    -------
    str
        'Ok' if successful.
    """
    get_root(name)  # Not Found
    follow(name)
    return 'Ok'


@app.get('/api/list/{name}')
async def get_list(name: str):
    """
    List the datasets in a root.

    Parameters
    ----------
    name : str
        The name of the root.

    Returns
    -------
    list
        The list of datasets in the root.
    """
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
    """
    Get the URLs to access a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.

    Returns
    -------
    list
        The URLs to access the dataset.
    """
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
    """
    Get the metadata of a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.

    Returns
    -------
    dict
        The metadata of the dataset.
    """
    abspath = lookup_path(path)
    return srv_utils.read_metadata(abspath)


async def partial_download(abspath, path, slice_):
    """
    Download the necessary chunks of a dataset.

    Parameters
    ----------
    abspath : pathlib.Path
        The absolute path to the dataset.
    path : str
        The path to the dataset.
    slice_ : slice, tuple of slices
        The slice to fetch.

    Returns
    -------
    None
        When finished, the dataset is available in cache.
    """
    # Build the list of chunks we need to download from the publisher
    array, schunk = srv_utils.open_b2(abspath)
    if slice_:
        if not array:
            if isinstance(slice_[0], slice):
                # TODO: support schunk.nitems to avoid computations like these
                nitems = schunk.nbytes // schunk.typesize
                start, stop, _ = slice_[0].indices(nitems)
            else:
                start, stop = slice_[0], slice_[0] + 1
            # get_slice_nchunks() does not support slices for schunks yet
            # TODO: support slices for schunks in python-blosc2
            nchunks = blosc2.get_slice_nchunks(schunk, (start, stop))
        else:
            nchunks = blosc2.get_slice_nchunks(array, slice_)
    else:
        nchunks = range(schunk.nchunks)

    # Fetch the chunks
    lock = locks.setdefault(path, asyncio.Lock())
    async with lock:
        for n in nchunks:
            if not srv_utils.chunk_is_available(schunk, n):
                await download_chunk(path, schunk, n)


@app.get('/api/download/{path:path}')
async def download_data(path: str, slice_: str = None, download: bool = False):
    """
    Download or fetch a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.
    slice_ : str
        The slice to fetch.
    download : bool
        True if the intent is to download the dataset from 'files/'.  If False, the data is
        returned as a StreamingResponse (it is 'fetched').

    Returns
    -------
    url or StreamingResponse
        The url of the file in 'files/' if `download` is True, or a StreamingResponse
        with the data if download is False.

    """
    abspath = lookup_path(path)
    slice_ = api_utils.parse_slice(slice_)

    # Download and update the necessary chunks of the schunk in cache
    await partial_download(abspath, path, slice_)

    # Interesting data has been downloaded, let's use it
    if download:
        # The complete file is already in the static files/ dir, so return the url.
        # We don't currently decompress data before downloading, so let's add the extension
        # in the url, if it is missing.
        if abspath.suffix == '.b2':
            path = f'{path}.b2'
        return f'http://{host}:{port}/files/{path}'

    array, schunk = srv_utils.open_b2(abspath)
    if slice_:
        if array:
            array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if isinstance(slice_, int):
                # TODO: make SChunk support integer as slice
                slice_ = slice(slice_, slice_ + 1)
            schunk = schunk[slice_]

    # Pickle and stream response of the NumPy array
    data = array if array is not None else schunk
    if not slice_:
        data = data[:]
    data = pickle.dumps(data, protocol=-1)
    # TODO: compress data is not working. HTTPX does this automatically?
    # data = zlib.compress(data)
    downloader = srv_utils.iterchunk(data)
    return responses.StreamingResponse(downloader)

#
# Command line interface
#

def main():
    conf = srv_utils.get_conf('subscriber', allow_id=True)
    parser = utils.get_parser(broker=conf.get('broker.http', 'localhost:8000'),
                              http=conf.get('.http', 'localhost:8002'),
                              loglevel=conf.get('.loglevel', 'warning'),
                              id=conf.id)
    _stdir = '_caterva2/sub' + (f'.{conf.id}' if conf.id else '')
    parser.add_argument('--statedir',
                        default=conf.get('.statedir', _stdir),
                        type=pathlib.Path)
    args = utils.run_parser(parser)

    # Global configuration
    global broker
    broker = args.broker

    # Init cache
    global cache
    statedir = args.statedir.resolve()
    cache = statedir / 'cache'
    cache.mkdir(exist_ok=True, parents=True)
    app.mount("/files", StaticFiles(directory=cache), name="files")

    # Init database
    global database
    model = models.Subscriber(roots={}, etags={})
    database = srv_utils.Database(statedir / 'db.json', model)

    # Run
    global host, port
    host, port = args.http
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
