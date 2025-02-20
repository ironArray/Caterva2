###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import collections.abc
import contextlib
import functools
import io
import itertools
import json
import logging
import mimetypes
import os
import pathlib
import shutil
import string
import tarfile
import typing
import zipfile

# Requirements
import blosc2
import dotenv
import fastapi
import furl
import httpx
import markdown
import nbconvert
import nbformat
import numpy as np
import PIL.Image

# FastAPI
from fastapi import Depends, FastAPI, Form, Request, UploadFile, responses
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Project
from caterva2 import api_utils, models, utils
from caterva2.services import settings, srv_utils
from caterva2.services.subscriber import db, schemas, users

BASE_DIR = pathlib.Path(__file__).resolve().parent

# Set CATERVA2_SECRET=XXX in .env file in working directory
dotenv.load_dotenv()

# Logging
logger = logging.getLogger("sub")

# State
clients = {}  # topic: <PubSubClient>
locks = {}

mimetypes.add_type("text/markdown", ".md")  # Because in macOS this is not by default
mimetypes.add_type("application/x-ipynb+json", ".ipynb")


def guess_type(path):
    mimetype, encoding = mimetypes.guess_type(path)
    return mimetype


class PubSourceDataset:
    """
    Class for getting chunks from a dataset on a publisher service.
    """

    def __init__(self, abspath, path, metadata=None):
        self.path = pathlib.Path(path)
        if metadata is not None:
            suffix = abspath.suffix
            if suffix == ".b2nd":
                metadata = models.Metadata(**metadata)
                self._shape = metadata.shape
                self._chunks = metadata.chunks
                self._blocks = metadata.blocks
                dtype = metadata.dtype
                if metadata.dtype.startswith("["):
                    # TODO: eval is dangerous, but we mostly trust the metadata
                    # This is a list, so we need to convert it to a string
                    dtype = eval(dtype)
                self._dtype = np.dtype(dtype)
                self._cparams = dict(metadata.schunk.cparams)
            else:
                if suffix == ".b2frame":
                    metadata = models.SChunk(**metadata)
                else:
                    abspath = pathlib.Path(f"{abspath}.b2")
                    metadata = models.SChunk(**metadata)
                self._typesize = metadata.cparams.typesize
                self._chunksize = metadata.chunksize
                self._nbytes = metadata.nbytes
                self._cparams = dict(metadata.cparams)
            del self._cparams["filters, meta"]
            self._cparams = blosc2.CParams(**self._cparams)
            self.abspath = abspath
            if self.abspath is not None:
                self.abspath.parent.mkdir(exist_ok=True, parents=True)

    def _get_request_args(self, nchunk, return_async_client):
        root, *name = self.path.parts
        root = settings.database.roots[root]
        name = pathlib.Path(*name)

        url = f"/api/download/{name}"
        client, url = api_utils.get_client_and_url(root.http, url, return_async_client=return_async_client)
        args = {"url": url, "params": {"nchunk": nchunk}, "timeout": 5}
        return client, args

    def _get_chunk(self, nchunk):
        client, req_args = self._get_request_args(nchunk, return_async_client=False)

        response = client.get(**req_args)
        response.raise_for_status()
        return response.content

    async def _aget_chunk(self, nchunk):
        client, req_args = self._get_request_args(nchunk, return_async_client=True)

        async with client.stream("GET", **req_args) as resp:
            buffer = []
            async for chunk in resp.aiter_bytes():
                buffer.append(chunk)
            return b"".join(buffer)


# Class representing a NDArray in a publisher
class PubNDDataset(blosc2.ProxyNDSource, PubSourceDataset):
    @property
    def shape(self):
        return self._shape

    @property
    def chunks(self):
        return self._chunks

    @property
    def blocks(self):
        return self._blocks

    @property
    def dtype(self):
        return self._dtype

    @property
    def cparams(self):
        return self._cparams

    def get_chunk(self, nchunk: int) -> bytes:
        return self._get_chunk(nchunk)

    async def aget_chunk(self, nchunk: int) -> bytes:
        return await self._aget_chunk(nchunk)


# Class representing a SChunk in a publisher
class PubSCDataset(blosc2.ProxySource, PubSourceDataset):
    @property
    def typesize(self):
        return self._typesize

    @property
    def chunksize(self):
        return self._chunksize

    @property
    def nbytes(self):
        return self._nbytes

    @property
    def cparams(self):
        return self._cparams

    def get_chunk(self, nchunk: int) -> bytes:
        return self._get_chunk(nchunk)

    async def aget_chunk(self, nchunk: int) -> bytes:
        return await self._aget_chunk(nchunk)


# Factory function for creating a proxy for a dataset in publisher
def PubDataset(abspath, path, metadata=None):
    dataset = PubSourceDataset(abspath, path, metadata)
    # By using __new__() and updating the internal dict of the instance,
    # we can return the right class avoiding calling PubSourceDataset.__init__ again
    if hasattr(dataset, "_shape"):
        # return PubNDDataset(abspath, path, metadata)
        instance = PubNDDataset.__new__(PubNDDataset)
    else:
        # return PubSCDataset(abspath, path, metadata)
        instance = PubSCDataset.__new__(PubSCDataset)
    instance.__dict__.update(dataset.__dict__)
    return instance


def get_disk_usage():
    exclude = {"db.json", "db.sqlite"}
    return sum(path.stat().st_size for path, _ in utils.walk_files(settings.statedir, exclude=exclude))


def truncate_path(path, size=35):
    """
    Smart truncaion of a long path for display.
    """
    assert type(path) is str

    if len(path) < size:
        return path

    # If the path is short just truncate at the end
    parts = pathlib.Path(path).parts
    if len(parts) < 3:
        n = len(path) - size
        return path[:-n] + "..."

    # If the path is long be smarter
    first, last = parts[0], parts[-1]
    label = f"{first}/.../{last}"
    n = len(label) - size
    if n > 0:
        last = last[:-n] + "..."

    return f"{first}/.../{last}"


def make_url(request, name, query=None, **path_params):
    url = request.app.url_path_for(name, **path_params)
    url = str(url)  # <starlette.datastructures.URLPath>
    if query:
        url = furl.furl(url).set(query).url
    return settings.urlbase + url


async def new_root(data, topic):
    logger.info(f"NEW root {topic} {data=}")
    root = models.Root(**data)
    settings.database.roots[root.name] = root
    settings.database.save()


async def updated_dataset(data, topic):
    name = topic
    relpath = data["path"]

    rootdir = settings.cache / name
    abspath = rootdir / relpath
    metadata = data.get("metadata")
    if metadata is None:
        if abspath.suffix not in {".b2nd", ".b2frame"}:
            abspath = pathlib.Path(f"{abspath}.b2")
        if abspath.is_file():
            abspath.unlink()
    else:
        key = f"{name}/{relpath}"
        init_b2(abspath, key, metadata)


def init_b2(abspath, path, metadata):
    dataset = PubDataset(abspath, path, metadata)
    # TODO: not sure if this would prevent some kind of update in dataset. @jdavid?
    # if os.path.exists(dataset.abspath):
    #     return
    schunk_meta = metadata.get("schunk", metadata)
    vlmeta = {}
    for k, v in schunk_meta["vlmeta"].items():
        vlmeta[k] = v
    blosc2.Proxy(dataset, urlpath=dataset.abspath, vlmeta=vlmeta, caterva2_env=True)


def open_b2(abspath, path):
    """
    Open a Blosc2 dataset.

    Return a Proxy if the dataset is in a publisher,
    or the LazyExpr or Blosc2 container otherwise.
    """
    if pathlib.Path(path).parts[0] in {"@personal", "@shared", "@public"}:
        container = blosc2.open(abspath)
        if isinstance(container, blosc2.LazyExpr):
            # Open the operands properly
            operands = container.operands
            for key, value in operands.items():
                if "proxy-source" in value.schunk.meta:
                    # Save operand as Proxy, see blosc2.open doc for more info
                    relpath = srv_utils.get_relpath(
                        value, settings.cache, settings.personal, settings.shared, settings.public
                    )
                    operands[key] = open_b2(value.schunk.urlpath, relpath)
            return container
        else:
            return container

    # Return Proxy
    dataset = PubDataset(abspath, path)
    container = blosc2.open(abspath)
    # No need to pass caterva2_env=True since _cache has already been created
    return blosc2.Proxy(dataset, _cache=container)


