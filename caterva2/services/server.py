###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import ast
import asyncio
import builtins
import collections.abc
import contextlib
import functools
import io
import itertools
import json
import linecache
import mimetypes
import os
import pathlib
import shutil
import string
import tarfile
import threading
import time
import traceback
import types
import typing
import uuid
import weakref
import zipfile

# Requirements
import blosc2

if blosc2._HAS_NUMBA:
    import numba
import dotenv
import fastapi
import furl
import httpx
import markdown
import nbconvert
import nbformat
import numpy as np
import PIL.Image
import pydantic
import pygments
import uvicorn
from blosc2 import linalg_funcs_list as linalg_funcs

# FastAPI
from fastapi import Depends, FastAPI, Form, Request, Response, UploadFile, concurrency, responses
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import MutableHeaders

# Project
from caterva2 import hdf5, models, utils
from caterva2.services import db, providers, remote_proxy, schemas, settings, srv_utils, users
from caterva2.services.notebook import inject_pyodide_bootstrap_cell

BASE_DIR = pathlib.Path(__file__).resolve().parent

# Set CATERVA2_SECRET=XXX in .env file in working directory
dotenv.load_dotenv()


# State
# Per-array locks, held weakly, so the table holds the locks in use and nothing
# else: a strong dict grows one entry per distinct array ever written, and never
# loses one, for as long as the server runs.
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def dataset_lock(abspath) -> asyncio.Lock:
    """Serialize the writes to one array within this process.

    Keyed on where the array is stored and not on the path it was asked for:
    `@personal/run.b2nd` is a name every user spells the same way and no two of
    them share (see `publish_key`), so a lock keyed on the request path makes
    every user's chunk writes wait on every other user's writes to a different
    file.

    Weak is safe because every caller takes the lock in an `async with` on this
    call's result: whoever holds it, and whoever waits on it, is a strong
    reference to it for as long as that matters.  A lock nobody holds has no
    state worth keeping.
    """
    key = str(abspath)
    lock = _locks.get(key)
    if lock is None:
        # Bound to a name first: the table's own reference is weak, so an
        # unnamed lock could be collected before it is returned
        lock = asyncio.Lock()
        lock = _locks.setdefault(key, lock)
    return lock


_thread_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_thread_locks_guard = threading.Lock()


def dataset_thread_lock(abspath) -> threading.Lock:
    """Serialize threadpool writes on one array within this process.

    Python-Blosc2's file locking (`locking=True`) executes in C without releasing
    the GIL during open/lock. If two threads in the same process attempt to
    open/lock the same file concurrently, one can block on the OS file lock
    while holding Python's GIL, deadlocking the process. Guarding operations on
    the same array with a threading.Lock ensures the waiting thread yields the
    GIL cleanly.
    """
    key = str(abspath)
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


mimetypes.add_type("text/markdown", ".md")  # Because in macOS this is not by default
mimetypes.add_type("application/x-ipynb+json", ".ipynb")

ncores = os.cpu_count() // 2


def guess_type(path):
    mimetype, _ = mimetypes.guess_type(path)
    return mimetype


def get_disk_usage():
    exclude = {"db.json", "db.sqlite"}
    return sum(
        path.stat().st_size
        for path, _ in srv_utils.walk_files(settings.statedir, exclude=exclude, include_internal=True)
    )


DISK_USAGE_TTL = 10.0
"""How long a walk of the state directory is reused before another is made."""

_disk_usage = {"walked_at": 0.0, "walked": 0, "written": 0}


def get_disk_usage_written(pending: int) -> int:
    """What the state directory holds, for a check made once per chunk written.

    `get_disk_usage` stats every file under the state directory.  That is a fair
    price for an upload, which happens once; a fill writes a chunk at a time and
    would pay it per chunk, so the walk that costs the most in the array's size
    would run the most often on the largest arrays -- an array of ten thousand
    chunks is ten thousand walks.

    The walk is kept for `DISK_USAGE_TTL` and the chunks written since are added
    to it, so the answer only ever *over*states what is on disk: a write this
    process made is counted from the moment it is made, and a file removed
    meanwhile is still counted until the next walk.  A quota can therefore bite
    slightly early, never slightly late, which is the direction to be wrong in.
    """
    now = time.time()
    if now - _disk_usage["walked_at"] > DISK_USAGE_TTL:
        _disk_usage.update(walked_at=now, walked=get_disk_usage(), written=0)
    return _disk_usage["walked"] + _disk_usage["written"] + pending


def account_chunk_written(nbytes: int) -> None:
    """Count a chunk just written against the kept walk; see `get_disk_usage_written`."""
    _disk_usage["written"] += nbytes


def remote_proxy_cache_limit(proxy: remote_proxy.ServerRemoteProxy) -> int | None:
    """Return the cache allowance; quota-enabled servers consume caches read-only.

    Payload limits cannot reserve physical metadata growth, and per-dataset locks
    do not coordinate other carriers, uploads, or workers. Until all writers share
    a physical-storage reservation mechanism, automatic fills must not grow disk
    usage under a customer quota. Zero still permits reuse of warm carrier data.
    """
    if proxy.cache_policy != "disk":
        return 0
    if not settings.quota:
        return proxy.max_cache_bytes
    return 0


async def read_remote_proxy(proxy, item, abspath):
    """Read one remote selection while serializing and accounting cache mutation."""
    lock = dataset_lock(abspath)
    async with lock:
        before = abspath.stat().st_size
        cache_limit = remote_proxy_cache_limit(proxy)
        data = await concurrency.run_in_threadpool(
            lambda: blosc2.asarray(proxy.read(item, cache_limit=cache_limit)).to_cframe()
        )
        if settings.quota:
            growth = max(0, abspath.stat().st_size - before)
            if growth:
                account_chunk_written(growth)
        return data


