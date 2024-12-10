###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import json
import pathlib
import typing

# Requirements
import blosc2
import fastapi
import fastapi_websocket_pubsub
import safer

# Project
from caterva2 import models


def compress_file(path):
    with open(path, "rb") as src:
        data = src.read()
        schunk = blosc2.SChunk(data=data)
        data = schunk.to_cframe()
        path2 = f"{path}.b2"
    with open(path2, "wb") as dst:
        dst.write(data)

    path.unlink()


def cache_lookup(cachedir, path, may_not_exist=False):
    if cachedir == path:
        # Special case for the cache root
        return path
    path = pathlib.Path(path)
    if (cachedir / path).is_dir():
        # Special case for directories
        return cachedir / path

    if path.suffix not in {".b2frame", ".b2nd"} and not may_not_exist:
        if path.is_file():
            compress_file(path)
        path = f"{path}.b2"

    return get_abspath(cachedir, path, may_not_exist)


def get_model_from_obj(obj, model_class, **kwargs):
    if isinstance(obj, dict):

        def getter(o, k):
            return o[k]
    else:
        getter = getattr

    data = kwargs.copy()
    for key, info in model_class.model_fields.items():
        if key not in data:
            try:
                value = getter(obj, key)
            except AttributeError:
                continue

            if info.annotation is str:
                value = str(value)

            data[key] = value

    return model_class(**data)


def read_metadata(obj, cache=None, personal=None, shared=None, public=None):
    # Open dataset
    if isinstance(obj, pathlib.Path):
        path = obj
        if not path.is_file():
            raise FileNotFoundError(f'File "{path}" does not exist or is a directory')

        assert path.suffix in {".b2frame", ".b2nd", ".b2"}
        obj = blosc2.open(path)
        stat = path.stat()
        mtime = stat.st_mtime
    else:
        mtime = None

    # Read metadata
    if isinstance(obj, blosc2.ndarray.NDArray):
        array = obj
        cparams = get_model_from_obj(array.schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        schunk = get_model_from_obj(array.schunk, models.SChunk, cparams=cparams)
        return get_model_from_obj(array, models.Metadata, schunk=schunk, mtime=mtime)
    elif isinstance(obj, blosc2.schunk.SChunk):
        schunk = obj
        cparams = get_model_from_obj(schunk.cparams, models.CParams)
        cparams = reformat_cparams(cparams)
        return get_model_from_obj(schunk, models.SChunk, cparams=cparams, mtime=mtime)
    elif isinstance(obj, blosc2.LazyExpr):
        operands = operands_as_paths(obj.operands, cache, personal, shared, public)
        return get_model_from_obj(obj, models.LazyArray, operands=operands, mtime=mtime)
    else:
        raise TypeError(f"unexpected {type(obj)}")


def reformat_cparams(cparams):
    cparams.__setattr__(
        "filters, meta",
        [
            (cparams.filters[i], cparams.filters_meta[i])
            for i in range(len(cparams.filters))
            if cparams.filters[i] != blosc2.Filter.NOFILTER
        ],
    )
    #   delattr(cparams, 'filters')
    #   delattr(cparams, 'filters_meta')
    return cparams


def get_relpath(ndarr, cache, personal, shared, public):
    path = pathlib.Path(ndarr.schunk.urlpath)
    if shared is not None and path.is_relative_to(shared):
        # Shared: /.../<shared>/<subpath> to <path> (i.e. no change)
        return path
    elif public is not None and path.is_relative_to(public):
        # Shared: /.../<public>/<subpath> to <path> (i.e. no change)
        return path
    try:
        # Cache: /.../<root>/<subpath> to <root>/<subpath>
        path = path.relative_to(cache)
    except ValueError:
        # personal: /.../<uid>/<subpath> to @personal/<subpath>
        path = path.relative_to(personal)
        parts = list(path.parts)
        parts[0] = "@personal"
        path = pathlib.Path(*parts)

    return path


def operands_as_paths(operands, cache, personal, shared, public):
    return {nm: str(get_relpath(op, cache, personal, shared, public)) for (nm, op) in operands.items()}


#
# Pub/Sub helpers
#


def start_client(url):
    client = fastapi_websocket_pubsub.PubSubClient()
    client.start_client(url)
    return client


async def disconnect_client(client, timeout=5):
    if client is not None:
        # If the broker is down client.disconnect hangs, wo we wrap it in a timeout
        await asyncio.wait_for(client.disconnect(), timeout)


#
# HTTP server helpers
#

HeaderType = typing.Annotated[str | None, fastapi.Header()]


def raise_bad_request(detail):
    raise fastapi.HTTPException(status_code=400, detail=detail)


def raise_unauthorized(detail="Unauthorized"):
    raise fastapi.HTTPException(status_code=401, detail=detail)


def raise_not_found(detail="Not Found"):
    raise fastapi.HTTPException(status_code=404, detail=detail)


def get_abspath(root, path, may_not_exist=False):
    abspath = root / path

    # Security check
    if root not in abspath.parents:
        raise_bad_request(f"Invalid path {path}")

    # Existence check
    if not abspath.is_file() and not may_not_exist:
        raise_not_found()

    return abspath


def check_dset_path(proot, path):
    try:
        exists = proot.exists_dset(path)
    except ValueError:
        raise_bad_request(f"Invalid path {path}")
    else:
        if not exists:
            raise_not_found()


#
# Blosc2 related helpers
#


def compress(data, dst=None):
    assert isinstance(data, (bytes, pathlib.Path))

    if dst is not None:
        dst.parent.mkdir(exist_ok=True, parents=True)
        if dst.exists():
            dst.unlink()

    # Create schunk
    cparams = {}
    dparams = {}
    storage = {
        "urlpath": dst,
        "cparams": cparams,
        "dparams": dparams,
    }
    schunk = blosc2.SChunk(**storage)

    # Append data
    if isinstance(data, pathlib.Path):
        with open(data, "rb") as f:
            data = f.read()

    schunk.append_data(data)

    return schunk


def iterchunk(chunk):
    # TODO Yield block by block
    yield chunk


#
# Facility to persist program state
#


class Database:
    def __init__(self, path, initial):
        self.path = path
        self.model = initial.__class__
        if path.exists():
            self.load()
        else:
            path.parent.mkdir(exist_ok=True, parents=True)
            self.data = initial
            self.save()

    def load(self):
        with self.path.open() as file:
            dump = json.load(file)
            self.data = self.model.model_validate(dump)

    def save(self):
        dump = self.data.model_dump_json(exclude_none=True)
        with safer.open(self.path, "w") as file:
            file.write(dump)

    def __getattr__(self, name):
        return getattr(self.data, name)