#
# Internal API
#


def follow(name: str):
    root = settings.database.roots.get(name)
    if root is None:
        return {name: "This dataset does not exist in the network"}

    if not root.subscribed:
        root.subscribed = True
        settings.database.save()

    # Create root directory in the cache
    rootdir = settings.cache / name
    if not rootdir.exists():
        rootdir.mkdir(exist_ok=True)

    # Get list of datasets
    try:
        data = api_utils.get("/api/list", server=root.http)
    except httpx.ConnectError:
        return None

    # Initialize the datasets in the cache
    for relpath in data:
        # If-None-Match header
        key = f"{name}/{relpath}"
        val = settings.database.etags.get(key)
        headers = None if val is None else {"If-None-Match": val}

        # Call API
        response = api_utils.get(
            f"/api/info/{relpath}",
            headers=headers,
            server=root.http,
            raise_for_status=False,
            return_response=True,
        )
        if response.status_code == 304:
            continue

        response.raise_for_status()
        metadata = response.json()

        # Save metadata and create Proxy
        abspath = rootdir / relpath
        init_b2(abspath, key, metadata)

        # Save etag
        settings.database.etags[key] = response.headers["etag"]
        settings.database.save()

    # Subscribe to changes in the dataset
    if name not in clients:
        client = srv_utils.start_client(f"ws://{settings.broker}/pubsub")
        client.subscribe(name, updated_dataset)
        clients[name] = client

    return None


#
# HTTP API
#


def user_login_enabled():
    if settings.login:
        if not bool(os.environ.get("CATERVA2_SECRET")):
            raise RuntimeError("CATERVA2_SECRET envvar is required")
        return True

    return False


def user_register_enabled():
    if settings.register:
        if not settings.login:
            raise RuntimeError("login config must be enabled")
        return True

    return False


current_active_user = users.current_active_user if user_login_enabled() else (lambda: None)
"""Depend on this if the route needs an authenticated user (if enabled)."""

optional_user = (
    users.fastapi_users.current_user(optional=True, verified=False)  # TODO: set when verification works
    if user_login_enabled()
    else (lambda: None)
)
"""Depend on this if the route may do something with no authentication."""


def _setup_plugin_globals():
    from . import plugins

    # These need to be available for plugins at import time.
    plugins.current_active_user = current_active_user


_setup_plugin_globals()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the (users) database
    if user_login_enabled():
        await db.create_db_and_tables(settings.statedir)

    # Initialize roots from the broker
    client = None
    if settings.broker:
        try:
            data = api_utils.get("/api/roots", server=settings.broker)
        except httpx.ConnectError:
            logger.warning(f'Broker "{settings.broker}" not available')
        else:
            changed = False
            # Deleted
            d = list(settings.database.roots.items())
            for name, _root in d:
                if name not in data:
                    del settings.database.roots[name]
                    changed = True

            # New or updated
            for name, values in data.items():
                root = models.Root(**values)
                if name not in settings.database.roots:
                    settings.database.roots[root.name] = root
                    changed = True
                elif settings.database.roots[root.name].http != root.http:
                    settings.database.roots[root.name].http = root.http
                    changed = True

            if changed:
                settings.database.save()

            # Follow the @new channel to know when a new root is added
            client = srv_utils.start_client(f"ws://{settings.broker}/pubsub")
            client.subscribe("@new", new_root)

            # Resume following
            for path in settings.cache.iterdir():
                if path.is_dir():
                    follow(path.name)

    yield

    # Disconnect from worker
    if client is not None:
        await srv_utils.disconnect_client(client)


# Visualize the size of a file on a compact and human-readable format
def custom_filesizeformat(value):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024.0:
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


app = FastAPI(lifespan=lifespan)

# TODO: Support user verification
if user_login_enabled():
    app.include_router(
        users.fastapi_users.get_auth_router(users.auth_backend), prefix="/auth/jwt", tags=["auth"]
    )
    app.include_router(
        users.fastapi_users.get_reset_password_router(),
        prefix="/auth",
        tags=["auth"],
    )


if user_register_enabled():
    app.include_router(
        users.fastapi_users.get_register_router(schemas.UserRead, schemas.UserCreate),
        prefix="/auth",
        tags=["auth"],
    )


def url(path: str) -> str:
    return f"{settings.urlbase}/{path}"


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["filesizeformat"] = custom_filesizeformat
templates.env.globals["url"] = url


# Add CSS/JS to templates namespace
BUILD_DIR = "static/build/"
with (BASE_DIR / BUILD_DIR / "manifest.json").open() as file:
    manifest = json.load(file)
    entry = manifest["src/main.js"]
    templates.env.globals["main_css"] = url(BUILD_DIR + entry["css"][0])
    templates.env.globals["main_js"] = url(BUILD_DIR + entry["file"])


@app.get("/api/roots")
async def get_roots(user: db.User = Depends(optional_user)) -> dict:
    """
    Get a dict of roots, with root names as keys and properties as values.

    Returns
    -------
    dict
        The dict of roots.
    """
    # Here we just return the roots that are known by the broker
    # plus the special roots @personal, @shared and @public
    roots = settings.database.roots.copy()
    root = models.Root(name="@public", http="", subscribed=True)
    roots[root.name] = root
    if user:
        for name in ["@personal", "@shared"]:
            root = models.Root(name=name, http="", subscribed=True)
            roots[root.name] = root

    return roots


def get_root(name):
    root = settings.database.roots.get(name)
    if root is None:
        srv_utils.raise_not_found(f"{name} not known by the broker")

    return root