def truncate_path(path, size=35):
    """
    Smart truncation of a long path for display.
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


def open_b2(abspath, path):
    """
    Open a Blosc2 dataset.

    Return a HDF5Proxy or a LazyExpr or Blosc2 container.
    """
    root = pathlib.Path(path).parts[0]
    if root not in {"@personal", "@shared", "@public"}:
        raise ValueError(f"Unexpected root={root}")

    reference = remote_proxy.inspect(abspath)
    if reference is not None:
        carrier, payload = reference
        try:
            return remote_proxy.resolve(carrier, payload)
        except remote_proxy.RemoteProxyDenied as exc:
            raise fastapi.HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        remote_proxy.guard_embedded(abspath)
    except remote_proxy.RemoteProxyDenied as exc:
        raise fastapi.HTTPException(status_code=403, detail=str(exc)) from exc
    container = blosc2.open(abspath)
    # CTable has its own storage and no table-level cparams/dparams; return early.
    if isinstance(container, blosc2.CTable):
        return container
    vlmeta = container.schunk.vlmeta if hasattr(container, "schunk") else container.vlmeta
    if isinstance(container, blosc2.LazyArray):
        # Open the operands properly
        operands = container.operands if isinstance(container, blosc2.LazyExpr) else container.inputs_dict
        for key, value in operands.items():
            if value is None:
                raise ValueError(f'Missing operand "{key}"')
            if isinstance(value, blosc2.Operand):
                metaval = value.schunk.meta if hasattr(value, "schunk") else {}
                vlmetaval = value.schunk.vlmeta
                if "proxy-source" in metaval or ("_ftype" in vlmetaval and vlmetaval["_ftype"] == "hdf5"):
                    # Save operand as Proxy, see blosc2.open doc for more info.
                    # Or, it can be an HDF5 dataset too (which should be handled in the next call)
                    relpath = srv_utils.get_relpath(value)
                    operands[key] = open_b2(value.schunk.urlpath, relpath)
            else:
                if value.shape != ():
                    raise ValueError("Something has gone wrong. Non-blosc2.Operands should be scalars.")

        if not hasattr(container, "_where_args"):
            # If the container does not have _where_args, it is a LazyExpr
            # and we can return it directly.
            return container

        # Repeat the operation for where args (for properly handling proxies)
        where_args = container._where_args
        for key, value in where_args.items():
            if value is None:
                raise ValueError(f'Missing operand "{key}"')
            metaval = value.schunk.meta if hasattr(value, "schunk") else {}
            vlmetaval = value.schunk.vlmeta if hasattr(value, "schunk") else {}
            if "proxy-source" in metaval or ("_ftype" in vlmetaval and vlmetaval["_ftype"] == "hdf5"):
                relpath = srv_utils.get_relpath(value)
                value = open_b2(value.schunk.urlpath, relpath)
                where_args[key] = value
            elif isinstance(value, blosc2.LazyExpr):
                # Properly open the operands (to e.g. find proxies)
                for opkey, opvalue in value.operands.items():
                    if isinstance(opvalue, blosc2.LazyExpr):
                        continue
                    relpath = srv_utils.get_relpath(opvalue)
                    value.operands[opkey] = open_b2(opvalue.schunk.urlpath, relpath)

        return container

    # Check if this is a file of a special type
    elif "_ftype" in vlmeta and vlmeta["_ftype"] == "hdf5":
        container = hdf5.HDF5Proxy(container)
    # Set the number of threads for compression and decompression
    container.cparams.nthreads = ncores
    container.dparams.nthreads = ncores
    return container


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
    try:
        from . import plugins  # When used as a module
    except ImportError:
        import plugins  # When used as a script

    # These need to be available for plugins at import time.
    plugins.current_active_user = current_active_user


_setup_plugin_globals()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the (users) database
    if user_login_enabled():
        await db.create_db_and_tables(settings.statedir)

    # Peer identity (Caterva3): a stable UUID for this server instance.
    idfile = settings.statedir / "peer_id"
    if not idfile.exists():
        idfile.write_text(uuid.uuid4().hex)
    settings.peer_id = idfile.read_text().strip()

    for p in providers.active:
        await p.startup()

    yield

    for p in providers.active:
        await p.shutdown()

    # Clean up the database engine on shutdown
    if user_login_enabled() and db.engine is not None:
        await db.engine.dispose()


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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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


def url_with_query(request, hx_current_url=None, **extra_params):
    """
    Build a URL with updated query parameters.

    Uses hx_current_url if available (HTMX), otherwise falls back to request.url.
    """
    base_url = hx_current_url or str(request.url)
    f = furl.furl(base_url)

    # Update or remove query params
    for key, value in extra_params.items():
        if value is None:
            f.query.params.pop(key, None)  # remove if present
        else:
            # Normalize boolean: True → '1', False → '0'
            if isinstance(value, bool):
                value = "1" if value else "0"
            f.query.params[key] = value

    return f.url


def brand_logo():
    path = "media/logo.webp"
    if not (settings.statedir / path).exists():
        path = "static/logo-caterva2-horizontal-small.webp"

    return url(path)


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["filesizeformat"] = custom_filesizeformat
templates.env.globals["url"] = url
templates.env.globals["url_with_query"] = url_with_query


# Add CSS/JS to templates namespace
BUILD_DIR = "static/build/"
with (BASE_DIR / BUILD_DIR / "manifest.json").open() as file:
    manifest = json.load(file)
    entry = manifest["src/main.js"]
    templates.env.globals["main_css"] = url(BUILD_DIR + entry["css"][0])
    templates.env.globals["main_js"] = url(BUILD_DIR + entry["file"])


@app.get("/api/peer")
async def get_peer_manifest() -> dict:
    """Identity manifest used by the Caterva3 peer handshake."""
    return {
        "peer_id": settings.peer_id,
        "name": settings.urlbase,
        "api_version": providers.PEER_API_VERSION,  # single source of truth in providers.py
        "roots": ["@public"],  # only locally-owned public data: never mounts
        "capabilities": {"chunk_api": "plain"},  # api/chunk works for plain
        # datasets only (see design doc)
    }


@app.get("/api/roots")
async def get_roots(user: db.User = Depends(optional_user)) -> dict:
    """
    Get a dict of roots, with root names as keys and properties as values.

    Returns
    -------
    dict
        The dict of roots.
    """
    # Here we just return the special roots @personal, @shared and @public
    roots = {}
    root = models.Root(name="@public")
    roots[root.name] = root
    if user:
        for name in ["@personal", "@shared"]:
            root = models.Root(name=name)
            roots[root.name] = root

    for p in providers.active:
        for name in p.roots():
            roots[name] = models.Root(name=name)

    return roots


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
    root = path.parts[0]
    provider = providers.provider_for(root)
    if provider is not None:
        rel = "/".join(path.parts[1:])
        try:
            return await provider.list(root, rel)
        except providers.ProviderError as exc:
            raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc

    # A path may descend into a container (e.g. a TreeStore .b2z or .h5); list
    # its members.
    directory, inner_key = split_and_resolve(path, user, resolver=get_writable_path)
    if directory.is_file():
        container = srv_utils.open_container(directory)
        if container is not None:
            try:
                # Deep listing of leaves relative to the requested path, matching
                # walk_files() semantics for directories.
                prefix = inner_key or "/"
                strip = prefix.rstrip("/") + "/"
                names = sorted(d[len(strip) :] for d in container.leaves(prefix))
                if not names and inner_key is not None and container.is_leaf(inner_key):
                    # `leaves` answers with a prefix's descendants, and a leaf is
                    # not one of its own: a path that names one lists as that
                    # name, the way a single file does below
                    names = [inner_key.rsplit("/", 1)[-1]]
                return names
            finally:
                container.close()
        if inner_key is not None:
            srv_utils.raise_not_found()
        name = pathlib.Path(directory.name)
        return [str(name.with_suffix("") if name.suffix == ".b2" else name)]
    # Sort the list of datasets and return
    paths = [
        str(relpath.with_suffix("") if relpath.suffix == ".b2" else relpath)
        for _, relpath in srv_utils.walk_files(directory)
    ]
    return sorted(paths)


@app.get("/api/info/{path:path}")
async def get_info(
    path: pathlib.Path,
    response: Response,
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
    root = path.parts[0]
    provider = providers.provider_for(root)
    if provider is not None:
        rel = "/".join(path.parts[1:])
        try:
            info = await provider.info(root, rel)
        except providers.ProviderError as exc:
            raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc
        # A peer dataset is fetched from its owner and re-serialized here, so
        # `api/fetch` will 416 a range for it.  Saying so now saves the client
        # the request it would otherwise spend finding that out: a `Proxy` over
        # a stored dataset reads blocks, and asks first whether it may.  Set over
        # whatever the peer said of its own copy, which is stored *there*
        if isinstance(info, dict):
            info["accept_ranges"] = "none"
        return info

    abspath, inner_key = split_and_resolve(path, user)
    if inner_key is not None:
        # A member inside a container (e.g. a TreeStore .b2z or .h5 leaf/group).
        meta = srv_utils.container_member_info(abspath, inner_key)
        if meta is None:
            srv_utils.raise_not_found()
        # The container's validator, which is the leaf's too: a leaf served in
        # ranges is a window of that file, so what tells a client the window
        # still holds is what tells it the file did not change under it
        etag = dataset_etag(abspath)
        if etag:
            response.headers["ETag"] = etag
        return meta
    if abspath.is_dir():
        files = list(srv_utils.walk_files(abspath))
        size = sum(f.stat().st_size for f, _ in files)
        return models.Directory(mtime=abspath.stat().st_mtime, size=size, nfiles=len(files))
    # The same validator the file responses carry, so a client that opens a frame
    # over two requests can tell that both saw the same one
    etag = dataset_etag(abspath)
    if etag:
        response.headers["ETag"] = etag
    try:
        meta = srv_utils.read_metadata(abspath)
    except remote_proxy.RemoteProxyDenied as exc:
        raise fastapi.HTTPException(status_code=403, detail=str(exc)) from exc
    # A dataset with a file of its own is served by `FileResponse`, which honours
    # a range.  Only said where it is certain: a directory or a lazy expression
    # is not a stored frame, and a container member depends on whether its leaf
    # has a window, so a client told nothing asks as it always did -- an omission
    # costs a request, a wrong answer would cost correctness.
    #
    # A `.b2nd` that proxies an HDF5 dataset is one of those wrong answers: it
    # reads as an `NDArray` here, so it arrives with a `Metadata` like any other,
    # but `api/fetch` opens it as an `HDF5Proxy` and rebuilds what it serves --
    # the file on disk is a proxy's chunks, not the array's.  It 416s a range,
    # and a client that took "bytes" on trust would find that out on its first
    # block read rather than on a probe it could have made
    if isinstance(meta, models.Metadata):
        if remote_proxy.is_metadata(meta):
            meta.accept_ranges = "none"
            # Cache bookkeeping contains binary bitmaps and source stamps that
            # are neither JSON metadata nor part of the public proxy contract.
            # Expose only the portable descriptor through api/info.
            meta.schunk.vlmeta = {"b2o": meta.schunk.vlmeta["b2o"]}
        elif not srv_utils.is_hdf5_proxy_meta(meta):
            meta.accept_ranges = "bytes"
    return meta


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
    lock = dataset_lock(abspath)
    async with lock:
        proxy = open_b2(abspath, path)
        await proxy.afetch(slice_)


# mtime is in the key and unused in the body, so that a container rewritten
# underneath is reopened instead of served from here (as get_filtered_array does)
@functools.lru_cache(maxsize=16)
def member_window(abspath, inner_key, mtime):
    """Where a container leaf's frame lies in the file, as (offset, nbytes), or None.

    A `.b2z` is a zip of *stored* members, so an external leaf's bytes in the
    file are the Blosc2 frame it would have been written as on its own -- which
    is what lets a ranged request be answered by seeking to it instead of
    rebuilding the leaf.  None where there is no such window: a leaf embedded in
    the store's own super-chunk, a `C2Array` reference, an HDF5 dataset (not a
    Blosc2 frame at all), a CTable `.b2z` (not a store of leaves).

    The frame's own magic is checked before the window is offered, so a `.b2z`
    written by something else -- with compressed members, say -- cannot have a
    window handed out that would decode to nonsense.
    """
    if abspath.suffix != ".b2z":
        return None
    try:
        store = blosc2.open(abspath)
    except Exception:
        return None
    if not isinstance(store, blosc2.DictStore):
        return None
    # A blosc2 that cannot say where a member lies is one more way there is no
    # window to offer, not an error: this is an optimization -- seek to the
    # leaf's frame instead of rebuilding it -- and the caller already has the
    # rebuild for every other case that returns None here.  Asked of the store
    # rather than of a version, so it starts working when the API arrives
    locate = getattr(store, "member_window", None)
    if locate is None:
        return None
    window = locate(inner_key)
    if window is None:
        return None
    offset, _size = window
    with open(abspath, "rb") as container:
        container.seek(offset)
        if container.read(10)[2:9] != b"b2frame":
            return None
    return window


@functools.lru_cache(maxsize=16)
def open_member(abspath, inner_key, mtime):
    """The leaf at *inner_key* inside a container file, kept between requests.

    Reading a leaf chunk by chunk, which is what a proxy over one does, would
    otherwise reopen the container for every chunk.  Holding on to the leaf is
    safe: a TreeStore leaf is an independent object once fetched, and outlives
    the adapter that produced it.
    """
    leaf = srv_utils.open_container_member(abspath, inner_key)
    if leaf is None:  # no such key, or it names a group rather than a dataset
        srv_utils.raise_not_found()
    return leaf


def get_abspath(
    path: pathlib.Path, user: (db.User | None), may_not_exist=False
) -> tuple[
    pathlib.Path,
    collections.abc.Callable[[], collections.abc.Awaitable],
]:
    """
    Get absolute path in local storage.
    """
    filepath = get_writable_path(path, user)

    root = path.parts[0]
    if root == "@personal":
        cachedir = settings.personal
    elif root == "@shared":
        cachedir = settings.shared
    elif root == "@public":
        cachedir = settings.public

    # Special case for the cache root
    if cachedir == filepath:
        return filepath

    # Special case for directories
    elif (cachedir / filepath).is_dir():
        return cachedir / filepath

    # HDF5 files cannot be compressed, as they are supported natively
    if (
        filepath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES | srv_utils.HDF5_SUFFIXES
        and not may_not_exist
    ):
        if filepath.is_file():
            srv_utils.compress_file(filepath)
        filepath = f"{filepath}.b2"

    # Security check
    abspath = cachedir / filepath
    if cachedir not in abspath.parents:
        srv_utils.raise_bad_request(f"Invalid path {filepath}")

    # Existence check
    if not abspath.is_file() and not may_not_exist:
        srv_utils.raise_not_found()

    return abspath


def split_and_resolve(path, user, resolver=None):
    """Split a container-descent path and resolve the container on disk.

    Like ``srv_utils.split_container_path`` + resolving, but skips split
    points that are real *directories* merely named like a container (e.g.
    ``results.h5/``, creatable through the upload API), so files beneath them
    stay reachable instead of 404ing. Returns ``(abspath, inner_key)``.
    """
    resolver = resolver or get_abspath
    parts = pathlib.Path(path).parts
    for i, part in enumerate(parts[:-1]):
        if pathlib.PurePath(part).suffix in srv_utils.BLOSC2_CONTAINER_SUFFIXES:
            abspath = resolver(pathlib.Path(*parts[: i + 1]), user)
            if abspath.is_dir():
                continue  # a directory that merely looks like a container
            return abspath, "/" + "/".join(parts[i + 1 :])
    return resolver(pathlib.Path(path), user), None


def parse_segment(segment):
    """One dimension of a slice string: `3`, `1:4`, `:9`, `:`.

    Raises `ValueError` for anything else, that being what the callers catch and
    turn into a 400.  A segment of four parts would make `slice()` raise a
    `TypeError` instead, which is a 500 about the server rather than a 400 about
    the request, so it is counted here.
    """
    if ":" not in segment:
        return int(segment)
    parts = [int(x.strip()) if x.strip() else None for x in segment.split(":")]
    if len(parts) > 3:
        raise ValueError(f"{segment!r} is not a slice: a slice has a start, a stop and a step")
    return slice(*parts)


def parse_slice(string):
    """A slice string as a key, or None where it names nothing.

    Raises `ValueError` for a string that is not one; the callers turn that into
    a 400, since what it describes is the request and not this server.
    """
    if not string:
        return None
    obj = [parse_segment(segment) for segment in string.split(",")]
    return tuple(obj) if len(obj) > 1 else obj[0]


MAX_INDICES_CHARS = 8 * 1024 * 1024
"""How long the `indices` parameter may be, measured before it is parsed.

The first bound the parse has, and the cheapest: it is a length, checked
against a string already in hand, and it caps what `json.loads` is asked to
build.  Generous enough for any key `MAX_FETCH_COORDS` allows -- a coordinate
costs a handful of characters.

Not a bound on the request, though: by the time a string is in hand it has been
read.  That bound is `MAX_FETCH_BODY`, which is what stops a body before it is
allocated.
"""

MAX_FETCH_BODY = MAX_INDICES_CHARS + 2**20
"""How much of a `POST api/fetch` body is read at all.

What `MAX_INDICES_CHARS` cannot be: a body is read whole before any parameter
of it can be measured, so a length checked after the read describes what the
server already spent.  Read off the request stream against this instead, and a
caller cannot make an anonymous fetch allocate half a gigabyte.

A megabyte over the longest `indices` this parses, which is the room the JSON
around it takes -- so every request that would have been answered still is, and
the refusal a too-long key gets is still the one about the key.
"""

MAX_FETCH_COORDS = 1_000_000
"""How many coordinates one fetch may name, across every dimension of the key.

`POST api/fetch` exists to lift the length limit a query string put on a key,
and that limit was the only thing bounding this: without a bound of its own, one
request can ask the server to gather, materialize and serialize an array of any
size at all.  The cap has to be said out loud now that the URL no longer says it.

