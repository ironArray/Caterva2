###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import argparse
import logging

# Requirements
import blosc2
import httpx
import tqdm

# Project
from caterva2 import api_utils, b2_utils, models


#
# Download helper
#

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
