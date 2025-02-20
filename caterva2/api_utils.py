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
import sys

# Requirements
import httpx
import requests

# Caterva2
from caterva2 import utils

# Optional requirements
try:
    import blosc2

    blosc2_is_here = True
except ImportError:
    blosc2_is_here = False


USE_REQUESTS = os.environ.get("USE_REQUESTS", False)
if sys.platform == "emscripten":
    USE_REQUESTS = True


def slice_to_string(slice_):
    if slice_ is None or slice_ == () or slice_ == slice(None):
        return ""
    slice_parts = []
    if not isinstance(slice_, tuple):
        slice_ = (slice_,)
    for index in slice_:
        if isinstance(index, int):
            slice_parts.append(str(index))
        elif isinstance(index, slice):
            start = index.start or ""
            stop = index.stop or ""
            if index.step not in (1, None):
                raise IndexError("Only step=1 is supported")
            # step = index.step or ''
            slice_parts.append(f"{start}:{stop}")
    return ", ".join(slice_parts)


def parse_slice(string):
    if not string:
        return None
    obj = []
    for segment in string.split(","):
        if ":" not in segment:
            segment = int(segment)
        else:
            segment = slice(*(int(x.strip()) if x.strip() else None for x in segment.split(":")))
        obj.append(segment)

    return tuple(obj) if len(obj) > 1 else obj[0]


def get_auth_cookie(urlbase, user_auth):
    """
    Authenticate to a subscriber as a user and get an authorization cookie.

    Authentication fields will usually be ``username`` and ``password``.

    Parameters
    ----------
    urlbase : str
        The base of URLs of the subscriber to query.
    user_auth : dict
        A mapping of fields and values used as data to be posted for
        authenticating the user.

    Returns
    -------
    str
        An authentication token that may be used as a cookie in further
        requests to the subscriber.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> cat2.upload('root-example/ds-sc-attr.b2nd', '@personal/attr.b2nd', urlbase, auth_cookie)
    PosixPath('@personal/attr.b2nd')
    """
    url = f"{urlbase}/auth/jwt/login"
    client, url = get_client_and_url(None, url)

    if hasattr(user_auth, "_asdict"):  # named tuple (from tests)
        user_auth = user_auth._asdict()
    resp = client.post(url, data=user_auth)
    resp.raise_for_status()
    return "=".join(list(resp.cookies.items())[0])


def fetch_data(path, urlbase, params, auth_cookie=None):
    response = _xget(f"{urlbase}/api/fetch/{path}", params=params, auth_cookie=auth_cookie)
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
    return f"{urlbase}/api/fetch/{path}"


def b2_unpack(filepath):
    if not blosc2_is_here:
        return filepath
    schunk = blosc2.open(filepath)
    outfile = filepath.with_suffix("")
    with open(outfile, "wb") as f:
        for i in range(schunk.nchunks):
            data = schunk.decompress_chunk(i)
            f.write(data)
    os.unlink(filepath)
    return outfile


# Not completely RFC6266-compliant, but probably good enough.
_attachment_b2fname_rx = re.compile(r';\s*filename\*?\s*=\s*"([^"]+\.b2)"')


def download_url(url, localpath, try_unpack=True, auth_cookie=None):
    client, url = get_client_and_url(None, url)

    localpath = pathlib.Path(localpath)
    localpath.parent.mkdir(parents=True, exist_ok=True)
    if localpath.is_dir():
        # Get the filename from the URL
        localpath /= url.split("/")[-1]

    headers = {"Cookie": auth_cookie} if auth_cookie else None
    if USE_REQUESTS:
        with client.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            # Build the local filepath
            cdisp = r.headers.get("content-disposition", "")
            is_b2 = bool(_attachment_b2fname_rx.findall(cdisp))
            if is_b2:
                # Append '.b2' to the filename
                localpath = localpath.with_suffix(localpath.suffix + ".b2")
            with open(localpath, "wb") as f:
                for data in r.iter_content():
                    f.write(data)

    else:
        with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            # Build the local filepath
            cdisp = r.headers.get("content-disposition", "")
            is_b2 = bool(_attachment_b2fname_rx.findall(cdisp))
            if is_b2:
                # Append '.b2' to the filename
                localpath = localpath.with_suffix(localpath.suffix + ".b2")
            with open(localpath, "wb") as f:
                for data in r.iter_bytes():
                    f.write(data)

    if is_b2 and try_unpack:
        localpath = b2_unpack(localpath)

    return localpath


def upload_file(localpath, remotepath, urlbase, try_pack=False, auth_cookie=None):
    client, url = get_client_and_url(None, f"{urlbase}/api/upload/{remotepath}")

    headers = {"Cookie": auth_cookie} if auth_cookie else None
    with open(localpath, "rb") as f:
        response = client.post(url, files={"file": f}, headers=headers)
        response.raise_for_status()
    return pathlib.Path(response.json())


#
# HTTP client helpers
#


def get_client_and_url(server, url, return_async_client=False):
    # Use requests instead of httpx (for pyodide)
    if USE_REQUESTS and not return_async_client and server is None:
        client = requests.Session()
        return client, url

    # When NOT called from pyodide
    if return_async_client:
        client_class = httpx.AsyncClient
        transport_class = httpx.AsyncHTTPTransport
    else:
        client_class = httpx.Client
        transport_class = httpx.HTTPTransport

    transport = None
    if server is not None:
        if type(server) is str:
            server = utils.Socket(server)

        assert url.startswith("/"), f'expected absolute path, got "{url}"'
        if server.uds:
            transport = transport_class(uds=server.uds)
            url = f"http://localhost{url}"
        else:
            url = f"http://{server}{url}"

    client = client_class(transport=transport)
    return client, url


def _xget(url, params=None, headers=None, timeout=5, auth_cookie=None, server=None, raise_for_status=True):
    client, url = get_client_and_url(server, url)
    if auth_cookie:
        headers = headers.copy() if headers else {}
        headers["Cookie"] = auth_cookie
    response = client.get(url, params=params, headers=headers, timeout=timeout)
    if raise_for_status:
        response.raise_for_status()
    return response


def get(
    url,
    params=None,
    headers=None,
    timeout=5,
    model=None,
    auth_cookie=None,
    server=None,
    raise_for_status=True,
    return_response=False,
):
    response = _xget(
        url, params, headers, timeout, auth_cookie, server=server, raise_for_status=raise_for_status
    )
    if return_response:
        return response

    json = response.json()
    return json if model is None else model(**json)


def post(url, json=None, auth_cookie=None, server=None):
    client, url = get_client_and_url(server, url)
    headers = {"Cookie": auth_cookie} if auth_cookie else None
    response = client.post(url, json=json, headers=headers)
    response.raise_for_status()
    return response.json()