A million points is far past what a scattered read is for and still a bounded
amount of work.  A caller with more of them has a whole dataset to fetch, or a
few batches to ask for.
"""


def parse_indices(string):
    """The fancy key `indices` names, ready to index an array with.

    JSON, one entry per dimension: a list of integers for a dimension indexed by
    an array, an integer for one indexed by a scalar, a string for one indexed by
    a slice (spelled as `slice_` spells it), and null for one taken whole.  So
    `[[1,5,9],450,"0:10",null]` is `array[[1,5,9], 450, 0:10, :]`.

    JSON rather than the spelling `slice_` uses, because a list of coordinates
    has no unambiguous reading as a comma-separated string, and `json.loads` is
    the one parser here that is not this module's to get wrong.  Every entry is
    checked: what comes back indexes an array, so nothing else may reach it.

    Bounded as well as checked: see `MAX_INDICES_CHARS` and `MAX_FETCH_COORDS`.
    """
    if len(string) > MAX_INDICES_CHARS:
        raise ValueError(f"indices is {len(string)} characters where at most {MAX_INDICES_CHARS} are read")
    try:
        raw = json.loads(string)
    except json.JSONDecodeError as exc:
        raise ValueError(f"indices is not JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("indices must be a JSON list, one entry per dimension")
    coords = sum(len(entry) for entry in raw if isinstance(entry, list))
    if coords > MAX_FETCH_COORDS:
        raise ValueError(
            f"indices names {coords} coordinates where at most {MAX_FETCH_COORDS} are gathered; "
            "ask for them in batches"
        )
    key = []
    for entry in raw:
        # `bool` is an `int` in Python and a mask is not one of these, so it is
        # ruled out before the integer branch can take it for a coordinate
        if entry is None:
            key.append(slice(None))
        elif isinstance(entry, str):
            try:
                key.append(parse_segment(entry))
            except ValueError as exc:
                raise ValueError(f"indices entry {entry!r} is not a slice: {exc}") from exc
        elif isinstance(entry, bool) or not isinstance(entry, (int, list)):
            raise ValueError(f"indices entry {entry!r} is not an integer, a list of them, a slice or null")
        elif isinstance(entry, int):
            key.append(entry)
        else:
            if any(isinstance(v, bool) or not isinstance(v, int) for v in entry):
                raise ValueError("an indices list may hold only integers")
            key.append(np.array(entry, dtype=np.int64))
    return tuple(key)


@app.get("/api/fetch/{path:path}")
async def fetch_data(
    path: pathlib.Path,
    slice_: str | None = None,
    user: db.User = Depends(optional_user),
    filter: str | None = None,
    field: str | None = None,
    indices: str | None = None,
    range_header: str | None = fastapi.Header(None, alias="Range"),
):
    """
    Fetch a dataset.

    Parameters
    ----------
    path : pathlib.Path
        The path to the dataset.
    slice_ : str
        The slice to fetch.
    filter : str
        The filter to apply to the dataset.
    field : str
        The desired field of dataset.

    The field and filter parameters are incompatible, if both are giving the API will
    return a "400 Bad Request" error response.

    Returns
    -------
    FileResponse or StreamingResponse
        The (slice of) dataset as a Blosc2 schunk.  When the whole dataset is
        to be downloaded (instead of some slice which does not cover it fully),
        its stored image is served containing all data and metadata (including
        variable length fields).

    The `FileResponse` case, and only that one, serves byte ranges: a client
    can read the header, the chunk offsets and single blocks of a stored frame
    instead of transferring it whole (blosc2's Proxy over a C2Array does).
    Everything else is built as it is sent and answers a `Range` header with a
    416, rather than quietly returning the whole body with a 200.
    """

    try:
        slice_ = parse_slice(slice_)
    except ValueError as exc:
        srv_utils.raise_bad_request(f"slice_ is not a slice: {exc}")
    if indices is not None:
        # Gathered here rather than at the client, which would have to fetch the
        # blocks holding the points to pick them out of: scattered points are
        # the case where a block is nearly all waste, and a shared uplink is
        # what a server runs out of first
        if slice_ is not None or filter or field:
            srv_utils.raise_bad_request("indices cannot be combined with slice_, filter or field")
        try:
            indices = parse_indices(indices)
        except ValueError as exc:
            srv_utils.raise_bad_request(str(exc))

    root = path.parts[0]
    provider = providers.provider_for(root)
    if provider is not None:
        if filter or field:
            # ponytail: filter/field post-processing is not wired for peer
            # datasets yet; refuse loudly rather than return wrong data.
            raise fastapi.HTTPException(
                status_code=400, detail="filter/field are not supported on peer datasets yet"
            )
        if indices is not None:
            # A provider fetches boxes: `RootProvider.fetch` takes a `slice_` and
            # nothing else, so coordinates have nowhere to go here.  Refused
            # rather than dropped -- a dropped key is not a smaller answer but
            # the whole dataset, handed back as though it were the points asked
            # for, which is the one failure this parameter must not have
            raise fastapi.HTTPException(
                status_code=400, detail="indices are not supported on peer datasets yet"
            )
        rel = "/".join(path.parts[1:])
        try:
            data = await provider.fetch(root, rel, slice_)
        except providers.ProviderError as exc:
            raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc
        if isinstance(data, bytes):
            # Already a serialized cframe (e.g. a peer CTable row range):
            # stream it as-is, no numpy wrap.
            srv_utils.refuse_range(range_header, path)
            return responses.StreamingResponse(
                srv_utils.iterchunk(data),
                media_type="application/octet-stream",
                headers=srv_utils.NO_RANGES,
            )
        # A peer dataset is fetched from its owner and re-serialized here, so
        # there is no file to seek into
        srv_utils.refuse_range(range_header, path)
        cframe = await asyncio.to_thread(lambda: blosc2.asarray(np.ascontiguousarray(data)).to_cframe())
        downloader = srv_utils.iterchunk(cframe)
        return responses.StreamingResponse(
            downloader, media_type="application/octet-stream", headers=srv_utils.NO_RANGES
        )

    abspath, inner_key = split_and_resolve(path, user)

    # Container leaves are fetchable too; a plain .h5/.hdf5 (no inner_key) is
    # a group, not a dataset, so it keeps the usual 400.
    fetchable = abspath.suffix in {".b2frame", ".b2nd", ".b2z"} or (
        abspath.suffix in srv_utils.HDF5_SUFFIXES and inner_key is not None
    )
    if not fetchable:
        srv_utils.raise_bad_request(
            "The fetch API only supports datasets (.b2nd, .b2frame, .b2z); "
            "use the download API if you only want to download the file"
        )

    window = None  # where a container leaf's frame lies, when it has one
    filter = filter.strip() if filter else filter
    if filter:
        if field:
            srv_utils.raise_bad_request("Cannot handle both field and filter parameters at the same time")
        mtime = abspath.stat().st_mtime
        try:
            container, _ = await concurrency.run_in_threadpool(
                lambda: get_filtered_array(
                    abspath, path, filter, sortby=None, mtime=mtime, inner_key=inner_key
                )
            )
        except ValueError as exc:
            srv_utils.raise_bad_request(str(exc))
    elif inner_key is not None:
        # A member inside a container (e.g. a TreeStore .b2z or .h5 leaf).
        # A leaf that is a whole frame inside the container can be served in
        # ranges, by seeking to it -- what a stored dataset gets from
        # FileResponse, and what lets a client read its blocks.  Not when a
        # field is projected out of it: that is computed, not stored.
        window = member_window(abspath, inner_key, abspath.stat().st_mtime)
        container = srv_utils.open_container_member(abspath, inner_key)
        if container is None:
            srv_utils.raise_not_found()
    else:
        container = open_b2(abspath, path)

    if field:
        container = container[field]

    if isinstance(container, blosc2.DictStore):
        # A container is a file of leaves rather than an array: its stored image
        # is the file, which is what a client opening it as a store expects --
        # and what the type ladder below used to die on, asking a TreeStore for
        # a typesize it has not got (a 500 where the docstring promises the
        # stored image).  Ranges come with FileResponse, so a leaf of it is
        # reachable byte-wise too.
        if slice_ is not None or indices is not None:
            # Both narrow an answer, and a container has nothing to narrow: what
            # is served here is the file itself.  Answering the whole of it to a
            # caller who named two coordinates would be a widening, not a slice
            srv_utils.raise_bad_request(
                f"{path} is a container, so there is nothing to slice; ask for a dataset inside it"
            )
        return FileResponse(
            abspath,
            filename=abspath.name,
            media_type="application/octet-stream",
            headers=with_etag(abspath),
        )

    if isinstance(
        container,
        (blosc2.NDArray, blosc2.LazyArray, hdf5.HDF5Proxy, blosc2.NDField, remote_proxy.ServerRemoteProxy),
    ):
        array = container
        schunk = getattr(array, "schunk", None)  # not really needed
        typesize = array.dtype.itemsize
        shape = array.shape
    elif isinstance(container, blosc2.CTable):
        array = container
        schunk = None
        typesize = 1  # not used for CTable
        shape = (array.nrows,)
    else:
        # SChunk
        array = None
        schunk = container  # blosc2.SChunk
        typesize = schunk.typesize
        shape = (len(schunk),)
        if isinstance(slice_, int):
            # TODO: make SChunk support integer as slice
            slice_ = slice(slice_, slice_ + 1)

    whole = (slice_ is None or slice_ == ()) and indices is None
    if not whole and isinstance(slice_, tuple):
        whole = all(
            isinstance(sl, slice)
            and (sl.start or 0) == 0
            and (sl.stop is None or sl.stop >= sh)
            and sl.step in (None, 1)
            for sl, sh in zip(slice_, shape, strict=False)
        )

    if (
        whole
        and (
            not isinstance(
                array,
                blosc2.LazyArray
                | hdf5.HDF5Proxy
                | blosc2.NDField
                | blosc2.CTable
                | remote_proxy.ServerRemoteProxy,
            )
        )
        and (not filter)
    ):
        if inner_key is None:
            # Send the data in the file straight to the client,
            # avoiding slicing and re-compression.  This is also the one branch that
            # serves byte ranges: FileResponse seeks into the file and sends only
            # what was asked for, which is what block-granular clients read.  The
            # `ETag` is ours: Starlette's is a digest of the mtime and the size,
            # and a chunk written as a run of zeros can leave both untouched
            return FileResponse(
                abspath,
                filename=abspath.name,
                media_type="application/octet-stream",
                headers=with_etag(abspath),
            )
        if window is not None and not field:
            # The same for a leaf, whose frame lies inside the container: its
            # stored image is a window of that file, ranges included.  Not only
            # cheaper than the rebuild below -- more faithful, since the rebuild
            # re-partitions (it slices and recompresses), and so disagrees with
            # the chunks and blocks api/info reports for the very same leaf.
            return srv_utils.window_response(abspath, window, range_header, headers=with_etag(abspath))

    # Everything below builds its answer, so a range cannot be honoured: say so
    # here, before computing a body that is not going to be sent
    srv_utils.refuse_range(range_header, path)

    if indices is not None:
        if not isinstance(array, blosc2.NDArray | remote_proxy.ServerRemoteProxy):
            srv_utils.raise_bad_request(f"{path} is not an array that can be indexed by coordinates")
        try:
            # `NDArray` reads scattered coordinates through its own sparse gather,
            # so this touches the chunks the points land in and not the dataset.
            # Off the event loop: bounded by `MAX_FETCH_COORDS` but not small, and
            # a gather that ran here would stall every other request for its
            # duration -- it reads, materializes and serializes, all blocking
            if isinstance(array, remote_proxy.ServerRemoteProxy):
                data = await read_remote_proxy(array, indices, abspath)
            else:
                data = await concurrency.run_in_threadpool(
                    lambda: blosc2.asarray(array[indices]).to_cframe()
                )
        except (IndexError, ValueError) as exc:
            srv_utils.raise_bad_request(str(exc))
    elif isinstance(array, blosc2.CTable):
        row_start, row_stop = srv_utils.ctable_row_range(slice_, array.nrows)
        view = array.slice(row_start, row_stop)
        data = await concurrency.run_in_threadpool(view.to_cframe)
    elif isinstance(array, hdf5.HDF5Proxy):
        data = array.to_cframe(() if slice_ is None else slice_)
    elif isinstance(array, blosc2.LazyArray):
        data = await concurrency.run_in_threadpool(
            lambda: array.compute(() if slice_ is None else slice_).to_cframe()
        )
    elif isinstance(array, blosc2.NDField):
        data = array[() if slice_ is None else slice_]
        data = blosc2.asarray(data)
        data = data.to_cframe()
    elif isinstance(array, remote_proxy.ServerRemoteProxy):
        data = await read_remote_proxy(array, () if slice_ is None else slice_, abspath)
    elif isinstance(array, blosc2.NDArray):
        # Using NDArray.slice() allows a fast path when it is aligned with the chunks
        # As we are going to serialize the slice right away, it is not clear in which
        # situations a contiguous slice is faster than a non-contiguous one.
        # Let's just use the contiguous one for now, until more testing is done.
        try:
            data = array.slice(slice_, contiguous=True).to_cframe()
        except IndexError as exc:
            srv_utils.raise_bad_request(str(exc))  # 400 Bad Request
    else:
        # SChunk
        data = schunk[slice_]  # SChunck => bytes
        # A bytes object can still be compressed as a SChunk
        schunk = blosc2.SChunk(data=data, cparams={"typesize": typesize})
        data = schunk.to_cframe()

    downloader = srv_utils.iterchunk(data)
    return responses.StreamingResponse(
        downloader, media_type="application/octet-stream", headers=srv_utils.NO_RANGES
    )


@app.post(
    "/api/fetch/{path:path}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": models.FetchPayload.model_json_schema()},
            },
        }
    },
)
async def post_fetch_data(
    path: pathlib.Path,
    request: Request,
    user: db.User = Depends(optional_user),
):
    """
    Fetch a dataset, with the parameters in the body rather than the query.

    The same fetch as `GET api/fetch` in every respect -- it is the same code,
    called with what the body holds -- and it exists for the one thing a query
    string cannot do: carry a key of more coordinates than a URL has room for.
    A client small enough to fit its key in an URL has no reason to come here.

    The body is read here rather than declared as a parameter, so that its
    length is a bound and not a report: FastAPI reads a declared body whole
    before anything of ours runs, so `MAX_INDICES_CHARS` would be checked
    against a string the server had already been made to allocate.

    Byte ranges are not offered.  A `Range` header pairs with a GET of a stored
    frame, and nothing that would arrive by this route is one.

    Parameters
    ----------
    path : pathlib.Path
        The path to the dataset.
    request : Request
        Carries `indices`, `slice_`, `filter` and `field` as JSON, by the names
        the query takes them under (`models.FetchPayload`).

    Returns
    -------
    FileResponse or StreamingResponse
        Whatever the GET would have returned for the same parameters.
    """
    body = await srv_utils.read_bounded_body(request, MAX_FETCH_BODY)
    try:
        payload = models.FetchPayload.model_validate_json(body)
    except pydantic.ValidationError as exc:
        # 422, the status FastAPI answers a body it cannot read with, and
        # reported the way FastAPI reports its own: without the input that
        # failed (`fastapi.routing` builds this error with an empty `input`).
        # Pydantic puts the *whole body* there for malformed JSON, so echoing
        # its errors back hands a caller who sent MAX_FETCH_BODY bytes of
        # nonsense every one of them again -- an amplifier on the one endpoint
        # this bound exists to keep small
        detail = [
            {"type": err["type"], "loc": err["loc"], "msg": err["msg"]}
            for err in exc.errors(include_url=False)
        ]
        raise fastapi.HTTPException(status_code=422, detail=detail) from exc
    return await fetch_data(
        path=path,
        slice_=payload.slice_,
        user=user,
        filter=payload.filter,
        field=payload.field,
        indices=payload.indices,
        range_header=None,
    )


@app.get("/api/download/{path:path}")
async def download_data(
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
    include_cache: bool = True,
    accept_encoding: str | None = fastapi.Header(None),
    range_header: str | None = fastapi.Header(None, alias="Range"),
):
    # This one always streams, decompressing on the way out more often than not,
    # so it never serves ranges; api/fetch on a stored dataset is what does.  The
    # refusal comes after the path is resolved, so a path that does not exist is
    # still a 404 rather than a 416 about a file nobody has.
    provider = providers.provider_for(path.parts[0])
    if provider is not None:
        try:
            body, media_type, headers = await provider.download(
                path.parts[0], "/".join(path.parts[1:]), accept_encoding
            )
        except providers.ProviderError as exc:
            raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc
        srv_utils.refuse_range(range_header, path)
        # Case-insensitive once, for everything added below, rather than per
        # header at each addition: these came off an httpx response and httpx
        # hands back lowercased names, so a plain dict sees no match, adds a
        # second entry, and the answer goes out carrying the header twice -- the
        # peer's `Content-Disposition` and ours, for a client to choose between.
        # `download` is a provider interface that answers with headers of its
        # own choosing, so this holds for whatever else it comes back with and
        # not only for the two names spelled out here
        headers = MutableHeaders(headers)
        headers.setdefault("Content-Disposition", f'attachment; filename="{path.name}"')
        headers.update(srv_utils.NO_RANGES)
        return responses.StreamingResponse(body, media_type=media_type, headers=headers)

    decompress = accept_encoding != "blosc2"
    # Read before creating the response: a bad path must 404 up front, not
    # abort the stream after the 200 headers already went out.
    content = await get_file_content(path, user, decompress=decompress, include_cache=include_cache)
    srv_utils.refuse_range(range_header, path)

    async def downloader():
        yield content

    mimetype = guess_type(path)
    headers = {"Content-Disposition": f'attachment; filename="{path.name}"', **srv_utils.NO_RANGES}
    if accept_encoding == "blosc2":
        abspath = get_abspath(path, user)
        suffix = abspath.suffix
        if suffix == ".b2":
            headers["Content-Encoding"] = "blosc2"

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
        html, _ = html_exporter.from_notebook_node(nb)
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
    """One compressed chunk of a dataset, as it is stored.

    This is how a client caches a remote array a chunk at a time, so it serves
    what is *stored* in Blosc2 chunks and nothing else.  A container leaf counts:
    a TreeStore keeps its leaves as ordinary Blosc2 arrays, and the chunk is
    handed over as it lies in the ``.b2z``.  An HDF5 dataset and a CTable do not,
    and say so rather than being taken apart and recompressed once per request --
    ``api/fetch`` with a ``slice_`` computes exactly the region wanted for those,
    which is both less work here and fewer bytes over the wire.
    """
    if providers.provider_for(path.parts[0]) is not None:
        # Non-transitivity guard: peer roots are never re-exposed chunk-wise.
        raise fastapi.HTTPException(status_code=404, detail="external roots are non-transitive")

    # Resolve the way api/fetch does, so a leaf inside a container is reachable:
    # get_abspath alone drops the inner key and 404s on the container's own path
    abspath, inner_key = split_and_resolve(path, user)
    if abspath.suffix in srv_utils.HDF5_SUFFIXES:
        srv_utils.raise_bad_request(
            f"{path} is HDF5, whose chunks are HDF5-compressed rather than Blosc2 chunks; "
            "fetch it with the slice_ parameter instead"
        )
    # One lock per container, so the leaves of a .b2z serialize with each other
    # over the file they share; a plain dataset is its own container, as before.
    # `abspath` *is* the container (`split_and_resolve` splits the inner key off
    # it), and it is where the file lives rather than what it was asked for --
    # so two users' `@personal` arrays of one name do not wait on each other
    lock = dataset_lock(abspath)
    async with lock:
        root = path.parts[0]
        get_rootdir_or_error(root, user)

        if inner_key is None:
            container = open_b2(abspath, path)
        else:
            container = open_member(abspath, inner_key, abspath.stat().st_mtime)
        if isinstance(container, blosc2.CTable):
            srv_utils.raise_bad_request(
                f"{path} is a CTable, which is a set of columns rather than one chunked array; "
                "fetch it with the slice_ parameter instead"
            )
        if isinstance(container, blosc2.LazyArray):
            # In case we do, this would have to be changed.
            chunk = container.get_chunk(nchunk)
        elif isinstance(container, remote_proxy.ServerRemoteProxy):
            before = abspath.stat().st_size
            cache_limit = remote_proxy_cache_limit(container)
            chunk = await concurrency.run_in_threadpool(
                lambda: container.get_chunk(nchunk, cache_limit=cache_limit)
            )
            if settings.quota:
                growth = max(0, abspath.stat().st_size - before)
                if growth:
                    account_chunk_written(growth)
        else:
            schunk = getattr(container, "schunk", container)
            chunk = schunk.get_chunk(nchunk)

    downloader = srv_utils.iterchunk(chunk)
    return responses.StreamingResponse(downloader)


# Where the frame's generation counter lives in its lock sidecar: past the byte
# range Windows locks, as c-blosc2 puts it
LOCK_SEQ_OFFSET = 8


def frame_generation(abspath: pathlib.Path) -> int | None:
    """The frame's own count of how many times it has been written to.

    Blosc2 bumps it in the `.b2lock` sidecar every time a handle takes the
    exclusive lock, which is what makes it a validator where the file's length is
    not one: a chunk written as a run of zeros stores no payload, so the frame can
    come out of a write exactly as long as it went in, with different content.

    None where nothing has ever written the frame under locking, which is every
    dataset that was only ever uploaded whole.
    """
    sidecar = abspath.with_name(abspath.name + ".b2lock")
    try:
        with open(sidecar, "rb") as counter:
            counter.seek(LOCK_SEQ_OFFSET)
            raw = counter.read(8)
    except OSError:
        return None
    return int.from_bytes(raw, "little") if len(raw) == 8 else None


def dataset_etag(abspath: pathlib.Path) -> str | None:
    """A validator that changes whenever the bytes behind *abspath* do.

    The generation counter and the file's own size and mtime together, because
    neither half is enough alone: the counter does not move when a dataset is
    replaced wholesale by an upload (which may leave the old sidecar behind), and
    the size and mtime do not reliably move when a chunk is written as a run of
    zeros.  Either changing is enough to change this.

    What it is for: a client reads a frame's header in one request and its
    offsets in another, and the second is only meaningful if the frame did not
    move in between -- a chunk written by someone else lands exactly where the
    old offsets block was.  With this the client can tell, rather than decoding
    the bytes of a chunk as though they were an index.
    """
    try:
        stat = abspath.stat()
    except OSError:
        return None
    generation = frame_generation(abspath)
    generation = "u" if generation is None else f"{generation:x}"
    return f'"{generation}-{stat.st_size:x}-{stat.st_mtime_ns:x}"'


def with_etag(abspath: pathlib.Path) -> dict:
    """`ETag` for a file response, where the file has one to give."""
    etag = dataset_etag(abspath)
    return {"etag": etag} if etag else {}


# What a frame codes in the top nibble of a chunk's flags byte: an uninitialized
# chunk is one nothing was ever written to, which is what `blosc2.uninit` lays an
# array out with and what a fill claims one slot at a time.  The value is
# blosc2's own, so a rename or a renumbering of it travels; the offset and the
# nibble are the header's layout, which is what reading one byte instead of
# walking the array costs (see `chunk_is_unwritten`)
CHUNK_FLAGS_BYTE = 31
SPECIAL_UNINIT = blosc2.SpecialValue.UNINIT.value


# Where a chunk says what its filters ran on.  Byte 3 of the Blosc header, which
# every chunk carries and `get_cbuffer_sizes` does not report
CHUNK_TYPESIZE_BYTE = 3
MAX_HEADER_TYPESIZE = 255


def chunk_typesize(chunk: bytes) -> int:
    """The typesize a chunk's filters were run on, off its own header."""
    return chunk[CHUNK_TYPESIZE_BYTE]


def filter_typesize(typesize: int) -> int:
    """What a chunk of an array of this typesize carries in its header.

    Itself, up to what one byte holds.  Past that a chunk records 1 instead:
    shuffling on a stride that wide buys nothing, so the filters run bytewise and
    the header says so.  Which makes this check weaker for such an array -- every
    typesize past 255 looks alike -- and no weaker than not making it.
    """
    return typesize if typesize <= MAX_HEADER_TYPESIZE else 1


def chunk_is_unwritten(schunk, nchunk: int) -> bool:
    """Whether a slot of a frame still holds no content at all.

    Read from the chunk's own header, not from a scan of the array: a lazy chunk
    is the header alone, so this is one small read whatever the array's size,
    where walking every chunk would make a fill cost the square of its length.
    Which is why `iterchunks_info()` -- the way this question is asked where a
    whole array is checked at once -- is not the way it is asked here: that one
    reads every chunk to answer about one, and this runs once per write.

    A run of zeros is *not* unwritten -- a writer that stored an all-zero chunk
    stored something, and the frame tags it as zeros rather than as
    uninitialized.  That distinction is the whole reason an array to be filled is
    laid out with `blosc2.uninit` rather than `blosc2.zeros`.
    """
    lazychunk = schunk.get_lazychunk(nchunk)
    return (lazychunk[CHUNK_FLAGS_BYTE] >> 4) & 0x7 == SPECIAL_UNINIT


def count_written(abspath: pathlib.Path) -> tuple[int, int]:
    """How many chunks of a frame hold content, and how many there are.

    Read from the frame's offsets in one go, which is a couple of reads and a
    decompress: the alternative walks the chunks and costs a read apiece, some
    50x more on an array of a few thousand chunks.  The offsets are also where
    the answer really lives -- a fill records itself there and nowhere else, so
    there is no count for this to fall out of step with.
    """
    source = blosc2.FsspecNDSource(str(abspath))
    written = source.written_chunks()
    return int(written.sum()), int(written.size)


# Where a fill records itself for the server's own use.  Not a second record of
# which chunks are written -- that is the frame's offsets and only them -- but of
# what is to happen once they all are
FILL_STATE = "fill_state"
PUBLISHED_URL = "published_url"
FILL_NONCE = "fill_nonce"
FILLING, COMPLETE, PUBLISHING, PUBLISHED = "filling", "complete", "publishing", "published"


def publish_key(path: pathlib.Path, user: db.User) -> pathlib.Path:
    """The key an array is published under, which separates users as the store does.

    `@personal` is a name every user spells the same way and no two of them share:
    on disk it is `personal/<user id>/...`, and a key taken from the request path
    alone would drop that id.  Two users filling `@personal/run.b2nd` would then
    publish to one destination, where the second overwrites the first's data --
    and reads it back, by publishing and then fetching what is there.

    `@shared` and `@public` are shared by design, and keep the name they are asked
    for: what several users write to one place there they meant to.
    """
    root, *subpath = path.parts
    if root == "@personal":
        return pathlib.Path(root, str(user.id), *subpath)
    return path


def publish_destination(path: pathlib.Path) -> str:
    """Where a finished array is published, under the root the server configures.

    *path* is a publish key (see `publish_key`), not the request path: the two
    differ where a root means a different place to each user.

    The client says which key it wants below that root and nothing above it: a
    destination taken from the request would let a caller point the server at a
    bucket they control and have it write someone else's data into it.

    "Below that root" is checked here and not only where the path was resolved on
    disk.  What comes back is an URL, and `fsspec.url_to_fs` normalizes a `..` in
    one just as a filesystem does: a segment that survived this far would move
    the write out of the publish root, or to another prefix of the same bucket.
    """
    if not settings.publish_root:
        srv_utils.raise_bad_request("this server publishes nowhere: set publish_root in its configuration")
    if ".." in path.parts:
        srv_utils.raise_bad_request(f"{path} climbs out of the root this server publishes to")
    return f"{str(settings.publish_root).rstrip('/')}/{path}"


def publish_dataset(abspath: pathlib.Path, path: pathlib.Path) -> str:
    """Copy a finished frame out to the publish root, and record where it went.

    *path* is a publish key (see `publish_key`), which is what names the array at
    the destination -- not the path the request spelled.

    Blocking, and run off the event loop and outside the per-dataset lock: it is
    a whole-file upload, and holding either for its duration would stall every
    writer of every other array on the server.

    Nothing here has to lock the frame for the copy.  A complete array is one
    nothing can write to any more -- every slot is claimed, so every write is
    refused -- so what is being read cannot change under the read.
    """
    try:
        import fsspec
    except ImportError:
        srv_utils.raise_bad_request("publishing needs fsspec, which is not installed here")
    destination = publish_destination(path)
    fs, target = fsspec.url_to_fs(destination)
    # Published under a name of its own and moved into place, so that what
    # appears at the destination is a whole frame or nothing.  A reader that
    # polls for the array would otherwise open it mid-copy: the file exists from
    # the first byte written, and a frame is not readable until its last.
    # (An object store makes a write visible only once it completes, so the move
    # is redundant there and costs a server-side copy.  Kept all the same: which
    # backends stream a partial file into view is not something to guess at.)
    # Named for this copy and not for the array: two publishes of one array can
    # overlap -- the background task the last chunk starts, and a client that
    # calls the endpoint to finish an interrupted one -- and neither holds the
    # per-dataset lock across the upload.  On one staging name they would
    # interleave their bytes into a single file and move the wreck into place;
    # on names of their own they write the same thing twice and the second move
    # wins, which is the same file either way
    staging = f"{target}.{uuid.uuid4().hex}.partial"
    parent = target.rsplit("/", 1)[0]
    if parent != target:
        fs.makedirs(parent, exist_ok=True)
    try:
        with open(abspath, "rb") as source, fs.open(staging, "wb") as target_file:
            shutil.copyfileobj(source, target_file)
        fs.mv(staging, target)
    except BaseException:
        # A staging file nothing will ever move is litter, and one per attempt
        # accumulates where one per array overwrote itself
        with contextlib.suppress(Exception):
            fs.rm(staging)
        raise
    with dataset_thread_lock(abspath):
        array = blosc2.open(abspath, mode="a", locking=True)
        with array.schunk.holding_lock():
            array.schunk.vlmeta[PUBLISHED_URL] = destination
            array.schunk.vlmeta[FILL_STATE] = PUBLISHED
        del array
    return destination


def store_chunk(abspath: pathlib.Path, nchunk: int, chunk: bytes) -> dict:
    """Write one chunk into a slot that holds none, and say where the fill is.

    Blocking, and meant to be run off the event loop: it compresses nothing, but
    it locks the frame, writes to it and reads its offsets back.

    The exclusive lock covers the check and the write together, which is what
    makes the refusal a compare-and-swap rather than a race: two writers that
    both find the slot free would otherwise both write it, and the second would
    move every chunk that came after the first.
    """
    with dataset_thread_lock(abspath):
        try:
            array = blosc2.open(abspath, mode="a", locking=True)
        except Exception as exc:
            srv_utils.raise_bad_request(f"{abspath.name} cannot be opened for writing: {exc}")
        if not isinstance(array, blosc2.NDArray):
            srv_utils.raise_bad_request(
                f"{abspath.name} is not an NDArray, so it has no chunks of a shape to write into"
            )
        schunk = array.schunk
        if not 0 <= nchunk < schunk.nchunks:
            srv_utils.raise_not_found(f"{abspath.name} has no chunk {nchunk}")
        try:
            nbytes, _, blocksize = blosc2.get_cbuffer_sizes(chunk)
            typesize = chunk_typesize(chunk)
        except Exception:
            srv_utils.raise_bad_request("the body is not a Blosc2 chunk")
        # A chunk of another geometry would be stored and then read as nonsense, so
        # it is refused here rather than left for whoever reads the array next
        if nbytes != schunk.chunksize:
            srv_utils.raise_bad_request(
                f"the chunk holds {nbytes} bytes where this array's chunks hold {schunk.chunksize}"
            )
        if blocksize != schunk.blocksize:
            srv_utils.raise_bad_request(
                f"the chunk is split into blocks of {blocksize} bytes where this array's are "
                f"{schunk.blocksize}; compress it against the array's blocks"
            )
        # The one part of the geometry the sizes do not carry, and the one whose
        # mismatch is silent: the shuffle filters read and write on a stride of it,
        # so a chunk compressed against another typesize decompresses to the right
        # number of bytes with every one of them in the wrong place -- no error
        # anywhere, just an array of scrambled values
        if typesize != filter_typesize(schunk.typesize):
            srv_utils.raise_bad_request(
                f"the chunk was compressed with a typesize of {typesize} where this array's is "
                f"{schunk.typesize}; compress it against the array's dtype"
            )
        complete = False
        with schunk.holding_lock():
            if not chunk_is_unwritten(schunk, nchunk):
                raise fastapi.HTTPException(
                    status_code=409, detail=f"chunk {nchunk} of {abspath.name} was already written"
                )
            schunk.update_chunk(nchunk, chunk)
            if FILL_NONCE not in schunk.vlmeta:
                # What names *this* array, as against another one that came to sit at
                # the same path with the same size.  A client caching the array reads
                # it from api/info and can tell the two apart, which a size and an
                # mtime cannot always do.  Written once, by whichever writer arrived
                # first, and never again
                schunk.vlmeta[FILL_NONCE] = uuid.uuid4().hex
                # Said out loud rather than left to be inferred from the absence of
                # it, and free here: the same locked region, the same trailer
                schunk.vlmeta[FILL_STATE] = FILLING
            written, nchunks = count_written(abspath)
            state = schunk.vlmeta.get(FILL_STATE, FILLING)
            if written == nchunks and state == FILLING:
                # Exactly once, whichever writer got here: the lock is held, so of two
                # writers that both see the array complete only one makes this move,
                # and that one owns the publishing.  Recorded even where there is
                # nowhere to publish to, because "every slot is claimed" is worth
                # saying on its own: it is what tells a reader the array can no
                # longer change under a cache of it
                complete = bool(settings.publish_root)
                state = PUBLISHING if complete else COMPLETE
                schunk.vlmeta[FILL_STATE] = state
        # Drop the handle before anything reads the file again: a handle left open
        # over a frame another one writes is the stale-handle hazard, and it is silent
        del array, schunk
        return {
            "nchunk": nchunk,
            "written": written,
            "nchunks": nchunks,
            "state": state,
            "publish": complete,
        }


@app.post("/api/chunk/{path:path}")
async def write_chunk(
    path: pathlib.Path,
    nchunk: int,
    request: Request,
    background: fastapi.BackgroundTasks,
    user: db.User = Depends(current_active_user),
):
    """Write one compressed chunk into a slot of a stored array.

    How several writers fill one array at once: each takes the chunks it owns and
    posts them, and the slot itself is the coordination.  A slot nothing was
    written to is free, and a write claims it; a second write to it is refused
    with a 409, so two writers that both believe they own a chunk are resolved by
    the array rather than by anything either of them holds.

    The array has to be laid out already -- `blosc2.uninit` and an upload is what
    makes one, and costs a couple of hundred bytes whatever the array's size --
    and it is never resized here: the geometry a writer compresses against is the
    geometry it was created with.

    Parameters
    ----------
    path : pathlib.Path
        The dataset to write into, in a root the user may write to.
    nchunk : int
        Which chunk of the array to write.
    request : Request
        Carries the compressed chunk as its body.

    Returns
    -------
    dict
        ``nchunk``, and the ``written`` count out of ``nchunks``, so a writer
        sees a fill finish without asking again.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Writing chunks requires authentication")
    if providers.provider_for(path.parts[0]) is not None:
        # As the read side does: a peer's dataset is not ours to write to
        raise fastapi.HTTPException(status_code=404, detail="external roots are non-transitive")

    abspath = get_writable_path(path, user)
    if abspath.suffix != ".b2nd":
        srv_utils.raise_bad_request(
            f"{path} is not a .b2nd array; chunks can only be written to a stored NDArray"
        )
    if not abspath.is_file():
        srv_utils.raise_not_found(f"{path} does not exist; create it before filling it")

    chunk = await request.body()
    if not chunk:
        srv_utils.raise_bad_request("no chunk was sent")
    if settings.quota:
        # The array was laid out empty, so its slots were never charged for: what
        # a fill costs arrives a chunk at a time, and is checked the same way --
        # off a kept walk of the state directory rather than a fresh one, since
        # this runs once per chunk (see `get_disk_usage_written`)
        total_size = get_disk_usage_written(len(chunk))
        if total_size > settings.quota:
            srv_utils.raise_bad_request("Write failed because quota limit has been exceeded.")

    # One lock per dataset in this process, and the frame's own lock across
    # processes: the write below blocks, so it cannot hold the event loop
    lock = dataset_lock(abspath)
    async with lock:
        answer = await concurrency.run_in_threadpool(store_chunk, abspath, nchunk, chunk)
    if settings.quota:
        # Counted only where it is checked, so the two stay paired
        account_chunk_written(len(chunk))
    if answer.pop("publish"):
        # After the response, and outside the lock: the writer that finished the
        # fill should not wait for the upload, and no other writer should either
        background.add_task(publish_dataset, abspath, publish_key(path, user))
    return answer


@app.post("/api/publish/{path:path}")
async def publish(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """Copy a filled array out to the server's publish root.

    What a fill is for: the array is written here a chunk at a time, by as many
    writers as there are chunks, and what leaves is one finished frame -- which
    is exactly what a byte-range reader over an object store wants, and none of
    what writing chunks to one would need.

    Runs by itself when the last chunk of an array lands, where a publish root is
    configured.  It is an endpoint of its own as well, because that automatic run
    can be interrupted: a server that dies mid-upload leaves the array saying
    ``publishing``, and this is what finishes it.

    Returns
    -------
    dict
        Where the array was published to.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Publishing requires authentication")
    abspath = get_writable_path(path, user)
    if not abspath.is_file():
        srv_utils.raise_not_found(f"{path} does not exist")
    key = publish_key(path, user)
    publish_destination(key)  # refuses here if this server publishes nowhere

    lock = dataset_lock(abspath)
    async with lock:
        written, nchunks = await concurrency.run_in_threadpool(count_written, abspath)
        if written != nchunks:
            srv_utils.raise_bad_request(
                f"{path} has {nchunks - written} of its {nchunks} chunks still unwritten; "
                "it is published once it is filled"
            )
    destination = await concurrency.run_in_threadpool(publish_dataset, abspath, key)
    return {"published": destination}


def make_expr(
    expr: models.Cat2LazyArr | types.SimpleNamespace,
    user: db.User,
    remotepath: pathlib.Path | None = None,
) -> str:
    """
    Create a lazy expression dataset in personal space.

    This may raise exceptions if there are problems parsing the dataset name
    or expression, or if the expression refers to operands which have not been
    defined.

    Parameters
    ----------
    name : str
        The name of the dataset to be created without extension.
    expr : str
        The expression to be evaluated.  It must result in a lazy expression.
    operands : dictionary of strings mapping to strings
        The variables used in the expression and which dataset paths they
        refer to.
    remotepath: pathlib.Path
        Where to save the lazy expression. Only valid if name is None.

    Returns
    -------
    str
        The path of the newly created (or overwritten) dataset.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Creating lazy expressions requires authentication")

    # Parse expression
    name = expr.name
    vars = expr.operands
    func = expr.func
    compute = expr.compute
    expression = expr.expression
    if (not expression and not func) or (not remotepath and not name):
        raise ValueError("name/remotepath and expression should not be empty")

    var_dict = {}
    for var, path in vars.items():
        # Detect special roots
        path = pathlib.Path(path)
        abspath = get_writable_path(path, user)
        var_dict[var] = open_b2(abspath, path)

    if func is not None:
        local_ns = {}
        filename = f"<{name}>"  # any unique name
        SAFE_GLOBALS = {
            "__builtins__": {
                name: value for name, value in builtins.__dict__.items() if name != "__import__"
            },
            "np": np,
            "blosc2": blosc2,
        }
        if blosc2._HAS_NUMBA:
            SAFE_GLOBALS["numba"] = numba

        # Register the source so inspect can find it when saving later on
        linecache.cache[filename] = (len(func), None, func.splitlines(True), filename)
        exec(compile(func, filename, "exec"), SAFE_GLOBALS, local_ns)

        if name not in local_ns or not isinstance(local_ns[name], typing.types.FunctionType):
            raise ValueError(f"User code must define a function called {name}")
        arr = blosc2.lazyudf(
            local_ns[name], tuple(var_dict[f"o{i}"] for i in range(len(var_dict))), expr.dtype, expr.shape
        )

    else:
        expression = expression.strip()
        # Create the lazy expression dataset
        arr = blosc2.lazyexpr(expression, var_dict)
        if any(method in arr.expression for method in linalg_funcs):
            compute = True

    # Handle name or path
    if remotepath is not None:  # provided a path
        # Get the absolute path for this user
        urlpath = get_writable_path(remotepath, user)
        abspath = urlpath.parent
        if urlpath.suffix != ".b2nd":
            raise ValueError('If path extension provided must be ".b2nd".')
        path = str(remotepath)
    else:  # just provided a name
        name = name.strip()
        abspath = settings.personal / str(user.id)
        urlpath = f"{abspath / name}.b2nd"
        path = f"@personal/{name}.b2nd"

    abspath.mkdir(exist_ok=True, parents=True)

    if compute:
        arr.compute(urlpath=urlpath, mode="w")
    else:
        arr.save(urlpath=urlpath, mode="w")

    return path


@app.post("/api/upload_lazyarr/{path:path}")
async def upload_lazyarr(
    path: pathlib.Path,
    expr: models.Cat2LazyArr,
    user: db.User = Depends(current_active_user),
) -> str:
    """
    Upload a lazy expression dataset (to any root).

    The JSON request body must contain a "name"=None for the dataset to be created,
    an "expression" to be evaluated, which must result in
    a lazy expression, and an "operands" object which maps variable names used
    in the expression to the dataset paths that they refer to.

    Returns
    -------
    str
        The path of the newly created (or overwritten) dataset.
    """
    try:
        result_path = make_expr(expr, user, path)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise srv_utils.raise_bad_request(f"Invalid name or expression: {exc}") from exc
    except KeyError as ke:
        detail = f"Expression error: {ke.args[0]} is not in the list of available datasets"
        raise srv_utils.raise_bad_request(detail) from ke
    except RuntimeError as exc:
        raise srv_utils.raise_bad_request(f"Runtime error: {exc}") from exc

    return result_path


@app.post("/api/lazyexpr/")
async def lazyexpr(
    expr: models.Cat2LazyArr,
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

    try:
        result_path = make_expr(expr, user)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise srv_utils.raise_bad_request(f"Invalid name or expression: {exc}") from exc
    except KeyError as ke:
        detail = f"Expression error: {ke.args[0]} is not in the list of available datasets"
        raise srv_utils.raise_bad_request(detail) from ke
    except RuntimeError as exc:
        raise srv_utils.raise_bad_request(f"Runtime error: {exc}") from exc

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
    abspath = get_abspath(namepath, user)
    dest_abspath = get_abspath(destpath, user, may_not_exist=True)

    # If destination has not an extension, assume it is a directory
    # If user wants something without an extension, she can add a '.b2' extension :-)
    if dest_abspath.is_dir() or not dest_abspath.suffix:
        dest_abspath /= abspath.name
        destpath /= namepath.name

    if abspath.suffix == ".b2" and dest_abspath.suffix != ".b2":
        dest_abspath = pathlib.Path(f"{dest_abspath}.b2")

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
    if not src.startswith(("@personal", "@shared", "@public")):
        raise fastapi.HTTPException(
            status_code=400,
            detail="Only copying from @personal or @shared or @public roots is allowed",
        )
    # dst should start with a special root
    if not dst.startswith(("@personal", "@shared", "@public")):
        raise fastapi.HTTPException(
            status_code=400,
            detail="Only copying to @personal or @shared or @public roots is allowed",
        )

    namepath, destpath = pathlib.Path(src), pathlib.Path(dst)
    abspath = get_abspath(namepath, user)
    dest_abspath = get_abspath(destpath, user, may_not_exist=True)

    # If destination has not an extension, assume it is a directory
    # If user wants something without an extension, she should add a '.b2' extension
    if dest_abspath.is_dir() or not dest_abspath.suffix:
        dest_abspath /= abspath.name
        destpath /= namepath.name

    if abspath.suffix == ".b2" and dest_abspath.suffix != ".b2":
        dest_abspath = pathlib.Path(f"{dest_abspath}.b2")

    # Not sure if we should allow overwriting, but let's allow it for now
    # if dest_abspath.exists():
    #     raise fastapi.HTTPException(status_code=409, detail="The new path already exists")

    dest_abspath.parent.mkdir(exist_ok=True, parents=True)
    if abspath.is_dir():
        shutil.copytree(abspath, dest_abspath)
    else:
        shutil.copy(abspath, dest_abspath)

    return str(destpath)


def get_writable_path(path: pathlib.Path, user: db.User) -> pathlib.Path:
    """
    Convert a path with special root to an absolute path that can be written to.

    Parameters
    ----------
    path : pathlib.Path
        The path with special root (@personal, @shared, @public)
    user : db.User
        The authenticated user

    Returns
    -------
    pathlib.Path
        The absolute path in the filesystem

    Raises
    ------
    fastapi.HTTPException
        If the path is not in a writable root, or leaves it
    """
    root, *subpath = path.parts
    rootdir = get_rootdir_or_error(root, user)
    # The root is the whole of the authorization: `@personal` is this user's
    # directory and nobody else's, and a path that climbs out of it is asking for
    # a place the root said nothing about.  Refused on the way in, before a
    # `..` becomes a real directory that exists and passes every later check --
    # `Path.joinpath` keeps the segment, and everything downstream (`is_file`,
    # `fsspec.url_to_fs`) resolves it away without ever asking whether it should
    if ".." in subpath:
        srv_utils.raise_bad_request(f"{path} climbs out of {root}, which is where it may write")
    return rootdir / pathlib.Path(*subpath)


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
        The file to upload (from local source).

    Returns
    -------
    str
        The path of the uploaded file.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Uploading requires authentication")

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)
    # We may upload a new file, or replace an existing file
    if abspath.is_dir():
        abspath /= file.filename
        path /= file.filename

    # Check quota
    # TODO To be fair we should check quota later (after compression, zip unpacking etc.)
    data = await file.read()
    if abspath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES:
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
            detail = "Upload failed because quota limit has been exceeded."
            raise fastapi.HTTPException(detail=detail, status_code=400)

    # If regular file, compress it
    abspath.parent.mkdir(exist_ok=True, parents=True)
    if abspath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES | {".h5", ".hdf5"}:
        data = schunk.to_cframe()
        abspath = abspath.with_suffix(abspath.suffix + ".b2")

    # Write the file
    with open(abspath, "wb") as f:
        f.write(data)

    # Return the urlpath
    return str(path)


@app.post("/api/load_from_url/{path:path}")
async def load_from_url(
    path: pathlib.Path,
    remote_url: str = fastapi.Form(...),
    user: db.User = Depends(current_active_user),
):
    """
    Load a file from a url to a root.

    Parameters
    ----------
    path : pathlib.Path
        The path to store the file.
    remote_url : str
        The url from which to get the file (from remote source).

    Returns
    -------
    str
        The path of the uploaded file.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Uploading requires authentication")

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)
    # We may upload a new file, or replace an existing file
    if abspath.is_dir():
        abspath /= remote_url.filename
        path /= remote_url.filename

    # Check quota
    # TODO To be fair we should check quota later (after compression, zip unpacking etc.)
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        response = await client.get(remote_url)
        response.raise_for_status()
    data = response.content

    if abspath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES:
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
            detail = "Upload failed because quota limit has been exceeded."
            raise fastapi.HTTPException(detail=detail, status_code=400)

    # If regular file, compress it
    abspath.parent.mkdir(exist_ok=True, parents=True)
    if abspath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES | {".h5", ".hdf5"}:
        data = schunk.to_cframe()
        abspath = abspath.with_suffix(abspath.suffix + ".b2")

    # Write the file
    with open(abspath, "wb") as f:
        f.write(data)

    # Return the urlpath
    return str(path)


