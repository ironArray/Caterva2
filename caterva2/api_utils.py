###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os
import pathlib
import pickle

# Requirements
import httpx

# Optional requirements
try:
    import blosc2
    blosc2_is_here = True
except ImportError:
    blosc2_is_here = False


def split_dsname(dataset):
    ds = str(dataset)
    root_sep = ds.find('/')
    root, dsname = ds[:root_sep], ds[root_sep + 1:]
    return dsname, root


def slice_to_string(slice_):
    if slice_ is None or slice_ == () or slice_ == slice(None):
        return ''
    slice_parts = []
    if not isinstance(slice_, tuple):
        slice_ = (slice_,)
    for index in slice_:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            start = index.start or ''
            stop = index.stop or ''
            if index.step not in (1, None):
                raise IndexError('Only step=1 is supported')
            # step = index.step or ''
            slice_parts.append(f"{start}:{stop}")
    return ", ".join(slice_parts)


def parse_slice(string):
    if not string:
        return None
    obj = []
    for segment in string.split(','):
        if ':' not in segment:
            segment = int(segment)
        else:
            segment = [int(x) if x else None for x in segment.split(':')]
            segment = slice(*segment)
        obj.append(segment)

    return tuple(obj)


def fetch_data(path, host, params):
    if 'prefer_schunk' not in params:
        params['prefer_schunk'] = blosc2_is_here
    response = httpx.get(f'http://{host}/api/fetch/{path}', params=params)
    response.raise_for_status()
    data = response.content
    # Try different deserialization methods
    try:
        data = pickle.loads(data)
    except pickle.UnpicklingError:
        try:
            data = blosc2.decompress2(data)
        except (ValueError, RuntimeError):
            data = blosc2.ndarray_from_cframe(data)
            data = data[:] if data.ndim == 1 else data[()]
    return data


def get_download_url(path, host):
    response = httpx.get(f'http://{host}/api/download/{path}')
    response.raise_for_status()
    return response.json()


def b2_unpack(filepath):
    if not blosc2_is_here:
        return filepath
    schunk = blosc2.open(filepath)
    outfile = filepath.with_suffix('')
    with open(outfile, 'wb') as f:
        for i in range(schunk.nchunks):
            data = schunk.decompress_chunk(i)
            f.write(data)
    os.unlink(filepath)
    return outfile


def download_url(url, localpath, try_unpack=True):
    is_b2 = url.endswith('.b2')
    if is_b2:
        localpath += '.b2'
    with httpx.stream("GET", url) as r:
        r.raise_for_status()
        # Build the local filepath
        localpath = pathlib.Path(localpath)
        localpath.parent.mkdir(parents=True, exist_ok=True)
        with open(localpath, "wb") as f:
            for data in r.iter_bytes():
                f.write(data)
        if is_b2 and try_unpack:
            localpath = b2_unpack(localpath)
    return localpath


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
