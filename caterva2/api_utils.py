###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os
import pathlib
import re

# Requirements
import httpx

# Optional requirements
try:
    import blosc2
    blosc2_is_here = True
except ImportError:
    blosc2_is_here = False


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
            segment = slice(*map(lambda x: int(x.strip()) if x.strip() else None, segment.split(':')))
        obj.append(segment)

    return tuple(obj)


def get_auth_cookie(urlbase, user_auth):
    """
    Authenticate to a subscriber as a user and get an authorization cookie.

    Authentication fields will usually be ``username`` and ``password``.

    Parameters
    ----------
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    user_auth : dict
        A mapping of fields and values used as data to be posted for
        authenticating the user.

    Returns
    -------
    str
        An authentication token that may be used as a cookie in further
        requests to the subscriber.
    """
    if hasattr(user_auth, '_asdict'):  # named tuple (from tests)
        user_auth = user_auth._asdict()
    resp = httpx.post(f'{urlbase}auth/jwt/login', data=user_auth)
    resp.raise_for_status()
    auth_cookie = '='.join(list(resp.cookies.items())[0])
    return auth_cookie


def fetch_data(path, urlbase, params, auth_cookie=None):
    response = _xget(f'{urlbase}api/fetch/{path}', params=params,
                     auth_cookie=auth_cookie)
    data = response.content
    # Try different deserialization methods
    try:
        data = blosc2.ndarray_from_cframe(data)
        data = data[:] if data.ndim == 1 else data[()]
    except RuntimeError:
        data = blosc2.schunk_from_cframe(data)
        data = data[:]
    return data


def get_download_url(path, urlbase):
    return f'{urlbase}api/fetch/{path}'


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


# Not completely RFC6266-compliant, but probably good enough.
_attachment_b2fname_rx = re.compile(r';\s*filename\*?\s*=\s*"([^"]+\.b2)"')


def download_url(url, localpath, try_unpack=True, auth_cookie=None):
    headers = {'Cookie': auth_cookie} if auth_cookie else None
    with httpx.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        # Build the local filepath
        cdisp = r.headers.get('content-disposition', '')
        is_b2 = bool(_attachment_b2fname_rx.findall(cdisp))
        if is_b2:
            localpath += '.b2'
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
def _xget(url, params=None, headers=None, timeout=5, auth_cookie=None):
    if auth_cookie:
        headers = headers.copy() if headers else {}
        headers['Cookie'] = auth_cookie
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def get(url, params=None, headers=None, timeout=5, model=None,
        auth_cookie=None):
    response = _xget(url, params, headers, timeout, auth_cookie)
    json = response.json()
    return json if model is None else model(**json)


def post(url, json=None, auth_cookie=None):
    headers = {'Cookie': auth_cookie} if auth_cookie else None
    response = httpx.post(url, json=json, headers=headers)
    response.raise_for_status()
    return response.json()
