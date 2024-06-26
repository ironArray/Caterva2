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
import string
import typing
from collections.abc import Awaitable, Callable

# FastAPI
from fastapi import Depends, FastAPI, Form, Request, UploadFile, responses
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import fastapi

# Requirements
import blosc2
import furl
import httpx
import markdown
import numexpr as ne
import numpy as np
import uvicorn

# Project
from caterva2 import utils, api_utils, models
from caterva2.services import srv_utils
from caterva2.services.subscriber import db, schemas, users


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


def _setup_plugin_globals():
    from . import plugins
    # These need to be available for plugins at import time.
    plugins.current_active_user = current_active_user


_setup_plugin_globals()


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
    lock = locks.setdefault(path, asyncio.Lock())
    async with lock:
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
        for n in nchunks:
            if not srv_utils.chunk_is_available(schunk, n):
                await download_chunk(path, schunk, n)


async def download_expr_deps(expr):
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
    coroutines = []
    for ndarr in expr.operands.values():
        relpath = srv_utils.get_relpath(ndarr, cache, scratch)
        if relpath.parts[0] != '@scratch':
            abspath = pathlib.Path(ndarr.schunk.urlpath)
            coroutine = partial_download(abspath, str(relpath))
            coroutines.append(coroutine)

    await asyncio.gather(*coroutines)


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
    parts = path.parts
    if user and parts[0] == '@scratch':
        filepath = scratch / str(user.id) / pathlib.Path(*parts[1:])
        abspath = srv_utils.cache_lookup(scratch, filepath)
        expr = blosc2.open(abspath)
        if isinstance(expr, blosc2.LazyArray):
            async def dataprep():
                return await download_expr_deps(expr)
        else:
            async def dataprep():
                pass

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

    Returns
    -------
    FileResponse or StreamingResponse
        The (slice of) dataset as a Blosc2 schunk.  When the whole dataset is
        to be downloaded (instead of some slice which does not cover it fully),
        its stored image is served containing all data and metadata (including
        variable length fields).
    """

    slice_ = api_utils.parse_slice(slice_)
    # Download and update the necessary chunks of the schunk in cache
    abspath, dataprep = abspath_and_dataprep(path, slice_, user=user)
    await dataprep()

    array, schunk = srv_utils.open_b2(abspath)
    typesize = array.dtype.itemsize if array is not None else schunk.typesize
    shape = array.shape if array is not None else (len(schunk),)

    whole = slice_ is None or slice_ == ()
    if not whole and isinstance(slice_, tuple):
        whole = all(isinstance(sl, slice)
                    and (sl.start or 0) == 0
                    and (sl.stop is None or sl.stop >= sh)
                    and sl.step in (None, 1)
                    for sl, sh in zip(slice_, shape))

    if whole and schunk is not None:  # whole and not lazy expr
        # Send the data in the file straight to the client,
        # avoiding slicing and re-compression.
        return FileResponse(abspath, filename=abspath.name,
                            media_type='application/octet-stream')

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
    data = array if array is not None else schunk
    if not slice_:
        # data is still a SChunk, so we need to get either a NumPy array, or a bytes object
        data = data[()] if array is not None else data[:]

    if isinstance(data, np.ndarray):
        data = blosc2.asarray(data)
        data = data.to_cframe()
    elif isinstance(data, bytes):
        # A bytes object can still be compressed as a SChunk
        schunk = blosc2.SChunk(data=data, cparams={'typesize': typesize})
        data = schunk.to_cframe()
    downloader = srv_utils.iterchunk(data)

    return responses.StreamingResponse(downloader,
                                       media_type='application/octet-stream')


def make_lazyexpr(name: str, expr: str, operands: dict[str, str],
                  user: db.User) -> str:
    """
    Create a lazy expression dataset in scratch space.

    This may raise exceptions if there are problems parsing the dataset name
    or expression, or if the expression refers to operands which have not been
    defined.

    Parameters
    ----------
    name : str
        The name of the dataset to be created (without extension).
    expr : str
        The expression to be evaluated.  It must result in a lazy expression.
    operands : dictionary of strings mapping to strings
        The variables used in the expression and which dataset paths they
        refer to.

    Returns
    -------
    str
        The path of the newly created (or overwritten) dataset.
    """

    if not user:
        raise fastapi.HTTPException(
            status_code=401,  # unauthorized
            detail="Creating lazy expressions requires enabling user authentication",
        )

    # Parse expression
    name = name.strip()
    expr = expr.strip()
    if not name or not expr:
        raise ValueError("Name or expression should not be empty")
    vars = ne.NumExpr(expr).input_names

    # Open expression datasets
    var_dict = {}
    for var in vars:
        path = operands[var]

        # Detect @scratch
        path = pathlib.Path(path)
        if path.parts[0] == '@scratch':
            abspath = scratch / str(user.id) / pathlib.Path(*path.parts[1:])
        else:
            abspath = cache / path

        var_dict[var] = blosc2.open(abspath, mode="r")

    # Create the lazy expression dataset
    arr = eval(expr, var_dict)
    if not isinstance(arr, blosc2.LazyExpr):
        cname = type(arr).__name__
        raise TypeError(f"Evaluates to {cname} instead of lazy expression")

    path = scratch / str(user.id)
    path.mkdir(exist_ok=True, parents=True)
    arr.save(urlpath=f'{path / name}.b2nd', mode="w")

    return f'@scratch/{name}.b2nd'


@app.post('/api/lazyexpr/')
async def lazyexpr(
    expr: models.NewLazyExpr,
    user: db.User = Depends(current_active_user),
) -> str:
    """
    Create a lazy expression dataset in scratch space.

    The JSON request body must contain a "name" for the dataset to be created
    (without extension), an "expression" to be evaluated, which must result in
    a lazy expression, and an "operands" object which maps variable names used
    in the expression to the dataset paths that they refer to.

    Returns
    -------
    str
        The path of the newly created (or overwritten) dataset.
    """

    def error(msg):
        return fastapi.HTTPException(status_code=400, detail=msg)  # bad request

    try:
        result_path = make_lazyexpr(expr.name, expr.expression, expr.operands,
                                    user)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise error(f'Invalid name or expression: {exc}')
    except KeyError as ke:
        raise error(f'Expression error: {ke.args[0]} is not in the list of available datasets')
    except RuntimeError as exc:
        raise error(str(exc))

    return result_path


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

    hx_current_url = furl.furl(hx_current_url)

    # Prepare datasets context
    def get_names():
        n = 1
        while True:
            for name in itertools.product(* [string.ascii_lowercase] * n):
                yield ''.join(name)
            n += 1

    names = get_names()

    query = {'roots': roots, 'search': search}
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
                url = make_url(request, "html_home", path=path, query=query)
                datasets.append({
                    'path': path,
                    'name': next(names),
                    'url': url,
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
    push_url = hx_current_url.set(args).url
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

    try:
        abspath, _ = abspath_and_dataprep(path, user=user)
    except FileNotFoundError:
        return htmx_error(request, 'FileNotFoundError: missing operand(s)')

    meta = srv_utils.read_metadata(abspath, cache=cache, scratch=scratch)

    vlmeta = getattr(getattr(meta, 'schunk', meta), 'vlmeta', {})
    contenttype = vlmeta.get('contenttype') or guess_dset_ctype(path, meta)
    plugin = plugins.get(contenttype)
    if plugin:
        display = {
            "url": f"/plugins/{plugin.name}/display/{path}",
            "label": plugin.label,
        }
    elif path.suffix == ".md":
        display = {
            "url": f"/markdown/{path}",
            "label": "Display",
        }
    else:
        display = None

    context = {
        "path": path,
        "meta": meta,
        "display": display,
        "scratch": path.parts[0] == '@scratch',
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
    push_url = make_url(request, 'html_home', path=path)
    current_query = furl.furl(hx_current_url).query
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
    sizes: typing.Annotated[list[int], Form()] = None,
    fields: typing.Annotated[list[str], Form()] = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()
    arr = blosc2.open(abspath)

    # Local variables
    shape = arr.shape
    ndims = len(shape)
    has_ndfields = hasattr(arr, 'fields') and arr.fields != {}

    # Set of dimensions that define the window
    # TODO Allow the user to choose the window dimensions
    dims = list(range(ndims))
    if ndims == 0:
        view_dims = {}
    elif ndims == 1 or has_ndfields:
        view_dims = {dims[-1]}
    else:
        view_dims = {dims[-2], dims[-1]}

    # Default values for input params
    index = (0,) * ndims if index is None else tuple(index)
    if sizes is None:
        sizes = [min(dim, 10) if i in view_dims else 1 for i, dim in enumerate(shape)]

    inputs = []
    tags = []
    for i, (start, size, size_max) in enumerate(zip(index, sizes, shape)):
        mod = size_max % size
        start_max = size_max - (mod or size)
        inputs.append({
            'start': start,
            'start_max': start_max,
            'size': size,
            'size_max': size_max,
            'with_size': i in view_dims,
        })
        if inputs[-1]['with_size']:
            tags.append([k for k in range(start, min(start+size, size_max))])

    if has_ndfields:
        cols = list(arr.fields.keys())
        fields = fields or cols[:5]
        idxs = [cols.index(f) for f in fields]
        rows = [fields]

        # Get array view
        if ndims >= 2:
            arr = arr[index[:-1]]
            i, isize = index[-1], sizes[-1]
            arr = arr[i:i + isize]
            arr = arr.tolist()
        elif ndims == 1:
            i, isize = index[0], sizes[0]
            arr = arr[i:i + isize]
            arr = arr.tolist()
        else:
            arr = [arr[()].tolist()]
        rows += [[row[i] for i in idxs] for row in arr]
    else:
        # Get array view
        cols = None
        if ndims >= 2:
            arr = arr[index[:-2]]
            i, isize = index[-2], sizes[-2]
            j, jsize = index[-1], sizes[-1]
            arr = arr[i:i+isize, j:j+jsize]
            rows = [tags[-1]] + list(arr)
        elif ndims == 1:
            i, isize = index[0], sizes[0]
            arr = [arr[i:i+isize]]
            rows = [tags[-1]] + list(arr)
        else:
            arr = [[arr[()]]]
            rows = list(arr)

    # Render
    context = {
        "view_url": make_url(request, "htmx_path_view", path=path),
        "inputs": inputs,
        "rows": rows,
        "cols": cols,
        "fields": fields,
        "tags": tags if len(tags) == 0 else tags[0],
    }
    return templates.TemplateResponse(request, "info_view.html", context)

@app.post("/htmx/command/", response_class=HTMLResponse)
async def htmx_command(
    request: Request,
    # Body
    command: typing.Annotated[str, Form()],
    names: typing.Annotated[list[str], Form()],
    paths: typing.Annotated[list[str], Form()],
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    operands = dict(zip(names, paths))
    try:
        result_name, expr = command.split('=')
        result_path = make_lazyexpr(result_name, expr, operands, user)
    except (SyntaxError, ValueError):
        return htmx_error(request,
                          'Invalid syntax: expected <varname> = <expression>')
    except TypeError as te:
        return htmx_error(request, f'Invalid expression: {te}')
    except KeyError as ke:
        return htmx_error(request,
                          f'Expression error: {ke.args[0]} is not in the list of available datasets')
    except RuntimeError as exc:
        return htmx_error(request, str(exc))

    # Redirect to display new dataset
    url = make_url(request, "html_home", path=result_path)
    return htmx_redirect(hx_current_url, url)


def htmx_error(request, msg):
    context = {'error': msg}
    return templates.TemplateResponse(request, "error.html", context, status_code=400)


def htmx_redirect(current_url, target_url):
    response = JSONResponse('OK')
    query = furl.furl(current_url).query
    roots = query.params.getlist('roots')
    if '@scratch' not in roots:
        query = query.add({'roots': '@scratch'})

    response.headers['HX-Redirect'] = f'{target_url}?{query.encode()}'
    return response

@app.post("/htmx/upload/")
async def htmx_upload(
    request: Request,
    # Body
    file: UploadFile,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    if not user:
        raise fastapi.HTTPException(status_code=401)  # unauthorized

    # Read file
    filename = pathlib.Path(file.filename)
    data = await file.read()
    if filename.suffix not in {'.b2frame', '.b2nd'}:
        schunk = blosc2.SChunk(data=data)
        data = schunk.to_cframe()
        filename = f'{filename}.b2frame'

    # Save file
    path = scratch / str(user.id)
    path.mkdir(exist_ok=True, parents=True)
    with open(path / filename, 'wb') as dst:
        dst.write(data)

    # Redirect to display new dataset
    path = f'@scratch/{filename}'
    url = make_url(request, "html_home", path=path)
    return htmx_redirect(hx_current_url, url)


@app.delete("/htmx/delete/{path:path}", response_class=HTMLResponse)
async def htmx_delete(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):

    parts = list(path.parts)
    assert parts[0] == '@scratch'

    # Remove
    parts[0] = str(user.id)
    path = pathlib.Path(*parts)
    (scratch / path).unlink()

    # Redirect to home
    url = make_url(request, "html_home")
    return htmx_redirect(hx_current_url, url)


@app.get("/markdown/{path:path}", response_class=HTMLResponse)
async def html_markdown(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
    # Response
    response_class=HTMLResponse,
):

    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()
    arr = blosc2.open(abspath)
    content = arr[:]
    # Markdown
    return markdown.markdown(content.decode('utf-8'))


#
# Command line interface
#

plugins = {}


def guess_dset_ctype(path: pathlib.Path, meta) -> str | None:
    """Try to guess dataset's content type (given path and metadata)."""
    for (ctype, plugin) in plugins.items():
        if (hasattr(plugin, 'guess')
                and plugin.guess(path, meta)):
            return ctype
    return None


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
    from .plugins import tomography  # delay module load
    app.mount(f"/plugins/{tomography.name}", tomography.app)
    plugins[tomography.contenttype] = tomography
    tomography.init(abspath_and_dataprep)

    # Run
    global urlbase
    host, port = args.http
    urlbase = args.url
    uvicorn.run(app, host=host, port=port)


if __name__ == '__main__':
    main()
