###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import json
import pathlib
import safer

# Requirements
import blosc2
import fastapi
import fastapi_websocket_pubsub
import httpx
import tqdm

# Project
from caterva2 import models
from caterva2 import api_utils, b2_utils


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
        model = get_model_from_obj(schunk, models.SChunk, cparams=cparams)
        return model
    else:
        raise TypeError(f'unexpected {type(obj)}')


def download(host, dataset, params, localpath=None, verbose=False):
    data = api_utils.get(f'http://{host}/api/info/{dataset}')

    # Create array/schunk in memory
    suffix = dataset.suffix
    if suffix == '.b2nd':
        metadata = models.Metadata(**data)
        array = b2_utils.init_b2nd(metadata, urlpath=localpath)
        schunk = array.schunk
    elif suffix == '.b2frame':
        metadata = models.SChunk(**data)
        schunk = b2_utils.init_b2frame(metadata, urlpath=localpath)
        array = None
    else:
        metadata = models.SChunk(**data)
        schunk = b2_utils.init_b2frame(metadata, urlpath=None)
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

    if 'slice' in params:
        slice_ = api_utils.parse_slice(params['slice'])
        if array:
            if localpath is not None:
                # We want to save the slice to a file
                ndarray = array.slice(slice_)  # in memory (compressed)
                # Remove previous new on-disk array and create a new one
                ndarray.copy(urlpath=localpath, mode="w", contiguous=True, cparams=schunk.cparams)
            else:
                array = array[slice_] if array.ndim > 0 else array[()]
        else:
            assert len(slice_) == 1
            slice_ = slice_[0]
            if localpath is not None:
                data = schunk[slice_]
                # TODO: fix the upstream bug in python-blosc2 that prevents this from working
                #  when not specifying chunksize (uses `data.size` instead of `len(data)`).
                blosc2.SChunk(data=data, mode="w", urlpath=localpath,
                              chunksize=schunk.chunksize,
                              cparams=schunk.cparams)
            else:
                if isinstance(slice_, int):
                    slice_ = slice(slice_, slice_ + 1)
                # TODO: make SChunk support integer as slice
                schunk = schunk[slice_]

    return array, schunk


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