@app.post("/api/subscribe/{name}")
async def post_subscribe(
    name: str,
    user: db.User = Depends(optional_user),
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
    if name == "@public":
        pass
    elif name in {"@personal", "@shared"}:
        if not user:
            raise srv_utils.raise_unauthorized(f"Subscribing to {name} requires authentication")
    else:
        get_root(name)
        follow(name)
    return "Ok"


@app.get("/api/list/{path:path}")
async def get_list(
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
):
    """
    List the datasets in a root or directory.

    Parameters
    ----------
    path : Path
        The path to a root or directory.

    Returns
    -------
    list
        The list of datasets, as name strings relative to path.
    """
    # Get the root
    root = path.parts[0]
    if root == "@public":
        rootdir = settings.public
    elif root == "@personal":
        if not user:
            srv_utils.raise_not_found("@personal needs authentication")
        rootdir = settings.personal / str(user.id)
    elif root == "@shared":
        if not user:
            srv_utils.raise_not_found("@shared needs authentication")
        rootdir = settings.shared
    else:
        root = get_root(root)
        rootdir = settings.cache / root.name

    # List the datasets in root or directory
    directory = rootdir / pathlib.Path(*path.parts[1:])
    if directory.is_file():
        name = pathlib.Path(directory.name)
        return [str(name.with_suffix("") if name.suffix == ".b2" else name)]
    # Sort the list of datasets and return
    paths = [
        str(relpath.with_suffix("") if relpath.suffix == ".b2" else relpath)
        for _, relpath in utils.walk_files(directory)
    ]
    return sorted(paths)


@app.get("/api/info/{path:path}")
async def get_info(
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
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
    return srv_utils.read_metadata(
        abspath, settings.cache, settings.personal, settings.shared, settings.public
    )


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
        proxy = open_b2(abspath, path)
        await proxy.afetch(slice_)


def abspath_and_dataprep(
    path: pathlib.Path, slice_: (tuple | None) = None, user: (db.User | None) = None, may_not_exist=False
) -> tuple[
    pathlib.Path,
    collections.abc.Callable[[], collections.abc.Awaitable],
]:
    """
    Get absolute path in local storage and data preparation operation.

    After awaiting the preparation operation to complete, data in the
    dataset should be ready for reading, either that covered by the slice if
    given, or the whole data otherwise.
    """
    parts = path.parts
    if parts[0] == "@personal":
        if not user:
            raise fastapi.HTTPException(status_code=404)  # NotFound

        filepath = settings.personal / str(user.id) / pathlib.Path(*parts[1:])
        abspath = srv_utils.cache_lookup(settings.personal, filepath, may_not_exist)

        async def dataprep():
            pass

    elif parts[0] == "@shared":
        if not user:
            raise fastapi.HTTPException(status_code=404)  # NotFound

        filepath = settings.shared / pathlib.Path(*parts[1:])
        abspath = srv_utils.cache_lookup(settings.shared, filepath, may_not_exist)

        async def dataprep():
            pass

    elif parts[0] == "@public":
        filepath = settings.public / pathlib.Path(*parts[1:])
        abspath = srv_utils.cache_lookup(settings.public, filepath, may_not_exist)

        async def dataprep():
            pass

    else:
        filepath = settings.cache / path
        abspath = srv_utils.cache_lookup(settings.cache, filepath, may_not_exist)

        async def dataprep():
            return await partial_download(abspath, path, slice_)

    return (abspath, dataprep)


@app.get("/api/fetch/{path:path}")
async def fetch_data(
    path: pathlib.Path,
    slice_: str | None = None,
    user: db.User = Depends(optional_user),
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
    # This is still needed and will only update the necessary chunks
    await dataprep()
    container = open_b2(abspath, path)

    if isinstance(container, blosc2.Proxy):
        container = container._cache

    if isinstance(container, blosc2.NDArray | blosc2.LazyExpr):
        array = container
        schunk = getattr(array, "schunk", None)
        typesize = array.dtype.itemsize
        shape = array.shape
    else:
        # SChunk
        array = None
        schunk = container
        typesize = schunk.typesize
        shape = (len(schunk),)

    whole = slice_ is None or slice_ == ()
    if not whole and isinstance(slice_, tuple):
        whole = all(
            isinstance(sl, slice)
            and (sl.start or 0) == 0
            and (sl.stop is None or sl.stop >= sh)
            and sl.step in (None, 1)
            for sl, sh in zip(slice_, shape, strict=False)
        )

    if whole and not isinstance(container, blosc2.LazyExpr):
        # Send the data in the file straight to the client,
        # avoiding slicing and re-compression.
        return FileResponse(abspath, filename=abspath.name, media_type="application/octet-stream")

    if slice_:
        if array is not None:
            array = container[slice_] if array.ndim > 0 else container[()]
        else:
            if isinstance(slice_, int):
                # TODO: make SChunk support integer as slice
                slice_ = slice(slice_, slice_ + 1)
            schunk = container[slice_]

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
        schunk = blosc2.SChunk(data=data, cparams={"typesize": typesize})
        data = schunk.to_cframe()
    downloader = srv_utils.iterchunk(data)

    return responses.StreamingResponse(downloader, media_type="application/octet-stream")


@app.get("/api/download/{path:path}")
async def download_data(
    path: pathlib.Path,
    # Query parameters
    user: db.User = Depends(optional_user),
):
    async def downloader():
        yield await get_file_content(path, user)

    mimetype = guess_type(path)
    headers = {"Content-Disposition": f'attachment; filename="{path.name}"'}
    return responses.StreamingResponse(downloader(), media_type=mimetype, headers=headers)


html_exporter = nbconvert.HTMLExporter()


@app.get("/api/preview/{path:path}")
async def preview(
    path: pathlib.Path,
    # Query parameters
    width: int | None = None,
    user: db.User = Depends(optional_user),
):
    mimetype = guess_type(path)
    if mimetype.startswith("image/") and width:
        img = await get_image(path, user)

        def downloader():
            yield from resize_image(img, width)

    elif mimetype == "application/x-ipynb+json":
        content = await get_file_content(path, user)
        nb = nbformat.reads(content, as_version=4)
        html, resources = html_exporter.from_notebook_node(nb)
        return HTMLResponse(html)

    else:

        async def downloader():
            yield await get_file_content(path, user)

    return responses.StreamingResponse(downloader(), media_type=mimetype)


@app.get("/api/chunk/{path:path}")
async def get_chunk(
    path: pathlib.PosixPath,
    nchunk: int,
    user: db.User = Depends(optional_user),
):
    abspath, _ = abspath_and_dataprep(path, user=user)
    lock = locks.setdefault(path, asyncio.Lock())
    async with lock:
        if user and path.parts[0] == "@personal":
            container = open_b2(abspath, path)
            if isinstance(container, blosc2.LazyArray):
                # We do not support LazyUDF in Caterva2 yet.
                # In case we do, this would have to be changed.
                chunk = container.get_chunk(nchunk)
            else:
                schunk = getattr(container, "schunk", container)
                chunk = schunk.get_chunk(nchunk)
        else:
            sub_dset = PubDataset(abspath, path)
            chunk = await sub_dset.aget_chunk(nchunk)

    downloader = srv_utils.iterchunk(chunk)
    return responses.StreamingResponse(downloader)


def make_expr(name: str, expr: str, operands: dict[str, str], user: db.User, lazy: bool = True) -> str:
    """
    Create a lazy expression dataset in personal space.

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
        raise srv_utils.raise_unauthorized("Creating lazy expressions requires authentication")

    # Parse expression
    name = name.strip()
    expr = expr.strip()
    if not name or not expr:
        raise ValueError("Name or expression should not be empty")
    vars = blosc2.get_expr_operands(expr)

    # Open expression datasets
    var_dict = {}
    for var in vars:
        path = operands[var]
        # Detect special roots
        path = pathlib.Path(path)
        if path.parts[0] == "@personal":
            abspath = settings.personal / str(user.id) / pathlib.Path(*path.parts[1:])
        elif path.parts[0] == "@shared":
            abspath = settings.shared / pathlib.Path(*path.parts[1:])
        elif path.parts[0] == "@public":
            abspath = settings.public / pathlib.Path(*path.parts[1:])
        else:
            abspath = settings.cache / path
        var_dict[var] = open_b2(abspath, path)

    # Create the lazy expression dataset
    arr = blosc2.lazyexpr(expr, var_dict)
    if not isinstance(arr, blosc2.LazyExpr):
        cname = type(arr).__name__
        raise TypeError(f"Evaluates to {cname} instead of lazy expression")

    # Save to filesystem
    path = settings.personal / str(user.id)
    path.mkdir(exist_ok=True, parents=True)
    urlpath = f"{path / name}.b2nd"
    if lazy:
        arr.save(urlpath=urlpath, mode="w")
    else:
        arr.compute(urlpath=urlpath, mode="w")

    return f"@personal/{name}.b2nd"


@app.post("/api/lazyexpr/")
async def lazyexpr(
    expr: models.NewLazyExpr,
    user: db.User = Depends(current_active_user),
) -> str:
    """
    Create a lazy expression dataset in personal space.

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
        result_path = make_expr(expr.name, expr.expression, expr.operands, user)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise error(f"Invalid name or expression: {exc}") from exc
    except KeyError as ke:
        raise error(f"Expression error: {ke.args[0]} is not in the list of available datasets") from ke
    except RuntimeError as exc:
        raise error(f"Runtime error: {exc}") from exc

    return result_path


@app.post("/api/move/")
async def move(
    payload: models.MoveCopyPayload,
    user: db.User = Depends(current_active_user),
):
    """
    Move a dataset.

    Returns
    -------
    str
        The new path of the dataset.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Moving files requires authentication")

    # Both src and dst should start with a special root
    if not payload.src.startswith(("@personal", "@shared", "@public")):
        raise fastapi.HTTPException(
            status_code=400, detail="Only moving from @personal or @shared or @public roots is allowed"
        )
    if not payload.dst.startswith(("@personal", "@shared", "@public")):
        raise fastapi.HTTPException(
            status_code=400, detail="Only moving to @personal or @shared or @public roots is allowed"
        )
    namepath = pathlib.Path(payload.src)
    destpath = pathlib.Path(payload.dst)
    abspath, _ = abspath_and_dataprep(namepath, user=user)
    dest_abspath, _ = abspath_and_dataprep(destpath, user=user, may_not_exist=True)

    # If destination has not an extension, assume it is a directory
    # If user wants something without an extension, she can add a '.b2' extension :-)
    if dest_abspath.is_dir() or not dest_abspath.suffix:
        dest_abspath /= abspath.name
        destpath /= namepath.name

    # Not sure if we should allow overwriting, but let's allow it for now
    # if dest_abspath.exists():
    #     raise fastapi.HTTPException(status_code=409, detail="The new path already exists")

    # Make sure the destination directory exists
    dest_abspath.parent.mkdir(exist_ok=True, parents=True)
    abspath.rename(dest_abspath)

    return str(destpath)


@app.post("/api/copy/")
async def copy(
    payload: models.MoveCopyPayload,
    user: db.User = Depends(current_active_user),
):
    """
    Copy a dataset.

    Returns
    -------
    str
        The path of the copied dataset.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Copying files requires authentication")

    src, dst = payload.src, payload.dst
    # src should start with a special root or known root
    if not src.startswith(("@personal", "@shared", "@public")) and src not in settings.database.roots:
        raise fastapi.HTTPException(status_code=400, detail="Only copying from existing roots is allowed")
    # dst should start with a special root
    if not dst.startswith(("@personal", "@shared", "@public")):
        raise fastapi.HTTPException(
            status_code=400, detail="Only copying to @personal or @shared or @public roots is allowed"
        )

    namepath, destpath = pathlib.Path(src), pathlib.Path(dst)
    abspath, _ = abspath_and_dataprep(namepath, user=user)
    dest_abspath, _ = abspath_and_dataprep(destpath, user=user, may_not_exist=True)

    # If destination has not an extension, assume it is a directory
    # If user wants something without an extension, she should add a '.b2' extension
    if dest_abspath.is_dir() or not dest_abspath.suffix:
        dest_abspath /= abspath.name
        destpath /= namepath.name

    # Not sure if we should allow overwriting, but let's allow it for now
    # if dest_abspath.exists():
    #     raise fastapi.HTTPException(status_code=409, detail="The new path already exists")

    dest_abspath.parent.mkdir(exist_ok=True, parents=True)
    if abspath.is_dir():
        shutil.copytree(abspath, dest_abspath)
    else:
        shutil.copy(abspath, dest_abspath)

    return str(destpath)


@app.post("/api/upload/{path:path}")
async def upload_file(
    path: pathlib.Path,
    file: UploadFile,
    user: db.User = Depends(current_active_user),
):
    """
    Upload a file to a root.

    Parameters
    ----------
    path : pathlib.Path
        The path to store the uploaded file.
    file : UploadFile
        The file to upload.

    Returns
    -------
    str
        The path of the uploaded file.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Uploading requires authentication")

    # Replace the root with absolute path
    root = path.parts[0]
    if root == "@personal":
        abspath = settings.personal / str(user.id) / pathlib.Path(*path.parts[1:])
    elif root == "@shared":
        abspath = settings.shared / pathlib.Path(*path.parts[1:])
    elif root == "@public":
        abspath = settings.public / pathlib.Path(*path.parts[1:])
    else:
        raise fastapi.HTTPException(
            status_code=400,  # bad request
            detail="Only uploading to @personal or @shared or @public roots is allowed",
        )

    # We may upload a new file, or replace an existing file
    if abspath.is_dir():
        abspath /= file.filename
        path /= file.filename

    # Check quota
    # TODO To be fair we should check quota later (after compression, zip unpacking etc.)
    data = await file.read()
    if abspath.suffix not in {".b2", ".b2frame", ".b2nd"}:
        schunk = blosc2.SChunk(data=data)
        newsize = schunk.nbytes
    else:
        newsize = len(data)

    if settings.quota:
        try:
            oldsize = abspath.stat().st_size
        except FileNotFoundError:
            oldsize = 0

        total_size = get_disk_usage() - oldsize + newsize
        if total_size > settings.quota:
            error = "Upload failed because quota limit has been exceeded."
            raise fastapi.HTTPException(status_code=400, detail=error)

    # If regular file, compress it
    abspath.parent.mkdir(exist_ok=True, parents=True)
    if abspath.suffix not in {".b2", ".b2frame", ".b2nd"}:
        data = schunk.to_cframe()
        abspath = abspath.with_suffix(abspath.suffix + ".b2")

    # Write the file
    with open(abspath, "wb") as f:
        f.write(data)

    # Return the urlpath
    return str(path)


@app.post("/api/remove/{path:path}")
async def remove(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """
    Remove a dataset or a directory path.

    Parameters
    ----------
    path : pathlib.Path
        The path of dataset / directory to remove.

    Returns
    -------
    list
        A list with the paths that have been removed.
    """

    if not user:
        raise srv_utils.raise_unauthorized("Removing files requires authentication")

    # Replace the root with absolute path
    root = path.parts[0]
    if root == "@personal":
        path2 = settings.personal / str(user.id) / pathlib.Path(*path.parts[1:])
    elif root == "@shared":
        path2 = settings.shared / pathlib.Path(*path.parts[1:])
    elif root == "@public":
        path2 = settings.public / pathlib.Path(*path.parts[1:])
    else:
        detail = "Only removing from @personal or @shared or @public roots is allowed"
        raise fastapi.HTTPException(status_code=400, detail=detail)

    # If path2 is a directory, remove the contents of the directory
    if path2.is_dir():
        shutil.rmtree(path2)
    else:
        # Try to unlink the file
        try:
            path2.unlink()
        except FileNotFoundError:
            # Try adding a .b2 extension
            path2 = path2.with_suffix(path2.suffix + ".b2")
            try:
                path2.unlink()
            except FileNotFoundError as exc:
                raise fastapi.HTTPException(
                    status_code=404,  # not found
                    detail="The specified path does not exist",
                ) from exc

    # Return the path
    return path


@app.post("/api/addnotebook/{path:path}")
async def add_notebook(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """
    Add a new notebook.

    Parameters
    ----------
    path : pathlib.Path
        The path where the notebook will be created.

    Returns
    -------
    str
        The path of the new notebook.
    """

    if not user:
        raise srv_utils.raise_unauthorized("Authentication is required")

    if path.suffix != ".ipynb":
        detail = "Notebooks must end with the .ipynb extension"
        raise fastapi.HTTPException(status_code=400, detail=detail)

    # Replace the root with absolute path
    root = path.parts[0]
    if root == "@personal":
        path2 = settings.personal / str(user.id) / pathlib.Path(*path.parts[1:])
    elif root == "@shared":
        path2 = settings.shared / pathlib.Path(*path.parts[1:])
    elif root == "@public":
        path2 = settings.public / pathlib.Path(*path.parts[1:])
    else:
        detail = "Only @personal or @shared or @public roots can be modified"
        raise fastapi.HTTPException(status_code=400, detail=detail)

    # Check a file does not exist in the same path
    path2 = pathlib.Path(f"{path2}.b2")
    if path2.exists():
        detail = "File exists at the given path"
        raise fastapi.HTTPException(status_code=400, detail=detail)

    # Create the new notebook at the given path
    nb = nbformat.v4.new_notebook()
    file = io.StringIO()
    nbformat.write(nb, file)
    data = file.getvalue().encode()
    srv_utils.compress(data, dst=path2)

    return path


#
# HTML interface
#

if user_login_enabled():

    @app.get("/login", response_class=HTMLResponse)
    async def html_login(request: Request, user: db.User = Depends(optional_user)):
        if user:
            return RedirectResponse(settings.urlbase, status_code=307)

        context = {
            "user_register_enabled": user_register_enabled(),
        }
        return templates.TemplateResponse(request, "login.html", context)

    @app.get("/logout", response_class=HTMLResponse)
    async def html_logout(request: Request, user: db.User = Depends(optional_user)):
        if user:
            return RedirectResponse(settings.urlbase, status_code=307)

        return templates.TemplateResponse(request, "logout.html")

    @app.get("/forgot-password", response_class=HTMLResponse)
    async def html_forgot_password(request: Request, user: db.User = Depends(optional_user)):
        if user:
            return RedirectResponse(settings.urlbase, status_code=307)

        return templates.TemplateResponse(request, "forgot-password.html")

    @app.get("/forgot-password-ok", response_class=HTMLResponse)
    async def html_forgot_password_ok(request: Request):
        return templates.TemplateResponse(request, "forgot-password-ok.html")

    @app.get("/reset-password/{token}", response_class=HTMLResponse, name="html-reset-password")
    async def html_reset_password(request: Request, token: str, user: db.User = Depends(optional_user)):
        if user:
            return RedirectResponse(settings.urlbase, status_code=307)

        context = {"token": token}
        return templates.TemplateResponse(request, "reset-password.html", context)

    @app.post("/api/adduser/")
    async def add_user(
        payload: models.AddUserPayload,
        user: db.User = Depends(current_active_user),
    ):
        """
        Add a user.

        Parameters
        ----------
        payload : AddUserPayload
            The payload containing the username, password and whether the user is a superuser.

        Returns
        -------
        str
            A message indicating success.
        """
        if not user:
            raise srv_utils.raise_unauthorized("Adding a user requires authentication")
        if not user.is_superuser:
            srv_utils.raise_unauthorized("Only superusers can add users")

        # Get the number of current users
        users = await srv_utils.alist_users()
        # None or 0 means unlimited users
        if settings.maxusers and len(users) >= settings.maxusers:
            raise srv_utils.raise_bad_request(f"Only a maximum of {settings.maxusers} users are allowed")

        try:
            await srv_utils.aadd_user(
                payload.username,
                payload.password,
                payload.superuser,
                state_dir=settings.statedir,
            )
        except Exception as exc:
            error_message = str(exc) if str(exc) else exc.__class__.__name__
            raise srv_utils.raise_bad_request(
                f"Error in adding {payload.username}: {error_message}"
            ) from exc
        return f"User added: {payload}"

    @app.get("/api/deluser/{username}")
    async def del_user(
        username: str,
        user: db.User = Depends(current_active_user),
    ):
        """
        Delete a user.

        Parameters
        ----------
        username : str
            The username of the user to delete.

        Returns
        -------
        str
            A message indicating success.
        """
        if not user:
            raise srv_utils.raise_unauthorized("Deleting a user requires authentication")
        if not user.is_superuser:
            srv_utils.raise_unauthorized("Only superusers can delete users")

        try:
            users = await srv_utils.alist_users(username)
            await srv_utils.adel_user(username)
        except Exception as exc:
            error_message = str(exc) if str(exc) else exc.__class__.__name__
            raise srv_utils.raise_bad_request(f"Error in deleting {username}: {error_message}") from exc
        # Remove the personal directory of the user
        userid = str(users[0].id)
        print(f"User {username} with id {userid} has been deleted")
        shutil.rmtree(settings.personal / userid, ignore_errors=True)
        return f"User deleted: {username}"

    @app.get("/api/listusers/")
    async def list_users(
        username: str | None = None,
        user: db.User = Depends(current_active_user),
    ):
        """
        List all users or a specific user.

        Parameters
        ----------
        username : str or None
            The username of the user to list (optional).

        Returns
        -------
        list of dict
            A list of all users (as dictionaries).
        """
        if not user:
            raise srv_utils.raise_unauthorized("Listing users requires authentication")
        return await srv_utils.alist_users(username)

    # TODO: Support user verification


if user_register_enabled():

    @app.get("/register", response_class=HTMLResponse)
    async def html_register(request: Request, user: db.User = Depends(optional_user)):
        if user:
            return RedirectResponse(settings.urlbase, status_code=307)

        return templates.TemplateResponse(request, "register.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BASE_DIR / "static/logo-caterva2-16x16.png")


@app.get("/", response_class=HTMLResponse)
@app.get("/roots/{path:path}")
async def html_home(
    request: Request,
    path: str = "",
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = "",
    # Dependencies
    user: db.User = Depends(optional_user),
):
    # Disk usage
    size = get_disk_usage()
    context = {
        "user_login_enabled": user_login_enabled(),
        "roots_url": make_url(request, "htmx_root_list", {"roots": roots}),
        "username": user.email if user else None,
        # Disk usage
        "usage_total": custom_filesizeformat(size),
    }

    context["config"] = {}

    if settings.quota:
        context["usage_quota"] = custom_filesizeformat(settings.quota)
        context["usage_percent"] = round((size / settings.quota) * 100)

    if roots:
        paths_url = make_url(request, "htmx_path_list", {"roots": roots, "search": search})
        context["paths_url"] = paths_url

    if path:
        context["meta_url"] = make_url(request, "htmx_path_info", path=path)

    return templates.TemplateResponse(request, "home.html", context)


@app.get("/htmx/root-list/")
async def htmx_root_list(
    request: Request,
    # Query
    roots: list[str] = fastapi.Query([]),
    # Depends
    user: db.User = Depends(optional_user),
):
    context = {
        "checked": roots,
        "roots": sorted(settings.database.roots.values(), key=lambda x: x.name),
        "user": user,
    }
    return templates.TemplateResponse(request, "root_list.html", context)


def _get_rootdir(user, root):
    if root == "@personal":
        if user:
            return settings.personal / str(user.id)
    elif root == "@shared":
        if user:
            return settings.shared
    elif root == "@public":
        return settings.public
    else:
        if not get_root(root).subscribed:
            follow(root)
        return settings.cache / root

    return None


@app.get("/htmx/path-list/", response_class=HTMLResponse)
async def htmx_path_list(
    request: Request,
    # Query parameters
    roots: list[str] = fastapi.Query([]),
    search: str = "",
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    hx_trigger: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(optional_user),
):
    hx_current_url = furl.furl(hx_current_url)

    # Prepare datasets context
    def get_names():
        n = 1
        while True:
            for name in itertools.product(*[string.ascii_lowercase] * n):
                yield "".join(name)
            n += 1

    names = get_names()

    datasets = []
    query = {"roots": roots, "search": search}

    def add_dataset(path, abspath):
        datasets.append(
            {
                "name": "_",
                "path": path,
                "size": abspath.stat().st_size,
                "url": make_url(request, "html_home", path=path, query=query),
                "label": truncate_path(path),
            }
        )

    for root in roots:
        rootdir = _get_rootdir(user, root)
        for abspath, relpath in utils.walk_files(rootdir):
            if relpath.suffix == ".b2":
                relpath = relpath.with_suffix("")
            path = f"{root}/{relpath}"
            if search in path:
                add_dataset(path, abspath)

    # Add current path if not already in the list
    current_path = hx_current_url.path
    segments = current_path.segments
    if segments and segments[0] == "roots":
        path = str(pathlib.Path(*segments[1:]))
        for dataset in datasets:
            if dataset["path"] == path:
                break
        else:
            root = segments[1]
            rootdir = _get_rootdir(user, root)
            relpath = pathlib.Path(*segments[2:])
            abspath = rootdir / relpath
            if abspath.suffix not in {".b2", ".b2nd", ".b2frame"}:
                abspath = pathlib.Path(f"{abspath}.b2")

            add_dataset(path, abspath)

    # Assign names to datasets
    datasets = sorted(datasets, key=lambda x: x["path"])
    for dataset in datasets:
        dataset["name"] = next(names)

    # Render template
    cmd_url = make_url(request, "htmx_command")
    search_url = make_url(request, "htmx_path_list", {"roots": roots})
    context = {
        "datasets": datasets,
        "search_text": search,
        "search_url": search_url,
        "cmd_url": cmd_url,
        "user": user,
        "commands": commands_list,
    }
    response = templates.TemplateResponse(request, "path_list.html", context)

    # Push URL only when clicked, not on load/reload
    if hx_trigger != "path-list":
        args = {"roots": roots}
        if search:
            args["search"] = search
        push_url = hx_current_url.set(args).url
        response.headers["HX-Push-Url"] = push_url

    return response


@app.get("/htmx/path-info/{path:path}", response_class=HTMLResponse)
async def htmx_path_info(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    hx_trigger: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(optional_user),
):
    # Used to deselect
    if len(path.parts) == 0:
        response = HTMLResponse("")
        push_url = make_url(request, "html_home")
        # Keep query
        current_query = furl.furl(hx_current_url).query
        if current_query:
            push_url = f"{push_url}?{current_query.encode()}"

        response.headers["HX-Push-Url"] = push_url
        return response

    # Read metadata
    abspath, _ = abspath_and_dataprep(path, user=user)
    try:
        meta = srv_utils.read_metadata(
            abspath, settings.cache, settings.personal, settings.shared, settings.public
        )
    except FileNotFoundError as exc:
        return htmx_error(request, f"FileNotFoundError: {exc} (missing operand?)")

    vlmeta = getattr(getattr(meta, "schunk", meta), "vlmeta", {})
    contenttype = vlmeta.get("contenttype") or guess_dset_ctype(path, meta)
    plugin = plugins.get(contenttype)
    if plugin:
        display = {
            "url": url(f"plugins/{plugin.name}/display/{path}"),
            "label": plugin.label,
        }
    else:
        mimetype = guess_type(path)
        if mimetype and (
            mimetype in {"application/pdf", "application/x-ipynb+json", "text/markdown"}
            or mimetype.startswith("image/")
        ):
            display = {
                "url": url(f"display/{path}"),
                "label": "Display",
            }
        else:
            display = None

    context = {
        "path": path,
        "meta": meta,
        "display": display,
        "can_delete": user and path.parts[0] in {"@personal", "@shared", "@public"},
    }

    # XXX
    if hasattr(meta, "shape"):
        view_url = make_url(request, "htmx_path_view", path=path)
        context.update(
            {
                "view_url": view_url,
                "shape": meta.shape,
            }
        )

    response = templates.TemplateResponse(request, "info.html", context=context)

    # Push URL only when clicked, not on load/reload
    if hx_trigger != "meta":
        push_url = make_url(request, "html_home", path=path)
        # Keep query
        current_query = furl.furl(hx_current_url).query
        if current_query:
            push_url = f"{push_url}?{current_query.encode()}"

        response.headers["HX-Push-Url"] = push_url

    return response


# Global dictionary to store objects
@functools.lru_cache(maxsize=16)
def get_filtered_array(abspath, path, filter, sortby):
    arr = open_b2(abspath, path)
    has_ndfields = hasattr(arr, "fields") and arr.fields != {}
    assert has_ndfields
    idx = None
    sortby = sortby.strip() if sortby else None

    # Filter rows only for NDArray with fields
    if filter:
        # Let's create a LazyExpr with the filter
        larr = arr[filter]
        # TODO: do some benchmarking to see if this is worth it
        idx = larr.indices(sortby).compute()
        # TODO: do some benchmarking to see if a numpy array is faster
        # but be aware that this will consume more memory (uncompressed)
        # idx = larr.indices(sortby)[:]
        arr = larr.sort(sortby).compute()
    elif sortby:
        # NDArray with fields; no need for the compute step
        idx = arr.indices(sortby)
        arr = arr.sort(sortby)

    return arr, idx


@app.post("/htmx/path-view/{path:path}", response_class=HTMLResponse)
async def htmx_path_view(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Input parameters
    index: typing.Annotated[list[int] | None, Form()] = None,
    sizes: typing.Annotated[list[int] | None, Form()] = None,
    fields: typing.Annotated[list[str] | None, Form()] = None,
    filter: typing.Annotated[str, Form()] = "",
    sortby: typing.Annotated[str, Form()] = "",
    # Depends
    user: db.User = Depends(optional_user),
):
    abspath, _ = abspath_and_dataprep(path, user=user)
    filter = filter.strip()
    if filter or sortby:
        try:
            arr, idx = get_filtered_array(abspath, path, filter, sortby)
        except TypeError as exc:
            return htmx_error(request, f"Error in filter: {exc}")
        except NameError as exc:
            return htmx_error(request, f"Unknown field: {exc}")
        except ValueError as exc:
            return htmx_error(request, f"ValueError: {exc}")
        except SyntaxError as exc:
            return htmx_error(request, f"SyntaxError: {exc}")
        except IndexError as exc:
            return htmx_error(request, f"IndexError: {exc}")
        except AttributeError as exc:
            return htmx_error(
                request,
                f"Invalid filter: {exc}." f" Only expressions can be used as filters, not field names.",
            )
    else:
        arr = open_b2(abspath, path)
        idx = None

    # Local variables
    shape = arr.shape
    ndims = len(shape)

    # Set of dimensions that define the window
    # TODO Allow the user to choose the window dimensions
    has_ndfields = hasattr(arr, "fields") and arr.fields != {}
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
    for i, (start, size, size_max) in enumerate(zip(index, sizes, shape, strict=False)):
        mod = size_max % size
        start_max = size_max - (mod or size)
        inputs.append(
            {
                "start": start,
                "start_max": start_max,
                "size": size,
                "size_max": size_max,
                "with_size": i in view_dims,
            }
        )
        if inputs[-1]["with_size"]:
            stop = min(start + size, size_max)
            if idx is None:
                tags.append(list(range(start, stop)))
            else:
                tags.append(list(idx[start:stop]))

    if has_ndfields:
        cols = list(arr.fields.keys())
        fields = fields or cols[:5]
        idxs = [cols.index(f) for f in fields]
        rows = [fields]

        # Get array view
        if ndims >= 2:
            arr = arr[index[:-1]]
            i, isize = index[-1], sizes[-1]
            arr = arr[i : i + isize]
            arr = arr.tolist()
        elif ndims == 1:
            i, isize = index[0], sizes[0]
            arr = arr[i : i + isize]
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
            arr = arr[i : i + isize, j : j + jsize]
            rows = [tags[-1]] + list(arr)
        elif ndims == 1:
            i, isize = index[0], sizes[0]
            arr = [arr[i : i + isize]]
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
        "filter": filter,
        "sortby": sortby,
        "shape": shape,
        "tags": tags if len(tags) == 0 else tags[0],
    }
    return templates.TemplateResponse(request, "info_view.html", context)


class AddUserCmd:
    """Add a new user."""

    names = ("adduser",)
    expected = "adduser <username>"
    nargs = 2

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        payload = models.AddUserPayload(username=argv[1], password=None, superuser=False)
        message = await add_user(payload, user)
        return htmx_message(request, message)


class DelUserCmd:
    """Remove user."""

    names = ("deluser",)
    expected = "deluser <username>"
    nargs = 2

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        message = await del_user(argv[1], user)
        return htmx_message(request, message)


class ListUsersCmd:
    """List users."""

    names = ("lsu", "listusers")
    expected = "lsu/listusers"
    nargs = 1

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        lusers = await list_users()
        users = [user.email for user in lusers]
        return htmx_message(request, f"Users: {users}")


class CopyCmd:
    """Copy file."""

    names = ("cp", "copy")
    expected = "cp/copy <src> <dst>"
    nargs = 3

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        src, dst = operands.get(argv[1], argv[1]), operands.get(argv[2], argv[2])
        payload = models.MoveCopyPayload(src=src, dst=dst)
        result_path = await copy(payload, user)
        # Redirect to display new dataset
        result_path = await display_first(result_path, user)
        url = make_url(request, "html_home", path=result_path)
        return htmx_redirect(hx_current_url, url)


class MoveCmd:
    """Move or rename file."""

    names = ("mv", "move")
    expected = "mv/move <src> <dst>"
    nargs = 3

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        src, dst = operands.get(argv[1], argv[1]), operands.get(argv[2], argv[2])
        payload = models.MoveCopyPayload(src=src, dst=dst)
        result_path = await move(payload, user)
        # Redirect to display new dataset
        result_path = await display_first(result_path, user)
        url = make_url(request, "html_home", path=result_path)
        return htmx_redirect(hx_current_url, url)


class RemoveCmd:
    """Remove file."""

    names = ("rm", "remove")
    expected = "rm/remove <path>"
    nargs = 2

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        path = operands.get(argv[1], argv[1])
        path = pathlib.Path(path)
        await remove(path, user)
        # Nothing to show after removing


class AddNotebookCmd:
    """Add a new notebook."""

    names = ("addnb",)
    expected = "addnb <path>"
    nargs = 2

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        path = pathlib.Path(argv[1])
        path = await add_notebook(path, user)

        # Redirect to display new dataset
        url = make_url(request, "html_home", path=path)
        return htmx_redirect(hx_current_url, url)


commands_list = [AddUserCmd, DelUserCmd, ListUsersCmd, CopyCmd, MoveCmd, RemoveCmd, AddNotebookCmd]

commands = {}
for cmd in commands_list:
    for name in cmd.names:
        if name in commands:
            raise ValueError(f'duplicated "{name}" command')
        commands[name] = cmd


@app.post("/htmx/command/", response_class=HTMLResponse)
async def htmx_command(
    request: Request,
    # Body
    command: typing.Annotated[str, Form()],
    names: typing.Annotated[list[str] | None, Form()] = None,
    paths: typing.Annotated[list[str] | None, Form()] = None,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):
    if names is None:
        names = []

    if paths is None:
        paths = []

    operands = dict(zip(names, paths, strict=False))
    argv = command.split()

    # First check for expressions
    nargs = len(argv)
    if nargs == 0:
        return responses.Response(status_code=204)

    elif nargs > 1 and argv[1] in {"=", ":="}:
        operator = argv[1]
        lazy = operator == "="
        try:
            result_name, expr = command.split(operator, maxsplit=1)
            make_expr(result_name, expr, operands, user, lazy=lazy)
        except SyntaxError:
            return htmx_error(request, "Invalid syntax: expected <varname> = <expression>")
        except ValueError as exc:
            return htmx_error(request, f"Invalid expression: {exc}")
        except TypeError as exc:
            return htmx_error(request, f"Invalid expression: {exc}")
        except KeyError as exc:
            error = f"Expression error: {exc.args[0]} is not in the list of available datasets"
            return htmx_error(request, error)
        except RuntimeError as exc:
            return htmx_error(request, f"Runtime error: {exc}")

    # Commands
    cmd = commands.get(argv[0])
    if cmd is not None:
        if len(argv) != cmd.nargs:
            return htmx_error(request, f"Invalid syntax: expected {cmd.expected}")

        try:
            return await cmd.call(request, user, argv, operands, hx_current_url)
        except Exception as exc:
            return htmx_error(request, f'Error in "{command}" command: {exc.detail}')

    # If the command is not recognized
    return htmx_error(request, f'Invalid command "{argv[0]}" or expression not found')


async def display_first(result_path, user):
    paths = await get_list(pathlib.Path(result_path), user)
    if len(paths) > 1:
        # Display the first path found
        result_path = f"{result_path}/{paths[0]}"
    elif len(paths) == 1 and not result_path.endswith(paths[0]):
        result_path = f"{result_path}/{paths[0]}"
    return result_path


def htmx_message(request, msg):
    context = {"message": msg}
    return templates.TemplateResponse(request, "message.html", context, status_code=400)


def htmx_error(request, msg, status_code=400):
    context = {"error": msg}
    return templates.TemplateResponse(request, "error.html", context, status_code=status_code)


def htmx_redirect(current_url, target_url, root=None):
    response = responses.JSONResponse("OK")
    query = furl.furl(current_url).query
    roots = query.params.getlist("roots")

    if root and root not in roots:
        query = query.add({"roots": root})

    response.headers["HX-Redirect"] = f"{target_url}?{query.encode()}"
    return response


@app.post("/htmx/upload/{name}")
async def htmx_upload(
    request: Request,
    name: str,
    # Body
    file: UploadFile,
    # Headers
    hx_current_url: srv_utils.HeaderType = None,
    # Depends
    user: db.User = Depends(current_active_user),
):
    if not user:
        raise srv_utils.raise_unauthorized("Uploading files requires authentication")

    if name == "@personal":
        path = settings.personal / str(user.id)
    elif name == "@shared":
        path = settings.shared
    elif name == "@public":
        path = settings.public
    else:
        raise fastapi.HTTPException(status_code=404)  # NotFound

    # Read the file and check quota
    data = await file.read()
    if settings.quota:
        total_size = get_disk_usage() + len(data)
        if total_size > settings.quota:
            error = "Upload failed because quota limit has been exceeded."
            return htmx_error(request, error)

    path.mkdir(exist_ok=True, parents=True)
    filename = pathlib.Path(file.filename)

    # If a tarball or zipfile, extract the files in path
    # We also filter out hidden files and MacOSX metadata
    suffix = filename.suffix
    suffixes = filename.suffixes[-2:]
    if suffix in [".tar", ".tgz", ".zip"] or suffixes == [".tar", ".gz"]:
        file.file.seek(0)  # Reset file pointer
        if suffix == ".zip":
            with zipfile.ZipFile(file.file, "r") as archive:
                members = [
                    m
                    for m in archive.namelist()
                    if (
                        not os.path.basename(m).startswith(".")
                        and not os.path.basename(m).startswith("__MACOSX")
                    )
                ]
                archive.extractall(path, members=members)
                # Convert members elements to Path instances
                members = [pathlib.Path(m) for m in members]
        else:
            mode = "r:gz" if suffix in {".tgz", ".gz"} else "r"
            with tarfile.open(fileobj=file.file, mode=mode) as archive:
                members = [
                    m
                    for m in archive.getmembers()
                    if (
                        not os.path.basename(m.name).startswith(".")
                        and not os.path.basename(m.name).startswith("__MACOSX")
                    )
                ]
                archive.extractall(path, members=members)
                # Convert members elements to Path instances
                members = [pathlib.Path(m.name) for m in members]

        # Compress files that are not compressed yet
        new_members = [
            member
            for member in members
            if not (path / member).is_dir() and member.suffix not in {".b2", ".b2frame", ".b2nd"}
        ]
        for member in new_members:
            srv_utils.compress_file(path / member)

        # We are done, redirect to home, and show the new files, starting with the first one
        first_member = next((m for m in new_members), None)
        path = f"{name}/{first_member}"
        return htmx_redirect(hx_current_url, make_url(request, "html_home", path=path), root=name)

    if filename.suffix not in {".b2", ".b2frame", ".b2nd"}:
        schunk = blosc2.SChunk(data=data)
        data = schunk.to_cframe()
        filename = f"{filename}.b2"

    # Save file
    with open(path / filename, "wb") as dst:
        dst.write(data)

    # Redirect to display new dataset
    path = f"{name}/{filename}"
    if path.endswith(".b2"):
        path = path[:-3]
    url = make_url(request, "html_home", path=path)
    return htmx_redirect(hx_current_url, url, root=name)


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
    # Find absolute path to file
    parts = list(path.parts)
    name = parts[0]
    if name == "@personal":
        parts[0] = str(user.id)
        path = pathlib.Path(*parts)
        abspath = settings.personal / path
    elif name == "@shared":
        path = pathlib.Path(*parts[1:])
        abspath = settings.shared / path
    elif name == "@public":
        path = pathlib.Path(*parts[1:])
        abspath = settings.public / path
    else:
        return fastapi.HTTPException(status_code=400)

    # Remove
    if abspath.suffix not in {".b2frame", ".b2nd"}:
        abspath = abspath.with_suffix(abspath.suffix + ".b2")
        if not abspath.exists():
            return fastapi.HTTPException(status_code=404)
    abspath.unlink()

    # Redirect to home
    url = make_url(request, "html_home")
    return htmx_redirect(hx_current_url, url, root=name)


async def get_container(path, user):
    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()
    return open_b2(abspath, path)


async def get_file_content(path, user):
    container = await get_container(path, user)

    # Return compressed (this is used only by the downloader)
    if path.suffix in {".b2frame", ".b2nd"}:
        return container.to_cframe()

    # Return uncompressed
    return container[:]


async def get_image(path, user):
    content = await get_file_content(path, user)
    return PIL.Image.open(io.BytesIO(content))


def resize_image(img, width):
    if width and img.width > width:
        height = (img.height * width) // img.width
        img = img.resize((width, height))

    img_file = io.BytesIO()
    img.save(img_file, format="PNG")
    img_file.seek(0)
    return img_file


@app.get("/display/{path:path}", response_class=HTMLResponse)
async def html_display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
    # Response
    response_class=HTMLResponse,
):
    mimetype = guess_type(path)
    if mimetype == "application/pdf":
        data = f"{url('api/preview/')}{path}"
        return f'<object data="{data}" type="application/pdf" class="w-100" style="height: 768px"></object>'
    elif mimetype == "application/x-ipynb+json":
        href = url(f"static/jupyterlite/notebooks/index.html?path={path}")
        src = f"{url('api/preview/')}{path}"
        return (
            f'<a href="{href}" target="_blank" class="btn btn-primary mb-1"><i class="fa-solid fa-gear"></i> Run</a>'
            f'<iframe src="{src}" class="w-100" height="768px"></iframe>'
        )
    elif mimetype == "text/markdown":
        content = await get_file_content(path, user)
        return markdown.markdown(content.decode("utf-8"))
    elif mimetype.startswith("image/"):
        src = f"{url('api/preview/')}{path}"
        img = await get_image(path, user)

        width = 768  # Max size
        links = []
        if img.width > width:
            links.append(
                {
                    "href": src,
                    "label": f"{img.width} x {img.height} (original size)",
                    "target": "blank_",
                }
            )
            src = f"{src}?{width=}"

        context = {"src": src, "links": links}
        return templates.TemplateResponse(request, "display_image.html", context=context)

    return "Format not supported"


#
# For Jupyterlite
#


@app.get("/static/jupyterlite/api/contents/{path:path}")
async def jupyterlite_contents(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
):
    """
    See https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html#get--api-contents-path
    """

    # The path must end with all.json
    parts = path.parts
    if parts[-1] != "all.json":
        raise fastapi.HTTPException(status_code=404)  # NotFound

    parts = parts[:-1]
    path = pathlib.Path(*parts)

    # Helper function for directories
    def directory(abspath, relpath, content=None):
        stat = abspath.stat()
        return {
            "content": content,
            "created": utils.epoch_to_iso(stat.st_ctime),
            "format": None if content is None else "json",
            "hash": None,
            "hash_algorithm": None,
            "last_modified": utils.epoch_to_iso(stat.st_mtime),
            "mimetype": None,
            "name": pathlib.Path(relpath).name,
            "path": relpath,
            "size": None,
            "type": "directory",
            "writable": False,
        }

    content = []
    if len(parts) == 0:
        rootdir = _get_rootdir(user, "@personal")
        if rootdir is not None:
            rootdir.mkdir(exist_ok=True)
            content.append(directory(rootdir, "@personal"))

        rootdir = _get_rootdir(user, "@shared")
        if rootdir is not None:
            content.append(directory(rootdir, "@shared"))

        rootdir = _get_rootdir(user, "@public")
        if rootdir is not None:
            content.append(directory(rootdir, "@public"))

        # TODO pub/sub roots: settings.database.roots.values()
        dir_abspath = rootdir.parent
        dir_relpath = ""
    else:
        # Check access to the root
        root, *subpath = parts
        rootdir = _get_rootdir(user, root)
        if rootdir is None:
            raise fastapi.HTTPException(status_code=404)  # NotFound

        # Get absolute and relative paths to the directory
        dir_abspath = rootdir / pathlib.Path(*subpath)
        dir_relpath = path

        for abspath, relpath in utils.iterdir(dir_abspath):
            relpath = path / relpath
            if abspath.is_dir():
                content.append(directory(abspath, relpath))
            elif abspath.is_file():
                if relpath.suffix == ".b2":
                    relpath = relpath.with_suffix("")

                mimetype = guess_type(relpath)
                if mimetype == "application/x-ipynb+json":
                    content_type = "notebook"
                    writable = False
                else:
                    content_type = "file"
                    writable = False

                stat = abspath.stat()
                content.append(
                    {
                        "content": None,
                        "created": utils.epoch_to_iso(stat.st_ctime),
                        "format": None,
                        "hash": None,
                        "hash_algorithm": None,
                        "last_modified": utils.epoch_to_iso(stat.st_mtime),
                        "mimetype": mimetype,
                        "name": relpath.name,
                        "path": relpath,
                        "size": stat.st_size,  # XXX Return the uncompressed size?
                        "type": content_type,
                        "writable": writable,
                    }
                )
            else:
                raise NotImplementedError("Only directories and files are supported")

    return directory(dir_abspath, dir_relpath, content=content)


@app.get("/static/jupyterlite/files/{path:path}")
async def jupyterlite_files(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
):
    async def downloader():
        yield await get_file_content(path, user)

    mimetype = guess_type(path)
    return responses.StreamingResponse(downloader(), media_type=mimetype)


@app.get("/service-worker.js")
async def jupyterlite_worker(
    # Query parameters
    enableCache: bool | None = None,
):
    abspath = BASE_DIR / "static/jupyterlite/service-worker.js"
    return FileResponse(abspath, filename=abspath.name, media_type="application/javascript")


@app.get("/api/service-worker-heartbeat", response_class=responses.PlainTextResponse)
async def jupyter_heartbeat():
    return "ok"


#
# Static
#

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


#
# Command line interface
#

plugins = {}


def guess_dset_ctype(path: pathlib.Path, meta) -> str | None:
    """Try to guess dataset's content type (given path and metadata)."""
    for ctype, plugin in plugins.items():
        if hasattr(plugin, "guess") and plugin.guess(path, meta):
            return ctype
    return None


def main():
    # Read configuration file
    conf = utils.get_conf("subscriber", allow_id=True)

    # Parse command line arguments
    _stdir = "_caterva2/sub" + (f".{conf.id}" if conf.id else "")
    parser = utils.get_parser(
        broker=conf.get("broker.http", ""),
        http=conf.get(".http", "localhost:8002"),
        loglevel=conf.get(".loglevel", "warning"),
        statedir=conf.get(".statedir", _stdir),
        id=conf.id,
    )
    args = utils.run_parser(parser)
    settings.broker = args.broker

    # Init cache
    settings.statedir = args.statedir.resolve()
    settings.cache = settings.statedir / "cache"
    settings.cache.mkdir(exist_ok=True, parents=True)
    # Use `download_cached()`, `StaticFiles` does not support authorization.
    # app.mount("/files", StaticFiles(directory=cache), name="files")

    # Shared/Public dirs
    settings.shared = settings.statedir / "shared"
    settings.shared.mkdir(exist_ok=True, parents=True)
    settings.public = settings.statedir / "public"
    settings.public.mkdir(exist_ok=True, parents=True)

    # personal dir
    settings.personal = settings.statedir / "personal"
    settings.personal.mkdir(exist_ok=True, parents=True)
    # Use `download_personal()`, `StaticFiles` does not support authorization.
    # app.mount("/personal", StaticFiles(directory=settings.personal), name="personal")

    # Init database
    model = models.Subscriber(roots={}, etags={})
    settings.database = srv_utils.Database(settings.statedir / "db.json", model)

    # Register display plugins
    from .plugins import tomography  # delay module load

    app.mount(f"/plugins/{tomography.name}", tomography.app)
    plugins[tomography.contenttype] = tomography
    tomography.init(abspath_and_dataprep, settings.urlbase)

    # Run
    root_path = str(furl.furl(settings.urlbase).path)
    srv_utils.uvicorn_run(app, args, root_path=root_path)


if __name__ == "__main__":
    main()