@app.post("/api/append/{path:path}")
async def append_file(
    path: pathlib.Path,
    file: UploadFile,
    user: db.User = Depends(current_active_user),
):
    """
    Append to dataset (along the first axis).

    Parameters
    ----------
    path : pathlib.Path
        The path to dataset to append.
    file : UploadFile
        The dataset to append.

    Returns
    -------
    tuple
        The new shape of the dataset.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Uploading requires authentication")

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)

    # We may upload a new file, or replace an existing file
    if not abspath.is_file():
        detail = "Target file does not exist or is not a file"
        raise fastapi.HTTPException(detail=detail, status_code=400)

    if abspath.suffix not in {".b2nd"}:
        detail = "Target file must be a NDArray"
        raise fastapi.HTTPException(detail=detail, status_code=400)

    # Check quota
    # TODO To be fair we should check quota later (after compression, zip unpacking etc.)
    data = await file.read()
    newsize = len(data)

    if settings.quota:
        oldsize = abspath.stat().st_size

        total_size = get_disk_usage() + oldsize + newsize
        if total_size > settings.quota:
            detail = "Upload failed because quota limit has been exceeded."
            raise fastapi.HTTPException(detail=detail, status_code=400)

    # Append the data
    # The original dataset (open in append mode so it can be resized/written)
    orig = blosc2.open(abspath, mode="a")
    # The data to append is a cframe
    new = blosc2.ndarray_from_cframe(data)
    # Check that the shapes are compatible
    if orig.shape[1:] != new.shape[1:]:
        detail = "The shapes of the original dataset and the data to append are not compatible"
        raise fastapi.HTTPException(detail=detail, status_code=400)
    # Materialize the new data before resizing: resizing the on-disk array
    # invalidates the in-memory cframe array buffer.
    new_data = new[:]
    new_len = new.shape[0]
    # Compute the new shape and resize the original dataset
    result_shape = (orig.shape[0] + new_len,) + orig.shape[1:]
    orig.resize(result_shape)
    # Append the new data to orig along the first axis
    orig[orig.shape[0] - new_len :] = new_data

    # Return the new shape
    return result_shape


@app.post("/api/unfold/{path:path}")
async def unfold_file(
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):
    """
    Unfold a container (zip, tar, hdf5, etc.) into a directory.

    The container is always unfolded into a directory with the same name as the
    container, but without the extension.

    Parameters
    ----------
    path : pathlib.Path
        The path to dataset to unfold.

    Returns
    -------
    str
        The path of the directory where the datasets have been unfolded.
    """
    if not user:
        raise srv_utils.raise_unauthorized("Unfolding requires authentication")

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)

    if not abspath.is_file():
        detail = "Target file does not exist or is not a file"
        raise fastapi.HTTPException(detail=detail, status_code=400)

    # Unfold the container
    dirname = None
    if abspath.suffix in {".h5", ".hdf5"}:
        # Create proxies for each dataset in HDF5 file
        all_dsets = list(hdf5.create_hdf5_proxies(abspath))
        if len(all_dsets) == 0:
            detail = "No arrays found in HDF5 file"
            raise fastapi.HTTPException(detail=detail, status_code=400)
        dirname = abspath.with_suffix("")
    else:
        detail = "Target file must be a zip, tar or hdf5 container"
        raise fastapi.HTTPException(detail=detail, status_code=400)

    # Check quota
    if settings.quota:
        # Get the size of the datasets (proxies) in new directory
        newsize = 0
        if os.path.exists(dirname):
            # Traverse the directory and get the size for all files
            for abspath, _ in srv_utils.walk_files(dirname):
                newsize += os.path.getsize(abspath)
        total_size = get_disk_usage() + newsize
        if total_size > settings.quota:
            # Remove the directory if it exists
            shutil.rmtree(dirname)
            detail = "Unfold failed because quota limit has been exceeded."
            raise fastapi.HTTPException(detail=detail, status_code=400)

    # Return the new directory name
    return path.stem


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

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)

    # If abspath is a directory, remove the contents of the directory
    if abspath.is_dir():
        shutil.rmtree(abspath)
    else:
        # Try to unlink the file. NotADirectoryError: a path descending into a
        # container file (e.g. foo.h5/g) names no real file of its own.
        try:
            srv_utils.unlink_with_b2lock(abspath)
        except (FileNotFoundError, NotADirectoryError):
            # Try adding a .b2 extension
            abspath = abspath.with_suffix(abspath.suffix + ".b2")
            try:
                srv_utils.unlink_with_b2lock(abspath)
            except (FileNotFoundError, NotADirectoryError) as exc:
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

    # Get the absolute path for this user
    abspath = get_writable_path(path, user)

    # Check a file does not exist in the same path
    abspath = pathlib.Path(f"{abspath}.b2")
    if abspath.exists():
        detail = "File exists at the given path"
        raise fastapi.HTTPException(status_code=400, detail=detail)

    # Create the new notebook at the given path
    nb = nbformat.v4.new_notebook()
    file = io.StringIO()
    nbformat.write(nb, file)
    data = file.getvalue().encode()
    srv_utils.compress(data, dst=abspath)

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
        context = {"settings": settings}
        return templates.TemplateResponse(request, "forgot-password-ok.html", context=context)

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
        userid = str(users[0]["id"])
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
        exclude = {"hashed_password"}
        return await srv_utils.alist_users(username, exclude=exclude)

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


def is_fullscreen(request, hx_current_url=None):
    url_to_check = hx_current_url or str(request.url)
    if url_to_check:
        parsed = furl.furl(url_to_check)
        return parsed.query.params.get("fullscreen") == "1"

    return False


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
    if not user:
        # Anonymous users see @public plus provider roots (peers are
        # public-only), not @personal/@shared.
        provider_roots = {r for p in providers.active for r in p.roots()}
        roots = [r for r in roots if r == "@public" or r in provider_roots] or ["@public"]

    # Disk usage
    size = get_disk_usage()
    context = {
        "is_fullscreen": is_fullscreen(request),
        "user_login_enabled": user_login_enabled(),
        "roots_url": make_url(request, "htmx_root_list", {"roots": roots}),
        "username": user.email if user else None,
        # Disk usage
        "usage_total": custom_filesizeformat(size),
        # Prompt
        "cmd_url": make_url(request, "htmx_command"),
        "commands": commands_list,
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
    mounted: list[str] = fastapi.Query([]),
    # Depends
    user: db.User = Depends(optional_user),
):
    seen = set()
    mounted_ok = []
    for path in mounted:
        prefix = path.split("/", 1)[0]
        # A mounted container may live under a classic root or a provider
        # root (peer mount). Stale/bad entries aren't validated here; the
        # path-list expansion skips them silently, same as local ones.
        known = get_rootdir_or_none(prefix, user) is not None or providers.provider_for(prefix) is not None
        if known and path not in seen:
            seen.add(path)
            mounted_ok.append(path)

    context = {
        "checked": roots,
        "mounted": mounted_ok,
        "provider_roots": [r for p in providers.active for r in p.roots()],
        "provider_widgets": [w for p in providers.active for w in p.widgets()],
        "user": user,
    }
    return templates.TemplateResponse(request, "root_list.html", context)


def get_rootdir_or_error(root, user):
    if root not in {"@personal", "@shared", "@public"}:
        raise fastapi.HTTPException(status_code=404)  # NotFound

    if root == "@public":
        return settings.public
    elif root == "@shared" and user:
        return settings.shared
    elif root == "@personal" and user:
        return settings.personal / str(user.id)

    raise fastapi.HTTPException(status_code=401)  # Unauthorized


def get_rootdir_or_none(root, user):
    if root == "@public":
        return settings.public
    elif root == "@shared" and user:
        return settings.shared
    elif root == "@personal" and user:
        return settings.personal / str(user.id)

    return None


def filter_roots(roots, user):
    for root in roots:
        rootdir = get_rootdir_or_none(root, user)
        if rootdir is not None:
            yield root, rootdir


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

    def add_dataset(path, abspath, mountable=False, size=None):
        datasets.append(
            {
                "name": "_",
                "path": path,
                "size": abspath.stat().st_size if size is None else size,
                "url": make_url(request, "html_home", path=path, query=query),
                "label": truncate_path(path),
                "mountable": mountable,
            }
        )

    for root, rootdir in filter_roots(roots, user):
        for abspath, relpath in srv_utils.walk_files(rootdir):
            if relpath.suffix == ".b2":
                relpath = relpath.with_suffix("")
            path = f"{root}/{relpath}"
            # A .b2z/.h5 holding a browsable container shows as a single
            # mountable row; its leaves are browsed once it's mounted as a
            # virtual root. A corrupt or non-container file (uploads aren't
            # validated) falls through to a plain row instead of crashing the
            # whole listing.
            if relpath.suffix in srv_utils.BLOSC2_CONTAINER_SUFFIXES and srv_utils.is_container_file(
                abspath
            ):
                if search in path:
                    add_dataset(path, abspath, mountable=True)
                continue
            if search in path:
                add_dataset(path, abspath)

    # Virtual roots: mounted .b2z/.h5 containers, expanded into leaves.
    for root in roots:
        proot = pathlib.PurePosixPath(root)
        if proot.suffix not in srv_utils.BLOSC2_CONTAINER_SUFFIXES:
            continue  # classic roots handled by filter_roots above
        provider = providers.provider_for(proot.parts[0])
        if provider is not None:
            # A mounted peer container: expand via the peer's own deep
            # listing. Stale/offline mounts skip silently, same rule as the
            # local-container comment below.
            try:
                member_rows = await provider.rows(proot.parts[0], "/".join(proot.parts[1:]))
            except providers.ProviderError:
                continue
            for name, size, _kind in member_rows:
                leaf_path = f"{root}/{name}"
                if search in leaf_path:
                    add_dataset(leaf_path, None, size=size or 0)
            continue
        rootdir = get_rootdir_or_none(proot.parts[0], user)
        if rootdir is None:
            continue
        abspath = (rootdir / pathlib.Path(*proot.parts[1:])).resolve()
        if rootdir.resolve() not in abspath.parents:
            continue
        # `root` is untrusted (client localStorage): a stale, non-container, or
        # corrupt path must skip silently, not 500 the whole listing.
        # open_container() swallows the underlying open errors itself.
        container = srv_utils.open_container(abspath)
        if container is None:
            continue
        try:
            for key in container.leaves():
                leaf_path = f"{root}{key}"
                if search in leaf_path:
                    add_dataset(leaf_path, abspath, size=container.leaf_size(key))
        finally:
            container.close()

    # Provider roots (external/virtual roots, e.g. peer mounts): @<name>
    # browsed via the provider's cached catalog.
    for root in roots:
        provider = providers.provider_for(root)
        if provider is None:
            continue

        for key, size, kind in await provider.rows(root):
            path = f"{root}/{key}"
            if search in path:
                datasets.append(
                    {
                        "name": "_",
                        "path": path,
                        "size": size,
                        "url": make_url(request, "html_home", path=path, query=query),
                        "label": truncate_path(path),
                        # Bare TreeStore .b2z rows are mountable, mirroring
                        # the local rule; CTable .b2z rows open directly.
                        "mountable": key.endswith(".b2z") and kind == "container",
                    }
                )

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
            provider = providers.provider_for(root)
            if provider is not None:
                # Peer leaf: size from its (memoized) api/info; failures skip
                # silently like the local suppress below.
                with contextlib.suppress(Exception):
                    info = await provider.info(root, "/".join(segments[2:]))
                    size = (
                        (info.get("schunk") or {}).get("cbytes")
                        or info.get("cbytes")
                        or info.get("size")
                        or 0
                    )
                    add_dataset(path, None, size=size)
                rootdir = None
            else:
                rootdir = get_rootdir_or_none(root, user)
            if rootdir is not None:
                # Path may descend into a container (e.g. an unmounted TreeStore
                # leaf); stat comes from the container file, but size is the
                # leaf's own (not the whole container's).
                container_path, inner_key = srv_utils.split_container_path(path)
                leaf_size = None
                if inner_key is not None:
                    abspath = rootdir / pathlib.Path(*container_path.parts[1:])
                    container = srv_utils.open_container(abspath)
                    if container is not None:
                        leaf_size = container.leaf_size(inner_key)
                        container.close()
                else:
                    relpath = pathlib.Path(*segments[2:])
                    abspath = rootdir / relpath
                    if abspath.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES:
                        abspath = pathlib.Path(f"{abspath}.b2")

                with contextlib.suppress(FileNotFoundError, NotADirectoryError):
                    add_dataset(path, abspath, size=leaf_size)

    # Assign names to datasets
    datasets = sorted(datasets, key=lambda x: x["path"])
    for dataset in datasets:
        dataset["name"] = next(names)

    # Render template
    search_url = make_url(request, "htmx_path_list", {"roots": roots})
    context = {
        "datasets": datasets,
        "search_text": search,
        "search_url": search_url,
        "user": user,
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


def _model_from_info(info):
    # B serves the JSON of the same pydantic models we build locally;
    # round-trip it into the right one so the attribute access in the
    # templates below works unchanged.
    # `kind` first, where the peer says it: a CTable's metadata has neither a
    # shape nor a size, so anything reading it off the fields alone falls
    # through to `File` and raises on the `size` it does not carry.  Asked for
    # rather than read off `info` directly, because `RootProvider.info` is not
    # promised to be a dict (`get_info` guards its own use of it the same way)
    # and a `.get` on something else would 500 the panel this renders
    if isinstance(info, dict) and info.get("kind") == "ctable":
        return srv_utils.get_model_from_obj(info, models.CTableMetadata)
    if "shape" in info:
        return srv_utils.get_model_from_obj(info, models.Metadata)
    elif "cparams" in info:
        return srv_utils.get_model_from_obj(info, models.SChunk)
    elif "nfiles" in info:
        return srv_utils.get_model_from_obj(info, models.Directory)
    else:
        return srv_utils.get_model_from_obj(info, models.File)


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

    if hx_trigger != "meta":
        push_url = make_url(request, "html_home", path=path)
        # Keep query
        current_query = furl.furl(hx_current_url).query
        if current_query:
            push_url = f"{push_url}?{current_query.encode()}"
    else:
        push_url = None

    # Read metadata (a path may descend into a container, e.g. a TreeStore
    # .b2z or .h5 leaf)
    provider = providers.provider_for(path.parts[0])
    if provider is not None:
        try:
            info = await provider.info(path.parts[0], "/".join(path.parts[1:]))
        except providers.ProviderError as exc:
            raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc
        meta = _model_from_info(info)
    else:
        abspath, inner_key = split_and_resolve(path, user)
        try:
            if inner_key is not None:
                meta = srv_utils.container_member_info(abspath, inner_key)
                if meta is None:
                    srv_utils.raise_not_found()
            else:
                meta = srv_utils.read_metadata(abspath)
        except FileNotFoundError:
            # e.g. a bare root (@personal) or another directory with no dataset of its own
            raise fastapi.HTTPException(status_code=404) from None  # NotFound

    # Context
    current_url = push_url or hx_current_url
    tabs = []
    context = {
        "current_url": current_url,
        "is_fullscreen": is_fullscreen(request, current_url),
        "can_delete": user and path.parts[0] in {"@personal", "@shared", "@public"},
        "meta": meta,
        "path": path,
        "tabs": tabs,
    }

    # Tabs: Display (b2)
    mimetype = guess_type(path)
    if mimetype:
        type_, _ = mimetype.split("/")
        known_mimetypes = {"application/json", "application/pdf", "application/x-ipynb+json"}
        if type_ in {"image", "text"} or mimetype in known_mimetypes:
            tabs.append(
                {
                    "name": "display",
                    "url": url(f"display/{path}"),
                    "label": "Display",
                }
            )

    # Tabs: Display (b2nd, b2z)
    is_ctable = getattr(meta, "kind", None) == "ctable"
    if hasattr(meta, "shape") or is_ctable:
        context["data_url"] = make_url(request, "htmx_path_view", path=path)
        context["shape"] = meta.shape if hasattr(meta, "shape") else (meta.nrows,)
        tabs.append(
            {
                "name": "data",
                "label": "Display",
                "include": "includes/info_data.html",
            }
        )

    # Tabs: Main
    tabs.append(
        {
            "name": "main",
            "label": "Meta",
            "include": "includes/info_metadata.html",
        }
    )

    # Tabs: plugin defined
    vlmeta = getattr(getattr(meta, "schunk", meta), "vlmeta", {})
    contenttype = vlmeta.get("contenttype") or guess_dset_ctype(path, meta)
    plugin = plugins.get(contenttype)
    if plugin:
        tabs.append(
            {
                "name": "plugin",
                "label": plugin.label,
                "url": url(f"plugins/{plugin.name}/display/{path}"),
            }
        )

    # Render response
    response = templates.TemplateResponse(request, "info.html", context=context)

    # Push URL only when clicked, not on load/reload
    if push_url is not None:
        response.headers["HX-Push-Url"] = push_url

    return response


# Added mtime to implicitly check when underlying files are changed, and so can't use cache (see issue #207)
def get_filtered_array(abspath, path, filter, sortby, mtime, inner_key=None):
    """One entry per distinct question, however the caller spells the call.

    `lru_cache` keys on the shape of the call as well as on its values: a
    keyword argument and the same argument positionally are two keys, and so are
    `sortby=None` and `sortby=""`, which name the same order.  `api/fetch` and
    the web view ask this the same question in both of those two ways, so a
    dataset used from both used to be computed and held twice -- and to take
    two of the sixteen entries there are.
    """
    sortby = sortby.strip() if sortby else None
    return _filtered_array(abspath, path, filter, sortby or None, mtime, inner_key)


@functools.lru_cache(maxsize=16)
def _filtered_array(abspath, path, filter, sortby, mtime, inner_key):
    # Always sorts ascending (so "col asc" and "col desc" share one cache entry);
    # descending is rendered by reading a tail window of this order in reverse.
    if inner_key is not None:
        arr = srv_utils.open_container_member(abspath, inner_key)
        if arr is None:
            raise ValueError("Cannot open container member")
        if filter and isinstance(arr, blosc2.NDArray):
            # blosc2's where fastpath re-opens the operand's urlpath, which for
            # a TreeStore leaf is the whole .b2z ("Key must be a string" error);
            # detach with an in-memory copy (cache-bounded, like .compute() below).
            arr = arr.copy()
    else:
        arr = open_b2(abspath, path)

    if filter and isinstance(arr, hdf5.HDF5Proxy):
        # HDF5Proxy supports slicing only; no string-indexed LazyExpr yet.
        raise ValueError("Filtering is not supported for HDF5-backed datasets")

    if isinstance(arr, blosc2.CTable):
        if filter:
            arr = arr.where(filter)
        if sortby:
            arr = arr.sort_by(sortby, view=True)
        return arr, None

    has_ndfields = hasattr(arr, "fields") and arr.fields != {}
    if not has_ndfields:
        raise ValueError("Filtering/sorting is not supported for this dataset type")
    idx = None

    # Filter rows only for NDArray with fields
    if filter:
        # Check whether filter is the name of a field
        if filter in arr.fields:
            if arr.dtype.fields[filter][0] == bool:  # noqa: E721
                # If boolean, give the filter a boolean expression
                filter = f"{filter} == True"
            else:
                raise IndexError("Filter should be a boolean expression")

        # Let's create a LazyExpr with the filter
        larr = arr[filter]
        # TODO: do some benchmarking to see if this is worth it
        idx = larr.argsort(sortby)
        # TODO: do some benchmarking to see if a numpy array is faster
        # but be aware that this will consume more memory (uncompressed)
        # idx = larr.argsort(sortby)[:]
        arr = larr.sort(sortby).compute()
    elif sortby:
        # NDArray with fields; no need for the compute step
        idx = arr.argsort(sortby)
        arr = arr.sort(sortby)

    return arr, idx


def _desc_window(total, start, size):
    """Map window [start, start+size) of a descending view onto the ascending order."""
    return max(total - start - size, 0), total - start


def _is_ctable_like(arr):
    """True for real CTables and provider-backed views that render through
    the CTable grid (ViewHandle.array is duck-typed by design)."""
    return isinstance(arr, blosc2.CTable) or (
        hasattr(arr, "nrows") and hasattr(arr, "schema_dict") and hasattr(arr, "slice")
    )


def _header_sort_links(displayed_fields, sortby, sortdir):
    """Per-column htmx `hx-vals` payload cycling the sort: ascending -> descending -> unsorted.

    Headers post with hx-include of the live form; these values override the
    form's hidden sortby/sortdir inputs (htmx hx-vals take precedence).
    """
    links = {}
    for col in displayed_fields:
        if sortby != col:
            nxt = {"sortby": col, "sortdir": "asc"}
        elif sortdir != "desc":
            nxt = {"sortby": col, "sortdir": "desc"}
        else:
            nxt = {"sortby": "", "sortdir": ""}
        links[col] = json.dumps(nxt)
    return links


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
    sortdir: typing.Annotated[str, Form()] = "",
    # Depends
    user: db.User = Depends(optional_user),
):
    filter = filter.strip()
    sortby = sortby.strip()
    if sortby and fields and sortby not in fields:
        # The sorted column was removed from the displayed fields; without a
        # header to click, the sort would be stuck. Clear it instead.
        sortby = sortdir = ""
    sort_desc = bool(sortby) and sortdir == "desc"

    provider = providers.provider_for(path.parts[0])
    async with contextlib.AsyncExitStack() as stack:
        if provider is not None:
            hdf5_member = False
            idx = None
            if filter or sortby:
                # ponytail: same gap as api/fetch — no filter/sort plumbing
                # for provider-backed proxies yet.
                return htmx_error(request, "Filtering/sorting is not supported on external roots yet.")
            try:
                handle = await stack.enter_async_context(
                    provider.open_view(path.parts[0], "/".join(path.parts[1:]))
                )
            except providers.ProviderError as exc:
                return htmx_error(request, exc.detail or "provider error")
            arr = handle.array
        else:
            abspath, inner_key = split_and_resolve(path, user)
            hdf5_member = inner_key is not None and abspath.suffix in srv_utils.HDF5_SUFFIXES
            # ponytail: HDF5 filter needs LazyExpr plumbing on HDF5Proxy; sort works via .indices()/.sort()
            if hdf5_member and filter:
                return htmx_error(request, "Filtering is not supported for HDF5 container members.")

        if provider is not None:
            pass  # arr/idx already set by the provider branch above
        elif inner_key is not None and not (filter or sortby):
            arr = srv_utils.open_container_member(abspath, inner_key)
            if arr is None:
                return htmx_error(request, "Cannot open container member.")
            idx = None
        elif filter or sortby:
            try:
                mtime = abspath.stat().st_mtime
                arr, idx = get_filtered_array(abspath, path, filter, sortby, mtime, inner_key)
            except TypeError as exc:
                return htmx_error(request, f"Error in filter: {exc}")
            except NameError as exc:
                return htmx_error(request, f"Unknown field: {exc}")
            except KeyError as exc:
                return htmx_error(request, f"Unknown field: {exc}")
            except ValueError as exc:
                return htmx_error(request, f"ValueError: {exc}")
            except SyntaxError as exc:
                return htmx_error(request, f"SyntaxError: {exc}")
            except IndexError as exc:
                return htmx_error(request, f"IndexError: {exc}")
            except (RuntimeError, OSError) as exc:
                # e.g. a corrupt member frame in a .b2z (blosc2 RuntimeError) or a
                # truncated HDF5 dataset (h5py OSError).
                return htmx_error(request, f"Error reading dataset: {exc}")
            except AttributeError as exc:
                return htmx_error(
                    request,
                    f"Invalid filter: {exc}. Only expressions can be used as filters, not field names.",
                )
        else:
            try:
                arr = open_b2(abspath, path)
            except ValueError:
                return htmx_error(request, "Cannot open array; missing operand?, unknown data source?")
            idx = None

        if _is_ctable_like(arr):
            schema = arr.schema_dict()
            cols = [c["name"] for c in schema.get("columns", [])]
            fields = fields or cols[:5]
            nrows = arr.nrows
            size = sizes[0] if sizes else min(nrows, 10)
            start = index[0] if index else 0
            stop = min(start + size, nrows)
            mod = nrows % size if size else 0
            start_max = nrows - (mod or size) if size else 0
            inputs = [
                {
                    "start": start,
                    "start_max": max(start_max, 0),
                    "size": size,
                    "size_max": nrows,
                    "with_size": True,
                }
            ]
            tags = list(range(start, stop))

            def cell(value):
                if isinstance(value, bytes):
                    return value.decode(errors="replace")
                if isinstance(value, np.generic):
                    return value.item()
                return value

            if provider is not None:
                # Materialize exactly this row window locally before the sync
                # slice below (the NDArray branch's prefetch, CTable-shaped).
                # sort_desc can't be true here: filter/sortby already errored
                # for providers above.
                try:
                    await handle.prefetch((slice(start, stop),))
                except providers.ProviderError as exc:
                    return htmx_error(request, exc.detail or "provider error")

            if sort_desc:
                # arr is ascending-sorted; read its tail and reverse for descending order.
                lo, hi = _desc_window(nrows, start, size)
                window = list(arr.slice(lo, hi))[::-1]
            else:
                window = arr.slice(start, stop)
            rows = [fields] + [[cell(row[f]) for f in fields] for row in window]
            context = {
                "view_url": make_url(request, "htmx_path_view", path=path),
                "inputs": inputs,
                "rows": rows,
                "cols": cols,
                "fields": fields,
                "filter": "",
                "sortby": sortby,
                "sortdir": sortdir,
                "shape": (nrows,),
                "tags": tags,
                "filterable": False,
                "header_sort": _header_sort_links(fields, sortby, sortdir),
            }
            return templates.TemplateResponse(request, "info_view.html", context)

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
                elif sort_desc:
                    # idx is ascending-sorted; read its tail and reverse for descending order.
                    lo, hi = _desc_window(size_max, start, size)
                    tags.append(list(reversed(idx[lo:hi])))
                else:
                    tags.append(list(idx[start:stop]))

        if provider is not None:
            # Prefetch exactly the window into the local cache so the sync
            # reads below are local cache hits, not blocking HTTP on the
            # event loop.
            window = tuple(
                slice(st, min(st + sz, dim)) if i in view_dims else st
                for i, (st, sz, dim) in enumerate(zip(index, sizes, shape, strict=False))
            )
            try:
                await handle.prefetch(window)
            except providers.ProviderError as exc:
                return htmx_error(request, exc.detail or "provider error")

        if has_ndfields:
            cols = list(arr.fields.keys())
            fields = fields or cols[:5]
            idxs = [cols.index(f) for f in fields]
            rows = [fields]

            # Get array view
            if ndims >= 2:
                # One combined slice: on a provider-backed Proxy, arr[index[:-1]]
                # alone would fetch that whole sub-array, not just the window.
                i, isize = index[-1], sizes[-1]
                arr = arr[(*index[:-1], slice(i, i + isize))]
                arr = arr.tolist()
            elif ndims == 1:
                i, isize = index[0], sizes[0]
                if idx is not None and sort_desc:
                    # arr is ascending-sorted; read its tail and reverse for descending order.
                    lo, hi = _desc_window(shape[0], i, isize)
                    arr = list(reversed(arr[lo:hi].tolist()))
                else:
                    arr = arr[i : i + isize]
                    arr = arr.tolist()
            else:
                arr = [arr[()].tolist()]
            rows += [[row[i] for i in idxs] for row in arr]
        else:
            # Get array view
            cols = None
            if ndims >= 2:
                # One combined slice (see the fields case above).
                i, isize = index[-2], sizes[-2]
                j, jsize = index[-1], sizes[-1]
                arr = arr[(*index[:-2], slice(i, i + isize), slice(j, j + jsize))]
                rows = [tags[-1]] + list(arr)
            elif ndims == 1:
                i, isize = index[0], sizes[0]
                arr = [arr[i : i + isize]]
                rows = [tags[-1]] + list(arr)
            else:
                val = arr[()]
                # blosc2.NDArray[()] returns a 0-d ndarray, not a scalar
                # (HDF5Proxy[()] already returns a scalar)
                if isinstance(val, np.ndarray):
                    val = val.item()
                arr = [[val]]
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
            "sortdir": sortdir,
            "shape": shape,
            "tags": tags if len(tags) == 0 else tags[0],
            "filterable": not hdf5_member and provider is None,
            "header_sort": _header_sort_links(fields, sortby, sortdir) if cols else {},
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
        users = [user["email"] for user in lusers]
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
        response = responses.Response(status_code=204)
        response.headers["HX-Refresh"] = "true"
        return response


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


class UnfoldCmd:
    """Unfold archive file (e.g. HDF5)."""

    names = ("unfold",)
    expected = "unfold <path>"
    nargs = 2

    @classmethod
    async def call(cls, request, user, argv, operands, hx_current_url):
        path = operands.get(argv[1], argv[1])
        path = pathlib.Path(path)
        _ = await unfold_file(path, user)
        # Redirect to display the archive file (the unfolded directory will be next to it)
        url = make_url(request, "html_home", path=path)
        return htmx_redirect(hx_current_url, url)


commands_list = [
    AddUserCmd,
    DelUserCmd,
    ListUsersCmd,
    CopyCmd,
    MoveCmd,
    RemoveCmd,
    AddNotebookCmd,
    UnfoldCmd,
]

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
        compute = operator == ":="
        try:
            result_name, expr = command.split(operator, maxsplit=1)
            alt_ops = {}
            if "#" in expr:  # get alternative operands
                expr, alt_ops = expr.split("#", maxsplit=1)
                alt_ops = ast.literal_eval(alt_ops.strip())  # convert str to dict
            opkeys = blosc2.get_expr_operands(expr.strip())  # get operands from expression
            operands = {k: operands[k] for k in opkeys}
            for k, v in alt_ops.items():
                operands[k] = v  # overwrite or add operands if necessary

            expr = {
                "name": result_name,
                "expression": expr,
                "compute": compute,
                "operands": operands,
                "func": None,
                "dtype": None,
                "shape": None,
            }
            result_path = make_expr(types.SimpleNamespace(**expr), user)
            url = make_url(request, "html_home", path=result_path)
            return htmx_redirect(hx_current_url, url)
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
            traceback.print_exc()
            return htmx_error(request, f'Error in "{command}" command: {exc}')

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

    if name not in {"@personal", "@shared", "@public"}:
        raise fastapi.HTTPException(status_code=404)  # NotFound

    if name == "@personal":
        path = settings.personal / str(user.id)
    elif name == "@shared":
        path = settings.shared
    elif name == "@public":
        path = settings.public

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
            if not (path / member).is_dir() and member.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES
        ]
        for member in new_members:
            srv_utils.compress_file(path / member)

        # We are done, redirect to home, and show the new files, starting with the first one
        first_member = next((m for m in new_members), None)
        path = f"{name}/{first_member}"
        return htmx_redirect(hx_current_url, make_url(request, "html_home", path=path), root=name)

    if suffix in [".h5", ".hdf5"]:
        pass
    elif filename.suffix not in srv_utils.BLOSC2_NATIVE_SUFFIXES:
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
    root = path.parts[0]
    if root not in {"@personal", "@shared", "@public"}:
        return fastapi.HTTPException(status_code=400)

    parts = list(path.parts)
    if root == "@personal":
        parts[0] = str(user.id)
        path = pathlib.Path(*parts)
        abspath = settings.personal / path
    elif root == "@shared":
        path = pathlib.Path(*parts[1:])
        abspath = settings.shared / path
    elif root == "@public":
        path = pathlib.Path(*parts[1:])
        abspath = settings.public / path

    # Remove
    if abspath.suffix in [".h5", ".hdf5"]:
        pass
    elif abspath.suffix not in {".b2frame", ".b2nd"}:
        abspath = abspath.with_suffix(abspath.suffix + ".b2")
        if not abspath.exists():
            return fastapi.HTTPException(status_code=404)

    srv_utils.unlink_with_b2lock(abspath)

    # Redirect to home
    url = make_url(request, "html_home")
    return htmx_redirect(hx_current_url, url, root=root)


async def get_container(path, user):
    abspath = get_abspath(path, user)
    return open_b2(abspath, path)


async def get_file_content(path, user, decompress=True, include_cache=True):
    """
    This helper function returns the contents of the file at the given path, as a byte
    string (if the given user has acces to it).

    There are 2 different cases:

    - Datasets (b2nd, b2frame and h5) are returned as they are stored (compressed)
    - Regular files are returned uncompressed

    This function is used when we need to send data to a regular client (e.g. a browser).
    Such a client does not know how to uncompress .b2 files, so we must send these files
    uncompressed.

    Our own client will use instead the fetch API, because it sends the .b2 files
    compressed, and then it's able to uncompress them in the client side.
    """
    abspath = get_abspath(path, user)
    suffix = abspath.suffix

    if suffix in {".b2frame", ".b2nd"}:
        reference = remote_proxy.inspect(abspath)
        if reference is not None:
            lock = dataset_lock(abspath)
            async with lock:
                carrier, payload = remote_proxy.inspect(abspath)
                return await concurrency.run_in_threadpool(
                    lambda: remote_proxy.export_cframe(carrier, payload, include_cache=include_cache)
                )

    if suffix == ".b2":
        # Blosc2 compressed files are decompressed
        container = open_b2(abspath, path)
        if decompress:
            return container[:]
        else:
            return container.to_cframe()
    elif suffix in {".b2frame", ".b2nd"}:
        # HDF5Proxy files are all zeros, so we have to open them (this will read the data
        # from the .h5 file)
        container = open_b2(abspath, path)
        if isinstance(container, hdf5.HDF5Proxy):
            return container.to_cframe()

    # Other files, not Blosc2 compressed
    # HDF5 files are not compressed with Blosc2
    with open(abspath, "rb") as file:
        return file.read()


async def get_image(path, user):
    content = await get_file_content(path, user)
    return PIL.Image.open(io.BytesIO(content))


def resize_image(img, width):
    if width and img.width > width:
        height = (img.height * width) // img.width
        img = img.resize((width, height))

    img_file = io.BytesIO()
    img.save(img_file, format="PNG")
    # img.save(img_file, format="WebP", lossless=True, quality=100)
    # img.save(img_file, format="AVIF", lossless=True)
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
    if mimetype == "application/json":
        content = await get_file_content(path, user)
        content = content.decode("utf-8")
        content = json.dumps(json.loads(content), indent=2)
        return f"<pre>{content}</pre>"
    elif mimetype == "application/pdf":
        data = f"{url('api/preview/')}{path}"
        return f'<object data="{data}" type="application/pdf" class="w-100" style="height: 768px"></object>'
    elif mimetype == "application/x-ipynb+json":
        href = url(f"static/jupyterlite/notebooks/index.html?path={path}")
        src = f"{url('api/preview/')}{path}"
        return (
            f'<a href="{href}" target="_blank" class="btn btn-primary mb-1"><i class="fa-solid fa-gear"></i> Run</a>'
            f'<iframe src="{src}" class="w-100" height="768px"></iframe>'
        )
    elif mimetype.startswith("text/"):
        content = await get_file_content(path, user)
        content = content.decode("utf-8")
        if mimetype == "text/markdown":
            return markdown.markdown(content)

        try:
            lexer = pygments.lexers.get_lexer_for_mimetype(mimetype)
        except pygments.util.ClassNotFound:
            lexer = None

        if lexer:
            formatter = pygments.formatters.HtmlFormatter(style="default")
            return pygments.highlight(content, lexer, formatter)
        else:
            return f"<pre>{content}</pre>"
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
            "created": srv_utils.epoch_to_iso(stat.st_ctime),
            "format": None if content is None else "json",
            "hash": None,
            "hash_algorithm": None,
            "last_modified": srv_utils.epoch_to_iso(stat.st_mtime),
            "mimetype": None,
            "name": pathlib.Path(relpath).name,
            "path": relpath,
            "size": None,
            "type": "directory",
            "writable": False,
        }

    content = []
    if len(parts) == 0:
        roots = {"@personal", "@shared", "@public"}
        for root, rootdir in filter_roots(roots, user):
            if root == "@personal":
                rootdir.mkdir(exist_ok=True)

            content.append(directory(rootdir, root))

        dir_abspath = rootdir.parent
        dir_relpath = ""
    else:
        # Get absolute and relative paths to the directory
        dir_abspath = get_writable_path(path, user)
        dir_relpath = path

        for abspath, relpath in srv_utils.iterdir(dir_abspath):
            relpath = path / relpath
            if abspath.is_dir():
                content.append(directory(abspath, relpath))
            elif abspath.is_file():
                if relpath.suffix == ".b2":
                    relpath = relpath.with_suffix("")

                mimetype = guess_type(relpath)
                if mimetype == "application/x-ipynb+json":
                    content_type = "notebook"
                    writable = bool(user)
                else:
                    content_type = "file"
                    writable = False

                stat = abspath.stat()
                content.append(
                    {
                        "content": None,
                        "created": srv_utils.epoch_to_iso(stat.st_ctime),
                        "format": None,
                        "hash": None,
                        "hash_algorithm": None,
                        "last_modified": srv_utils.epoch_to_iso(stat.st_mtime),
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
        content = await get_file_content(path, user)
        if guess_type(path) == "application/x-ipynb+json":
            content = inject_pyodide_bootstrap_cell(content)
        yield content

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
    # Load configuration (args)
    parser = utils.get_server_parser()
    args = parser.parse_args()
    conf = utils.get_server_conf(args.conf)
    utils.config_log(args, conf)
    remote_proxy.configure(conf)

    # Directories
    statedir = args.statedir or pathlib.Path(conf.get(".statedir", "_caterva2/state"))
    settings.statedir = statedir.resolve()
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
    model = models.Server()
    settings.database = srv_utils.Database(settings.statedir / "db.json", model)

    # Register display plugins (delay module load)
    try:
        from .plugins import image, tomography  # When used as module
    except ImportError:
        from caterva2.services.plugins import image, tomography  # When used as script

    # tomography
    app.mount(f"/plugins/{tomography.name}", tomography.app)
    plugins[tomography.contenttype] = tomography
    tomography.init(settings.urlbase)
    # image
    app.mount(f"/plugins/{image.name}", image.app)
    plugins[image.contenttype] = image
    image.init(settings.urlbase)

    # Discover and mount root providers (external/virtual root sources)
    providers.active[:] = providers.discover(settings)
    for p in providers.active:
        if p.router is not None:
            app.include_router(p.router, prefix=f"/provider/{p.name}")

    # Mount media
    media = settings.statedir / "media"
    media.mkdir(exist_ok=True, parents=True)
    app.mount("/media", StaticFiles(directory=media), name="media")
    templates.env.globals["brand"] = {
        "logo": brand_logo(),
    }

    # Run
    root_path = str(furl.furl(settings.urlbase).path)
    listen = args.listen or utils.Socket(conf.get(".listen", "localhost:8000"))
    if listen.uds:
        uvicorn.run(app, uds=listen.uds, root_path=root_path)
    else:
        uvicorn.run(app, host=listen.host, port=listen.port, root_path=root_path)


if __name__ == "__main__":
    main()
