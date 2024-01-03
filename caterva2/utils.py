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
import safer
import tqdm

# Project
from . import models


#
# Blosc2 related functions
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
        'urlpath': dst,
        'cparams': cparams,
        'dparams': dparams,
    }
    schunk = blosc2.SChunk(**storage)

    # Append data
    if isinstance(data, pathlib.Path):
        with open(data, 'rb') as f:
            data = f.read()

    schunk.append_data(data)

    return schunk

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
    # Blosc2 flags are at offset 31
    # (see https://github.com/Blosc/c-blosc2/blob/main/README_CHUNK_FORMAT.rst)
    flag = (schunk.get_lazychunk(nchunk)[31] & 0b01110000) >> 4
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


def parse_slice(string):
    obj = []
    for segment in string.split(','):
        if ':' not in segment:
            segment = int(segment)
        else:
            segment = [int(x) if x else None for x in segment.split(':')]
            segment = slice(*segment)
        obj.append(segment)

    return tuple(obj)

def download(host, dataset, params, urlpath=None, verbose=False):
    # TODO: Should we allow downloading a slice to a file (and fill the rest as uninit)?
    #  Let's do so for now.
    # if urlpath is not None and 'slice' in params:
    #     raise ValueError('Cannot download a slice to a file')
    data = get(f'http://{host}/api/info/{dataset}')

    # Create array/schunk in memory
    suffix = dataset.suffix
    if suffix == '.b2nd':
        metadata = models.Metadata(**data)
        array = init_b2nd(metadata, urlpath=urlpath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        metadata = models.SChunk(**data)
        schunk = init_b2frame(metadata, urlpath=urlpath)
        array = None
    else:
        metadata = models.SChunk(**data)
        schunk = init_b2frame(metadata, urlpath=None)
        array = None

    # Download and update schunk
    url = f'http://{host}/api/download/{dataset}'
    iter_chunks = range(schunk.nchunks)
    if verbose:
        iter_chunks = tqdm.tqdm(iter_chunks, desc='Downloading', unit='chunk')
    for nchunk in iter_chunks:
        params['nchunk'] = nchunk
        response = httpx.get(url, params=params, timeout=None)
        response.raise_for_status()
        chunk = response.read()
        schunk.update_chunk(nchunk, chunk)

    # TODO: streaming
#       with httpx.stream('GET', url, params=params) as resp:
#           buffer = []
#           for chunk in resp.iter_bytes():
#               print('LEN', len(buffer))
#               buffer.append(chunk)
#           chunk = b''.join(buffer)
#           schunk.update_chunk(nchunk, chunk)

    if 'slice' in params:
        slice_ = parse_slice(params['slice'])
        if array:
            if urlpath is not None:
                # We want to save the slice to a file
                ndarray = array.slice(slice_)  # in memory (compressed)
                # Remove previous new on-disk array and create a new one
                ndarray.copy(urlpath=urlpath, mode="w", contiguous=True, cparams=schunk.cparams)
            else:
                array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if urlpath is not None:
                data = schunk[slice_]
                # TODO: fix the upstream bug in python-blosc2 that prevents this from working
                #  when not specifying chunksize (uses `data.size` instead of `len(data)`).
                blosc2.SChunk(data=data, mode="w", urlpath=urlpath,
                              chunksize=schunk.chunksize,
                              cparams=schunk.cparams)
            else:
                schunk = schunk[slice_]

    return array, schunk


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
def get(url, params=None, headers=None, timeout=5, model=None):
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
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
        with safer.open(self.path, 'w') as file:
            file.write(dump)

    def __getattr__(self, name):
        return getattr(self.data, name)
