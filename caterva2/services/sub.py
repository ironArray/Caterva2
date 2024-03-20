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

# FastAPI
from fastapi import FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import fastapi

# Requirements
import blosc2
import furl
import numpy as np
import httpx
import uvicorn

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils
from .plugins import tomography


BASE_DIR = pathlib.Path(__file__).resolve().parent

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


def make_url(request, name, query=None, **path_params):
    url = request.url_for(name, **path_params)
    # url.include_query_params() does not seem to work for list values
    if query:
        url = furl.furl(url).set(query)
    return str(url)


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

    # Get list of datasets
    try:
        data = api_utils.get(f'http://{root.http}/api/list')
    except httpx.ConnectError:
        return

    # Initialize the datasets in the cache
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
        d = list(database.roots.items())
        for name, root in d:
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
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"))
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
    abspath = srv_utils.cache_lookup(cache, path)
    return srv_utils.read_metadata(abspath)


async def partial_download(abspath, path, slice_=None):
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


@app.get('/api/fetch/{path:path}')
async def fetch_data(path: str, slice_: str = None, prefer_schunk: bool = False):
    """
    Fetch a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.
    slice_ : str
        The slice to fetch.
    prefer_schunk : bool
        True if the client accepts Blosc2 schunks.

    Returns
    -------
    StreamingResponse
        The (slice of) dataset as a NumPy array or a Blosc2 schunk.
    """

    abspath = srv_utils.cache_lookup(cache, path)
    slice_ = api_utils.parse_slice(slice_)

    # Download and update the necessary chunks of the schunk in cache
    await partial_download(abspath, path, slice_)

    array, schunk = srv_utils.open_b2(abspath)
    typesize = schunk.typesize
    if slice_:
        if array is not None:
            array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if isinstance(slice_, int):
                # TODO: make SChunk support integer as slice
                slice_ = slice(slice_, slice_ + 1)
            schunk = schunk[slice_]

    # Serialization can be done either as:
    # * a serialized NDArray
    # * a compressed SChunk (bytes, via blosc2.compress2)
    # * a pickled NumPy array (specially scalars and 0-dim arrays)
    data = array if array is not None else schunk
    if not slice_:
        # data is still a SChunk, so we need to get either a NumPy array, or a bytes object
        data = data[()] if array is not None else data[:]

    # Optimizations for small data. If too small, we pickle it instead of compressing it.
    # Some measurements have been done and it looks like this has no effect on performance.
    # TODO: do more measurements and decide whether to keep this or not.
    small_data = 128  # length in bytes
    if isinstance(array, np.ndarray):
        if array.size == 0:
            # NumPy scalars or 0-dim are not supported by blosc2 yet, so we need to use pickle better
            prefer_schunk = False
        elif array.size * array.itemsize < small_data:
            prefer_schunk = False
    if isinstance(data, bytes) and len(data) < small_data:
        prefer_schunk = False

    if prefer_schunk:
        if isinstance(data, np.ndarray):
            data = blosc2.asarray(data)
            data = data.to_cframe()
        else:
            # A bytes object can still be compressed
            data = blosc2.compress2(data, typesize=typesize)
        downloader = srv_utils.iterchunk(data)
    else:
        data = pickle.dumps(data, protocol=-1)
        downloader = iter((data,))
    return responses.StreamingResponse(downloader)


@app.get('/api/download/{path:path}')
async def download_data(path: str):
    """
    Download a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.

    Returns
    -------
    url
        The url of the file in 'files/' to be downloaded later on.
    """

    abspath = srv_utils.cache_lookup(cache, path)

    # Download and update the necessary chunks of the schunk in cache
    await partial_download(abspath, path)

    # The complete file is already in the static files/ dir, so return the url.
    # We don't currently decompress data before downloading, so let's add the extension
    # in the url, if it is missing.
    if abspath.suffix == '.b2':
        path = f'{path}.b2'
    return f'http://{host}:{port}/files/{path}'


#
# HTML interface
#

def home(request, roots=None, search=None, context=None):
    context = context or {}

    context['roots_url'] = make_url(request, 'htmx_root_list', {'roots': roots})
    if roots:
        paths_url = make_url(request, 'htmx_path_list', {'roots': roots, 'search': search})
        context['paths_url'] = paths_url

    return templates.TemplateResponse(request, "home.html", context)


@app.get("/", response_class=HTMLResponse)
async def html_home(
    request: Request,
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = '',
):
    return home(request, roots, search)


@app.get("/htmx/root-list/")
async def htmx_root_list(
    request: Request,
    # Headers
    roots: list[str] = fastapi.Query([]),
    hx_current_url: srv_utils.HeaderType = None,
):

    context = {
        "roots": sorted(database.roots.values(), key=lambda x: x.name),
        "checked": roots,
    }
    return templates.TemplateResponse(request, "root_list.html", context)


@app.get("/htmx/path-list/", response_class=HTMLResponse)
async def htmx_path_list(
    request: Request,
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = '',
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
):

    paths = []
    for root in roots:
        if not get_root(root).subscribed:
            follow(root)

        rootdir = cache / root
        for path, relpath in utils.walk_files(rootdir):
            if relpath.suffix == '.b2':
                relpath = relpath.with_suffix('')
            if search in str(relpath):
                paths.append(f'{root}/{relpath}')

    # Render template
    search_url = make_url(request, 'htmx_path_list', {'roots': roots})
    context = {
        "paths": paths,
        "search_text": search,
        "search_url": search_url,
    }
    response = templates.TemplateResponse(request, "path_list.html", context)

    # Push URL
    args = {'roots': roots}
    if search:
        args['search'] = search
    push_url = furl.furl(hx_current_url).set(args).url
    response.headers['HX-Push-Url'] = push_url

    return response


@app.get("/roots/{path:path}", response_class=HTMLResponse)
async def html_path_info(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = '',
    # Headers
    hx_request: srv_utils.HeaderType = None,
    hx_current_url: srv_utils.HeaderType = None,
):

    if not hx_request:
        context = {
            "meta_url": make_url(request, 'html_path_info', path=path),
        }
        return home(request, roots, search, context)

    filepath = cache / path
    abspath = srv_utils.cache_lookup(cache, filepath)
    meta = srv_utils.read_metadata(abspath)

    #getattr(meta, 'schunk', meta).vlmeta['contenttype'] = 'tomography'
    contenttype = getattr(meta, 'schunk', meta).vlmeta.get('contenttype')

    plugin = plugins.get(contenttype)
    if plugin:
        display = {
            "name": plugin.name
        }
    else:
        display = None

    context = {"path": path, "meta": meta, "display": display}
    response = templates.TemplateResponse(request, "info.html", context=context)

    current_url = furl.furl(hx_current_url)
    request_url = furl.furl(request.url)
    response.headers['HX-Push-Url'] = request_url.set(current_url.query.params).url
    return response


#
# Command line interface
#

plugins = {}

def main():
    conf = utils.get_conf('subscriber', allow_id=True)
    _stdir = '_caterva2/sub' + (f'.{conf.id}' if conf.id else '')
    parser = utils.get_parser(broker=conf.get('broker.http', 'localhost:8000'),
                              http=conf.get('.http', 'localhost:8002'),
                              loglevel=conf.get('.loglevel', 'warning'),
                              statedir=conf.get('.statedir', _stdir),
                              id=conf.id)
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

    # Register display plugins
    app.mount(f"/plugins/{tomography.name}", tomography.app)
    plugins[tomography.contenttype] = tomography
    tomography.init(cache, partial_download)

    # Run
    global host, port
    host, port = args.http
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
