###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import contextlib
import itertools
import logging
import os
import pathlib
import pickle
import string
import typing
from collections.abc import Awaitable, Callable

# FastAPI
from fastapi import Depends, FastAPI, Form, Request, responses
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import fastapi
import numexpr as ne

# Requirements
import blosc2
import furl
import numpy as np
import httpx
import uvicorn

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils
from caterva2.services.subscriber import db, schemas, users
from .plugins import tomography


BASE_DIR = pathlib.Path(__file__).resolve().parent

# Logging
logger = logging.getLogger('sub')

# Configuration
broker = None

# State
statedir = None
cache = None
scratch = None
clients = {}       # topic: <PubSubClient>
database = None    # <Database> instance
locks = {}
urlbase = None


def make_url(request, name, query=None, **path_params):
    url = request.app.url_path_for(name, **path_params)
    url = str(url)  # <starlette.datastructures.URLPath>

    if query:
        url = furl.furl(url).set(query).url

    return url


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

def user_auth_enabled():
    return bool(os.environ.get(users.SECRET_TOKEN_ENVVAR))


current_active_user = (users.current_active_user if user_auth_enabled()
                       else (lambda: None))
"""Depend on this if the route needs an authenticated user (if enabled)."""

optional_user = (users.fastapi_users.current_user(
                     optional=True,
                     verified=False)  # TODO: set when verification works
                 if user_auth_enabled()
                 else (lambda: None))
"""Depend on this if the route may do something with no authentication."""


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the (users) database
    if user_auth_enabled():
        await db.create_db_and_tables(statedir)

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
if user_auth_enabled():
    app.include_router(
        users.fastapi_users.get_auth_router(users.auth_backend),
        prefix="/auth/jwt", tags=["auth"]
    )
    app.include_router(
        users.fastapi_users.get_register_router(
            schemas.UserRead, schemas.UserCreate),
        prefix="/auth", tags=["auth"],
    )
    # TODO: Support user verification, allow password reset and user deletion.
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"))
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get('/api/roots')
async def get_roots(user: db.User = Depends(current_active_user)) -> dict:
    """
    Get a dict of roots, with root names as keys and properties as values.

    Returns
    -------
    dict
        The dict of roots.
    """
    if not user:
        return database.roots
    roots = database.roots.copy()
    scratch_root = models.Root(name='@scratch', http='', subscribed=True)
    roots[scratch_root.name] = scratch_root
    return roots


def get_root(name):
    root = database.roots.get(name)
    if root is None:
        srv_utils.raise_not_found(f'{name} not known by the broker')

    return root


@app.post('/api/subscribe/{name}')
async def post_subscribe(
    name: str,
    user: db.User = Depends(current_active_user),
):
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
    if name != '@scratch' or not user:
        get_root(name)  # Not Found
        follow(name)
    return 'Ok'


@app.get('/api/list/{name}')
async def get_list(
    name: str,
    user: db.User = Depends(current_active_user),
):
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
    if user and name == '@scratch':
        rootdir = scratch / str(user.id)
    else:
        root = get_root(name)
        rootdir = cache / root.name

    if not rootdir.exists():
        if name == '@scratch':
            return []
        srv_utils.raise_not_found(f'Not subscribed to {name}')

    return [
        relpath.with_suffix('') if relpath.suffix == '.b2' else relpath
        for path, relpath in utils.walk_files(rootdir)
    ]


