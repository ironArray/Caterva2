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
import blosc2
import httpx


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


def get_auth_cookie(urlbase, user_auth, timeout=5):
    """
    Authenticate to a server as a user and get an authorization cookie.

    Authentication fields will usually be ``username`` and ``password``.

    Parameters
    ----------
    urlbase : str
        The base of URLs of the server to query.
    user_auth : dict
        A mapping of fields and values used as data to be posted for
        authenticating the user.

    Returns
    -------
    str
        An authentication token that may be used as a cookie in further
        requests to the server.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> cat2.upload('root-example/ds-sc-attr.b2nd', '@personal/attr.b2nd', urlbase, auth_cookie)
    PosixPath('@personal/attr.b2nd')
    """
    client = get_client()
    url = f"{urlbase}/auth/jwt/login"

    if hasattr(user_auth, "_asdict"):  # named tuple (from tests)
        user_auth = user_auth._asdict()
    try:
        resp = client.post(url, data=user_auth, timeout=timeout)
    except httpx.ReadTimeout as e:
        raise TimeoutError(
            f"Timeout after {timeout} seconds while trying to access {url}. "
            f"Try increasing the timeout (currently {timeout} s) for Client instance for large datasets."
        ) from e
    resp.raise_for_status()
    return "=".join(list(resp.cookies.items())[0])


def fetch_data(path, urlbase, params, auth_cookie=None, as_blosc2=False, timeout=5):
    response = _xget(f"{urlbase}/api/fetch/{path}", params=params, auth_cookie=auth_cookie, timeout=timeout)
    data = response.content
    # Try different deserialization methods
    try:
        data = blosc2.ndarray_from_cframe(data)
    except RuntimeError:
        data = blosc2.schunk_from_cframe(data)
    if as_blosc2:
        return data
    if hasattr(data, "ndim"):  # if b2nd or b2frame
        # catch 0d case where [:] fails
        return data[()] if data.ndim == 0 else data[:]
    else:
        return data[:]


def get_download_url(path, urlbase):
    return f"{urlbase}/api/download/{path}"


def get_handle_url(path, urlbase):
    # Get the root in path (first element in path)
    # root = path.split("/")[0]
    # return f"{urlbase}/roots/{path}?roots={root}"
    # We don't want to show other datasets in the same root
    return f"{urlbase}/roots/{path}"


def b2_unpack(filepath):
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


def download_url(url, localpath, auth_cookie=None):
    client = get_client()

    localpath = pathlib.Path(localpath)
    localpath.parent.mkdir(parents=True, exist_ok=True)
    if localpath.is_dir():
        # Get the filename from the URL
        localpath /= url.split("/")[-1]

    headers = {}
    headers["Accept-Encoding"] = "blosc2"
    if auth_cookie:
        headers["Cookie"] = auth_cookie

    with client.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        decompress = r.headers.get("Content-Encoding") == "blosc2"
        if decompress:
            localpath = localpath.with_suffix(localpath.suffix + ".b2")

        with open(localpath, "wb") as f:
            for data in r.iter_bytes():
                f.write(data)

    if decompress:
        localpath = b2_unpack(localpath)

    return localpath


def upload_file(localpath, remotepath, urlbase, auth_cookie=None):
    client = get_client()
    url = f"{urlbase}/api/upload/{remotepath}"

    headers = {"Cookie": auth_cookie} if auth_cookie else None
    with open(localpath, "rb") as f:
        response = client.post(url, files={"file": f}, headers=headers)
        response.raise_for_status()
    return pathlib.PurePosixPath(response.json())


def download_from_url(localpath, remotepath, urlbase, auth_cookie=None):
    client = get_client()
    url = f"{urlbase}/api/download_from_url/{remotepath}"

    headers = {"Cookie": auth_cookie} if auth_cookie else None
    response = client.post(url, data={"file": localpath}, headers=headers)
    response.raise_for_status()
    return pathlib.PurePosixPath(response.json())


def unfold_file(remotepath, urlbase, auth_cookie=None):
    client = get_client()
    url = f"{urlbase}/api/unfold/{remotepath}"

    headers = {"Cookie": auth_cookie} if auth_cookie else None
    response = client.post(url, headers=headers)
    response.raise_for_status()
    return pathlib.PurePosixPath(response.json())


#
# HTTP client helpers
#


def get_client(return_async_client=False):
    if return_async_client:
        client_class = httpx.AsyncClient
    else:
        client_class = httpx.Client

    return client_class()


def _xget(url, params=None, headers=None, timeout=5, auth_cookie=None):
    client = get_client()
    if auth_cookie:
        headers = headers.copy() if headers else {}
        headers["Cookie"] = auth_cookie
    try:
        response = client.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.ReadTimeout as e:
        raise TimeoutError(
            f"Timeout after {timeout} seconds while trying to access {url}. "
            f"Try increasing the timeout (currently {timeout} s) for Client instance for large datasets."
        ) from e

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Only customize for 400 errors
        if exc.response.status_code == 400:
            detail = None
            try:
                body = exc.response.json()
                detail = body.get("detail")
            except (ValueError, AttributeError, TypeError):
                # Fallback to raw text if JSON decoding fails
                detail = exc.response.text.strip() or None

            if detail:
                # Build a new message that replaces the MDN link with the detail
                message = f"{exc.request.method} request to {exc.response.url} failed: {detail}"
                raise httpx.HTTPStatusError(
                    message=message, request=exc.request, response=exc.response
                ) from exc
        # Re-raise original for non-400 errors
        raise

    return response


def get(
    url,
    params=None,
    headers=None,
    timeout=5,
    model=None,
    auth_cookie=None,
    return_response=False,
):
    response = _xget(url, params, headers, timeout, auth_cookie)
    if return_response:
        return response

    json = response.json()
    return json if model is None else model(**json)


def post(url, json=None, auth_cookie=None, timeout=5):
    client = get_client()
    headers = {"Cookie": auth_cookie} if auth_cookie else None
    try:
        response = client.post(url, json=json, headers=headers, timeout=timeout)
    except httpx.ReadTimeout as e:
        raise TimeoutError(
            f"Timeout after {timeout} seconds while trying to access {url}. "
            f"Try increasing the timeout (currently {timeout} s) for Client instance for large datasets."
        ) from e
    response.raise_for_status()
    return response.json()
