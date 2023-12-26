###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib

# Requirements
import blosc2
import fastapi
import fastapi_websocket_pubsub
import httpx
import numpy as np

# Project
import models


#
# Blosc2 related functions
#

def init_b2nd(metadata, urlpath=None):
    if urlpath is not None:
        urlpath.parent.mkdir(exist_ok=True, parents=True)
        if urlpath.exists():
            urlpath.unlink()

    dtype = getattr(np, metadata.dtype)
    return blosc2.uninit(metadata.shape, dtype, urlpath=urlpath,
                         chunks=metadata.chunks, blocks=metadata.blocks)

def init_b2frame(metadata, urlpath=None):
    if urlpath is not None:
        urlpath.parent.mkdir(exist_ok=True, parents=True)
        if urlpath.exists():
            urlpath.unlink()

    cparams = metadata.cparams.model_dump()
    sc = blosc2.SChunk(
        metadata.chunksize,
        contiguous=metadata.contiguous,
        cparams=cparams,
        dparams={},
        urlpath=urlpath,
    )
    sc.fill_special(metadata.nbytes / metadata.typesize,
                    special_value=blosc2.SpecialValue.UNINIT)
    return sc


def open_b2(abspath):
    suffix = abspath.suffix
    if suffix == '.b2nd':
        array = blosc2.open(abspath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        array = None
        schunk = blosc2.open(abspath)
    elif suffix == '.b2':
        array = None
        schunk = blosc2.open(abspath)
    else:
        raise NotImplementedError()

    return array, schunk

def chunk_is_available(schunk, nchunk):
    flag = (schunk.get_chunk(nchunk)[31] & 0b01110000) >> 4
    return flag != blosc2.SpecialValue.UNINIT.value

def iterchunk(chunk):
    # TODO Yield block by block
    yield chunk

def get_model_from_obj(obj, model_class, **kwargs):
    if type(obj) is dict:
        getter = lambda o, k: o[k]
    else:
        getter = getattr

    data = kwargs.copy()
    for key, info in model_class.model_fields.items():
        if key not in data:
            value = getter(obj, key)
            if info.annotation is str:
                value = str(value)

            data[key] = value

    return model_class(**data)

def read_metadata(obj):
    # Open dataset
    if isinstance(obj, pathlib.Path):
        path = obj
        if not path.is_file():
            raise FileNotFoundError('File does not exist or is a directory')

        suffix = path.suffix
        if suffix in {'.b2frame', '.b2nd', '.b2'}:
            obj = blosc2.open(path)
        else:
            # Special case for regular files
            stat = path.stat()
            keys = ['mtime', 'size']
            data = {key: getattr(stat, f'st_{key}') for key in keys}
            return get_model_from_obj(data, models.File)

    # Read metadata
    if isinstance(obj, blosc2.ndarray.NDArray):
        array = obj
        cparams = get_model_from_obj(array.schunk.cparams, models.CParams)
        schunk = get_model_from_obj(array.schunk, models.SChunk, cparams=cparams)
        return get_model_from_obj(array, models.Metadata, schunk=schunk)
    elif isinstance(obj, blosc2.schunk.SChunk):
        schunk = obj
        cparams = get_model_from_obj(schunk.cparams, models.CParams)
        return get_model_from_obj(schunk, models.SChunk, cparams=cparams)
    else:
        raise TypeError(f'unexpected {type(obj)}')


def get_nchunks_from_slice(obj, slice_obj):
    # TODO Stub. This may be implemented in python-blosc2
    if isinstance(obj, blosc2.ndarray.NDArray):
        schunk = obj.schunk
    elif isinstance(obj, blosc2.schunk.SChunk):
        schunk = obj

    total = schunk.nchunks
    return range(total)


#
# Context managers
#

@contextlib.contextmanager
def log_exception(logger, message):
    try:
        yield
    except Exception:
        logger.exception(message)


#
# Filesystem helpers
#

def walk_files(root, exclude=None):
    if exclude is None:
        exclude = set()

    for path in root.glob('**/*'):
        if path.is_file():
            relpath = path.relative_to(root)
            if str(relpath) not in exclude:
                yield path, relpath

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
# Command line helpers
#
def socket_type(string):
    host, port = string.split(':')
    port = int(port)
    return (host, port)

def get_parser(broker=None, http=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--loglevel', default='warning')
    if broker:
        parser.add_argument('--broker', default=broker)
    if http:
        parser.add_argument('--http', default=http, type=socket_type)
    return parser

def run_parser(parser):
    args = parser.parse_args()

    # Logging
    loglevel = args.loglevel.upper()
    logging.basicConfig(level=loglevel)

    return args


#
# HTTP client helpers
#
def get(url, params=None, timeout=5, model=None):
    response = httpx.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    json = response.json()
    return json if model is None else model(**json)

def post(url, json=None):
    response = httpx.post(url, json=json)
    response.raise_for_status()
    return response.json()


#
# HTTP server helpers
#
def raise_bad_request(detail):
    raise fastapi.HTTPException(status_code=400, detail=detail)

def raise_not_found(detail='Not Found'):
    raise fastapi.HTTPException(status_code=404, detail=detail)

def get_abspath(root, path):
    abspath = root / path

    # Security check
    if root not in abspath.parents:
        raise_bad_request(f'Invalid path {path}')

    # Existence check
    if not abspath.is_file():
        raise_not_found()

    return abspath


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
        with self.path.open('w') as file:
            file.write(dump)

    def __getattr__(self, name):
        return getattr(self.data, name)