# TODO: This endpoint should probably be removed.
@app.get('/api/url/{path:path}',
         dependencies=[Depends(current_active_user)])
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
async def get_info(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """
    Get the metadata of a dataset.

    Parameters
    ----------
    path : pathlib.Path
        The path to the dataset.

    Returns
    -------
    dict
        The metadata of the dataset.
    """
    abspath, _ = abspath_and_dataprep(path, user=user)
    return srv_utils.read_metadata(abspath, cache=cache)


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


async def download_expr_deps(abspath):
    """
    Download the datasets that the lazy expression dataset depends on.

    Parameters
    ----------
    abspath : pathlib.Path
        The absolute path to the lazy expression dataset.

    Returns
    -------
    None
        When finished, expression dependencies are available in cache.
    """
    def download_dep(ndarr):
        path = pathlib.Path(ndarr.schunk.urlpath)
        return partial_download(path, str(path.relative_to(cache)))
    expr = blosc2.open(abspath)
    await asyncio.gather(*[download_dep(op) for op in expr.operands.values()])


def abspath_and_dataprep(path: pathlib.Path,
                         slice_: (tuple | None) = None,
                         user: (db.User | None) = None) -> tuple[
                             pathlib.Path,
                             Callable[[], Awaitable],
                         ]:
    """
    Get absolute path in local storage and data preparation operation.

    After awaiting for the preparation operation to complete, data in the
    dataset should be ready for reading, either that covered by the slice if
    given, or the whole data otherwise.
    """
    parts = list(path.parts)
    if user and parts[0] == '@scratch':
        filepath = scratch / str(user.id) / pathlib.Path(*parts[1:])
        abspath = srv_utils.cache_lookup(scratch, filepath)
        async def dataprep():
            return await download_expr_deps(abspath)
    else:
        filepath = cache / path
        abspath = srv_utils.cache_lookup(cache, filepath)
        async def dataprep():
            return await partial_download(abspath, str(path), slice_)
    return (abspath, dataprep)


@app.get('/api/fetch/{path:path}')
async def fetch_data(
    path: pathlib.Path,
    slice_: str = None,
    prefer_schunk: bool = False,
    user: db.User = Depends(current_active_user),
):
    """
    Fetch a dataset.

    Parameters
    ----------
    path : pathlib.Path
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

    slice_ = api_utils.parse_slice(slice_)
    # Download and update the necessary chunks of the schunk in cache
    abspath, dataprep = abspath_and_dataprep(path, slice_, user=user)
    await dataprep()

    array, schunk = srv_utils.open_b2(abspath)
    typesize = array.dtype.itemsize if array is not None else schunk.typesize
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


@app.get('/api/download-url/{path:path}')
async def download_url(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """
    Return the download URL from the publisher.

    Parameters
    ----------
    path : pathlib.Path
        The path to the dataset.

    Returns
    -------
    url
        The url of the file in 'files/' or 'scratch/' to be downloaded later
        on.
    """

    # Download and update the necessary chunks of the schunk in cache
    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()

    # The complete file is already in the static files/ dir, so return the url.
    # We don't currently decompress data before downloading, so let's add the extension
    # in the url, if it is missing.
    spath = f'{path}.b2' if abspath.suffix == '.b2' else str(path)
    if path.parts and path.parts[0] == '@scratch':
        spath = spath.replace('@scratch', str(user.id), 1)
        return f'{urlbase}scratch/{spath}'
    return f'{urlbase}files/{spath}'


@app.get('/api/download/{path:path}',
         dependencies=[Depends(current_active_user)])
async def download_data(path: str):
    """
    Download a dataset.

    Parameters
    ----------
    path : str
        The path to the dataset.

    Returns
    -------
    The file's data.
    """

    abspath = srv_utils.cache_lookup(cache, path)

    # Download and update the necessary chunks of the schunk in cache
    await partial_download(abspath, path)

    # Send the data to the client
    abspath = pathlib.Path(abspath)
    return FileResponse(abspath, filename=abspath.name)


#
# Static files (as `StaticFiles` does not support authorization)
#

async def download_static(path: str, directory: pathlib.Path):
    abspath = srv_utils.cache_lookup(directory, path)
    abspath = pathlib.Path(abspath)
    # TODO: Support conditional requests, HEAD, etc.
    return FileResponse(abspath, filename=abspath.name)


@app.get('/files/{path:path}',
         dependencies=[Depends(current_active_user)])
async def download_cached(path: str):
    if path.endswith('.b2'):
        path = path[:-3]  # let cache lookup re-add extension
    return await download_static(path, cache)


@app.get('/scratch/{path:path}')
async def download_scratch(path: str,
                           user: db.User = Depends(current_active_user)):
    parts = pathlib.Path(path).parts
    if user and (not parts or parts[0] != str(user.id)):
        raise fastapi.HTTPException(status_code=401, detail="Unauthorized")
    return await download_static(path, scratch)


#
# HTML interface
#

if user_auth_enabled():
    @app.get("/login", response_class=HTMLResponse)
    async def html_login(
            request: Request,
            user: db.User = Depends(optional_user)
    ):
        if user:
            return RedirectResponse("/", status_code=307)

        return templates.TemplateResponse(request, "login.html")

    @app.get("/logout", response_class=HTMLResponse)
    async def html_logout(
            request: Request,
            user: db.User = Depends(optional_user)
    ):
        if user:
            return RedirectResponse("/", status_code=307)

        return templates.TemplateResponse(request, "logout.html")

    @app.get("/register", response_class=HTMLResponse)
    async def html_register(
            request: Request,
            user: db.User = Depends(optional_user)
    ):
        if user:
            return RedirectResponse("/", status_code=307)

        return templates.TemplateResponse(request, "register.html")

    # TODO: Support user verification, allow password reset and user deletion.


@app.get("/", response_class=HTMLResponse)
@app.get("/roots/{path:path}")
async def html_home(
    request: Request,
    path: str = '',
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = '',
    # Dependencies
    opt_user: db.User = Depends(optional_user),
):

    # Redirect to login page if user not authenticated.
    if user_auth_enabled() and not opt_user:
        return RedirectResponse("/login", status_code=307)

    context = {}
    if opt_user:
        context['username'] = opt_user.email

    context['roots_url'] = make_url(request, 'htmx_root_list', {'roots': roots})
    if roots:
        paths_url = make_url(request, 'htmx_path_list', {'roots': roots, 'search': search})
        context['paths_url'] = paths_url

    if path:
        context["meta_url"] = make_url(request, 'htmx_path_info', path=path)

    return templates.TemplateResponse(request, "home.html", context)


@app.get("/htmx/root-list/")
async def htmx_root_list(
    request: Request,
    # Query
    roots: list[str] = fastapi.Query([]),
    # Depends
    user: db.User = Depends(current_active_user),
):

    context = {
        "roots": sorted(database.roots.values(), key=lambda x: x.name),
        "checked": roots,
        "user": user,
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
    # Depends
    user: db.User = Depends(current_active_user),
):

    def get_names():
        n = 1
        while True:
            for name in itertools.product(* [string.ascii_lowercase] * n):
                yield ''.join(name)
            n += 1

    names = get_names()

    datasets = []
    for root in roots:
        if user and root == '@scratch':
            rootdir = scratch / str(user.id)
        else:
            if not get_root(root).subscribed:
                follow(root)
            rootdir = cache / root

        for path, relpath in utils.walk_files(rootdir):
            if relpath.suffix == '.b2':
                relpath = relpath.with_suffix('')
            if search in str(relpath):
                path = f'{root}/{relpath}'
                datasets.append({
                    'path': path,
                    'name': next(names),
                })

    # Render template
    cmd_url = make_url(request, 'htmx_command')
    search_url = make_url(request, 'htmx_path_list', {'roots': roots})
    context = {
        "datasets": datasets,
        "search_text": search,
        "search_url": search_url,
        "cmd_url": cmd_url,
        "user": user,
    }
    response = templates.TemplateResponse(request, "path_list.html", context)

    # Push URL
    args = {'roots': roots}
    if search:
        args['search'] = search
    push_url = furl.furl(hx_current_url).set(args).url
    response.headers['HX-Push-Url'] = push_url

    return response


@app.get("/htmx/path-info/{path:path}", response_class=HTMLResponse)
async def htmx_path_info(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    abspath, _ = abspath_and_dataprep(path, user=user)
    meta = srv_utils.read_metadata(abspath, cache=cache)

    #getattr(meta, 'schunk', meta).vlmeta['contenttype'] = 'tomography'
    if hasattr(getattr(meta, 'schunk', meta), 'vlmeta'):
        contenttype = getattr(meta, 'schunk', meta).vlmeta.get('contenttype')
    else:
        contenttype = None
    plugin = plugins.get(contenttype)
    if plugin:
        display = {
            "url": f"/plugins/{plugin.name}/display/{path}",
        }
    elif path.suffix == ".md":
        display = {
            "url": f"/markdown/{path}",
        }
    else:
        display = None

    context = {
        "path": path,
        "meta": meta,
        "display": display,
    }

    # XXX
    if hasattr(meta, 'shape'):
        view_url = make_url(request, "htmx_path_view", path=path)
        context.update({
            "view_url": view_url,
            "shape": meta.shape,
        })

    response = templates.TemplateResponse(request, "info.html", context=context)

    # Preserve state (query)
    current_url = furl.furl(hx_current_url)
    current_query = current_url.query
    push_url = make_url(request, 'html_home', path=path)
    if current_query:
        push_url = f'{push_url}?{current_query.encode()}'

    response.headers['HX-Push-Url'] = push_url

    return response


@app.post("/htmx/path-view/{path:path}", response_class=HTMLResponse)
async def htmx_path_view(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Input parameters
    index: typing.Annotated[list[int], Form()] = None,
    size: typing.Annotated[list[int], Form()] = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()
    arr = blosc2.open(abspath)

    # Local variables
    shape = arr.shape
    ndims = len(shape)
    if ndims >= 2:
        view_ndims = 2
    elif ndims == 1:
        view_ndims = 1
    else:
        view_ndims = 0

    # Default values for input params
    index = (0,) * ndims if index is None else tuple(index)
    if size is None:
        size = [10, 10]

    inputs_size = []
    for i, dim in enumerate(shape[-view_ndims:]):
        inputs_size.append({
            'max': dim,
            'ndim': ndims - view_ndims  + i,
            'value': size[i],
        })

    inputs = []
    for i, (value, dim) in enumerate(zip(index, shape)):
        if i < ndims - 2:
            step = 1
        elif dim > 10:
            step = inputs_size[i - (ndims - view_ndims)]['value']
        else:
            step = None
        inputs.append({'step': step, 'max': dim - 1, 'value': value})

    # Get array view
    if ndims >= 2:
        arr = arr[index[:-2]]
        i, j = index[-2:]
        isize = size[0] + 1
        jsize = size[1] + 1
        arr = arr[i:i+isize, j:j+jsize]
    elif ndims == 1:
        i = index[0]
        isize = size[0] + 1
        arr = [arr[i:i+isize]]
    else:
        arr = [[arr[()]]]

    # Render
    context = {
        "view_url": make_url(request, "htmx_path_view", path=path),
        "inputs": inputs,
        "inputs_size": inputs_size,
        "rows": list(arr),
    }
    return templates.TemplateResponse(request, "info_view.html", context)

@app.post("/htmx/command/", response_class=HTMLResponse)
async def htmx_command(
    request: Request,
    # Body
    command: typing.Annotated[str, Form()],
    names: typing.Annotated[list[str], Form()],
    paths: typing.Annotated[list[str], Form()],
    # Depends
    user: db.User = Depends(current_active_user),
):

    # Parse command
    try:
        result_name, expr = command.split('=')
        result_name = result_name.strip()
        expr = expr.strip()
        vars = ne.NumExpr(expr).input_names
    except (SyntaxError, ValueError):
        error = 'Invalid syntax'
        return templates.TemplateResponse(request, "command.html", {'text': error})

    # Open expression datasets and create the lazy expression dataset
    var_dict = {}
    for var in vars:
        path = paths[names.index(var)]
        var_dict[var] = blosc2.open(cache / path, mode="r")
    arr = eval(expr, var_dict)
    path = scratch / str(user.id)
    path.mkdir(exist_ok=True, parents=True)
    arr.save(urlpath=f'{path / result_name}.b2nd', mode="w")

    # TODO Display info and update list
    context = {'text': 'Output saved'}
    response = templates.TemplateResponse(request, "command.html", context)
    return response



@app.get("/markdown/{path:path}", response_class=HTMLResponse,
         dependencies=[Depends(current_active_user)])
async def markdown(
        request: Request,
        # Path parameters
        path: pathlib.Path,
):
    abspath = srv_utils.cache_lookup(cache, cache / path)
    await partial_download(abspath, str(path))
    arr = blosc2.open(abspath)
    content = arr[:]

    import markdown
    temp_html = markdown.markdown(content.decode('utf-8'))
    f = open(f"{BASE_DIR}/templates/markdown.html", "w")
    f.write(temp_html)
    f.close()

    return templates.TemplateResponse(request, "markdown.html", context={})


#
# Command line interface
#

plugins = {}

def main():
    conf = utils.get_conf('subscriber', allow_id=True)
    _stdir = '_caterva2/sub' + (f'.{conf.id}' if conf.id else '')
    parser = utils.get_parser(broker=conf.get('broker.http', 'localhost:8000'),
                              http=conf.get('.http', 'localhost:8002'),
                              url=conf.get('.url'),
                              loglevel=conf.get('.loglevel', 'warning'),
                              statedir=conf.get('.statedir', _stdir),
                              id=conf.id)
    args = utils.run_parser(parser)

    # Global configuration
    global broker
    broker = args.broker

    # Init cache
    global statedir, cache
    statedir = args.statedir.resolve()
    cache = statedir / 'cache'
    cache.mkdir(exist_ok=True, parents=True)
    # Use `download_cached()`, `StaticFiles` does not support authorization.
    #app.mount("/files", StaticFiles(directory=cache), name="files")

    # Scratch dir
    global scratch
    scratch = statedir / 'scratch'
    scratch.mkdir(exist_ok=True, parents=True)
    # Use `download_scratch()`, `StaticFiles` does not support authorization.
    #app.mount("/scratch", StaticFiles(directory=scratch), name="scratch")

    # Init database
    global database
    model = models.Subscriber(roots={}, etags={})
    database = srv_utils.Database(statedir / 'db.json', model)

    # Register display plugins
    app.mount(f"/plugins/{tomography.name}", tomography.app)
    plugins[tomography.contenttype] = tomography
    tomography.init(cache, partial_download)

    # Run
    global urlbase
    host, port = args.http
    urlbase = args.url
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
