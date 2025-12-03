###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import ast
import functools
import inspect
import io
import pathlib
import sys
import textwrap
from collections.abc import Sequence
from pathlib import PurePosixPath

import blosc2
import httpx
import numpy as np
from blosc2 import NDArray, SChunk

from . import api_utils, utils


def _format_paths(urlbase, path=None):
    if urlbase is None and sys.platform == "emscripten":
        urlbase = ""

    if isinstance(urlbase, pathlib.Path):
        urlbase = urlbase.as_posix()
    if urlbase.endswith("/"):
        urlbase = urlbase[:-1]
        urlbase = pathlib.PurePosixPath(urlbase)

    if path is not None:
        # Ensure path is a string before checking startswith
        p = path.as_posix() if hasattr(path, "as_posix") else str(path)
        if p.startswith("/"):
            raise ValueError("path cannot start with a slash")

    return urlbase, path


def _looks_like_slice(s: str) -> bool:
    """Return True if `s` parses as an index/slice expression for `np.index_exp[...]`."""
    if not isinstance(s, str) or not s.strip():
        return False
    try:
        # restrict eval environment to only numpy (no builtins)
        eval(f"np.index_exp[{s}]", {"np": np, "__builtins__": {}}, {})
        return True
    except Exception:
        return False


class Root:
    def __init__(self, client, name):
        """
        Represents a remote root directory.

        Parameters
        ----------
        client : Client
            The client used to interact with the remote repository.
        name : str
            Name of the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client("https://cat2.cloud/demo")
        >>> root = client.get("example")
        >>> root.file_list[-3:]
        ['ds-sc-attr.b2nd', 'lung-jpeg2000_10x.b2nd', 'tomo-guess-test.b2nd']
        """
        self.client = client
        urlbase, name = _format_paths(client.urlbase, name)
        self.name = name

    @property
    def urlbase(self):
        return self.client.urlbase

    @property
    def cookie(self):
        return self.client.cookie

    @property
    def file_list(self):
        """
        Retrieves a list of files in this root.
        """
        return self.client._get(
            f"{self.urlbase}/api/list/{self.name}", auth_cookie=self.cookie, timeout=self.client.timeout
        )

    def __repr__(self):
        return f"<Root: {self.name}>"

    def __getitem__(self, path):
        """
        Retrieves a file or dataset from the root.

        Parameters
        ----------
        path : str or Path
            Path of the file or dataset to retrieve.

        Returns
        -------
        File, Dataset
            An instance of :class:`File` or :class:`Dataset`.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> root['ds-1d.b2nd']
        <Dataset: example/ds-1d.b2nd>
        """
        path = path.as_posix() if hasattr(path, "as_posix") else str(path)
        if path.endswith((".b2nd", ".b2frame")):
            return Dataset(self, path)
        else:
            return File(self, path)

    def __contains__(self, path):
        """
        Checks if a path exists in the root.

        Parameters
        ----------
        path : str or Path
            The path of the file or dataset to check.

        Returns
        -------
        bool
            True if the path exists in the root, otherwise False.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> 'ds-1d.b2nd' in root
        True
        """
        path = path.as_posix() if hasattr(path, "as_posix") else str(path)
        return path in self.file_list

    def __iter__(self):
        """
        Iterates over the files and datasets in the root.

        Returns
        -------
        iter
            An iterator for the files and datasets in the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> for file in root:
        ...     print(file)
        README.md
        dir1/ds-2d.b2nd
        dir1/ds-3d.b2nd
        dir2/ds-4d.b2nd
        ds-1d-b.b2nd
        ds-1d-fields.b2nd
        ds-1d.b2nd
        ds-2d-fields.b2nd
        ds-hello.b2frame
        ds-sc-attr.b2nd
        lung-jpeg2000_10x.b2nd
        tomo-guess-test.b2nd
        """
        return iter(self.file_list)

    def __len__(self):
        """
        Returns the number of files in the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> len(root)
        12
        """
        return len(self.file_list)

    def __str__(self):
        return self.name

    def upload(self, local_dset, remotepath=None, compute=None):
        """
        Uploads a local file to this root.

        Parameters
        ----------
        local_dset : Path | in-memory object
            Path to the local dataset or an in-memory object (convertible to blosc2.SChunk).
        remotepath : Path, optional
            Remote path where the file will be uploaded.  If not provided, the
            file will be uploaded to the top level of this root.
        compute: None | bool
            For LazyArray objects, whether to compute the result eagerly or not.

        Returns
        -------
        File
            A instance of :class:`File` or :class:`Dataset`.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To upload a file you must be registered as a user.
        >>> client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        >>> root = client.get('@personal')
        >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        True
        """
        if remotepath is None:
            # localpath cannot be absolute in this case (too much prone to errors)
            if (
                not isinstance(local_dset, (str, pathlib.Path))
                or pathlib.PurePosixPath(local_dset).is_absolute()
            ):
                raise ValueError("When `remotepath` is not specified, `localpath` must be a relative path")
            remotepath = pathlib.PurePosixPath(self.name) / local_dset
        else:
            remotepath = pathlib.PurePosixPath(self.name) / pathlib.PurePosixPath(remotepath)
        return self.client.upload(local_dset, remotepath, compute)

    def load_from_url(self, urlpath, remotepath=None):
        """
        Loads a third party file via url to this root.

        Parameters
        ----------
        urlpath : str
            Url path of the file to get.
        remotepath : Path, optional
            Remote path where the file will be placed.  If not provided, the
            file will be placed in the top level of this root.

        Returns
        -------
        File
            A instance of :class:`File` or :class:`Dataset`.

        """
        if remotepath is None:
            remotepath = pathlib.PurePosixPath(self.name) / urlpath
        else:
            remotepath = pathlib.PurePosixPath(self.name) / pathlib.PurePosixPath(remotepath)
        return self.client.load_from_url(urlpath, remotepath)


class File:
    def __init__(self, root, path):
        """
        Represents a file, which can be a Blosc2 dataset or a regular file on a root repository.

        This class is not intended for direct instantiation; it should be accessed through a
        :class:`Root` instance.

        Parameters
        ----------
        root : Root
            The root repository.
        path : str
            The path of the file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> file = root['README.md']
        >>> file
        <File: example/README.md>
        >>> file.name
        'README.md'
        >>> file.urlbase
        'https://cat2.cloud/demo'
        >>> file.path
        PurePosixPath('example/README.md')
        >>> file.meta['contiguous']
        True
        """
        self.root = root
        _, name = _format_paths(root.urlbase, path)
        _, root = _format_paths(root.urlbase, root.name)
        self.name = name
        self.path = pathlib.PurePosixPath(f"{self.root}/{self.name}")
        self.meta = self.root.client._get(
            f"{self.urlbase}/api/info/{self.path}", auth_cookie=self.cookie, timeout=self.root.client.timeout
        )
        # TODO: 'cparams' is not always present (e.g. for .b2nd files)
        # print(f"self.meta: {self.meta['cparams']}")

    @property
    def client(self):
        return self.root.client

    @property
    def urlbase(self):
        return self.client.urlbase

    @property
    def cookie(self):
        return self.client.cookie

    def __repr__(self):
        return f"<File: {self.path}>"

    @functools.cached_property
    def vlmeta(self):
        """
        Returns a mapping of metalayer names to their respective values.

        This is used to access variable-length metalayers (user attributes)
        associated with the file.

        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> file = root['ds-sc-attr.b2nd']
        >>> file.vlmeta
        {'a': 1, 'b': 'foo', 'c': 123.456}
        """
        schunk_meta = self.meta.get("schunk", self.meta)
        return schunk_meta.get("vlmeta", {})

    def get_download_url(self):
        """
        Retrieves the download URL for the file.

        Returns
        -------
        str
            The file's download URL.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'https://cat2.cloud/demo/api/fetch/example/ds-1d.b2nd'
        """
        return api_utils.get_download_url(self.path, self.urlbase)

    def __getitem__(self, item):
        """
        Retrieves a slice of the dataset.

        Parameters
        ----------
        item : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> ds = root['ds-1d.b2nd']
        >>> ds[1]
        array(1)
        >>> ds[:1]
        array([0])
        >>> ds[0:10]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        if isinstance(item, str):  # used a filter or field to index so want blosc2 array as result
            try:
                dtype = np.dtype(self.dtype)
            except (ValueError, TypeError):
                dtype = np.dtype(ast.literal_eval(self.dtype))
            fields = dtype.fields
            if fields is None:
                raise ValueError("The array is not structured (its dtype does not have fields)")
            if item in fields:
                # A shortcut to access fields
                return self.client.get_slice(self.path, as_blosc2=True, field=item)  # arg key is None
            else:  # used a filter (possibly lazyexpr)
                return self.client.get_slice(self.path, item, as_blosc2=True)
        else:
            return self.slice(item, as_blosc2=False)

    def slice(
        self, key: int | slice | Sequence[slice], as_blosc2: bool = True
    ) -> NDArray | SChunk | np.ndarray:
        """Get a slice of a File/Dataset.

        Parameters
        ----------
        key : int, slice, or sequence of slices
            The slice to retrieve.  If a single slice is provided, it will be
            applied to the first dimension.  If a sequence of slices is
            provided, each slice will be applied to the corresponding
            dimension.
        as_blosc2 : bool
            If True (default), the result will be returned as a Blosc2 object
            (either a `SChunk` or `NDArray`).  If False, it will be returned
            as a NumPy array (equivalent to `self[key]`).

        Returns
        -------
        NDArray or SChunk or numpy.ndarray
            A new Blosc2 object containing the requested slice.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> ds = root['ds-1d.b2nd']
        >>> ds.slice(1)
        <blosc2.ndarray.NDArray object at 0x10747efd0>
        >>> ds.slice(1)[()]
        array(1)
        >>> ds.slice(slice(0, 10))[:]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        # Fetch and return the data as a Blosc2 object / NumPy array
        return self.client.get_slice(self.path, key, as_blosc2)

    def download(self, localpath=None):
        """
        Downloads the file to storage.

        Parameters
        ----------
        localpath : Path, optional
            The destination path for the downloaded file.  If not specified, the file will
            be downloaded to the current working directory.

        Returns
        -------
        Path
            The path to the downloaded file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> file = root['ds-1d.b2nd']
        >>> file.download()
        PosixPath('example/ds-1d.b2nd')
        >>> file.download('mydir/myarray.b2nd')
        PosixPath('mydir/myarray.b2nd')
        """
        return self.client.download(
            self.path,
            localpath=localpath,
        )

    def unfold(self):
        """
        Unfolds the file in a remote directory.

        Returns
        -------
        Path
            The path to the unfolded directory.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> file = root['ds-1d.h5']
        >>> file.unfold()
        PurePosixPath('example/ds-1d.h5')
        """
        return self.client.unfold(self.path)

    def move(self, dst):
        """
        Moves the file to a new location.

        Parameters
        ----------
        dst : Path
            The destination path for the file.

        Returns
        -------
        Path
            The new path of the file after the move.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> # For moving a file you need to be a registered user
        >>> client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        >>> root = client.get('@personal')
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> file = root['root-example/dir2/ds-4d.b2nd']
        >>> file.move('@personal/root-example/dir1/ds-4d-moved.b2nd')
        PurePosixPath('@personal/root-example/dir1/ds-4d-moved.b2nd')
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        False
        >>> 'root-example/dir1/ds-4d-moved.b2nd' in root
        True
        """
        return self.client.move(self.path, dst)

    def copy(self, dst):
        """
        Copies the file to a new location.

        Parameters
        ----------
        dst : Path
            The destination path for the file.

        Returns
        -------
        Path
            The new path of the copied file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # For copying a file you need to be a registered user
        >>> client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        >>> root = client.get('@personal')
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> file = root['root-example/dir2/ds-4d.b2nd']
        >>> file.copy('@personal/root-example/dir2/ds-4d-copy.b2nd')
        PurePosixPath('@personal/root-example/dir2/ds-4d-copy.b2nd')
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        True
        >>> 'root-example/dir2/ds-4d-copy.b2nd' in root
        True
        """
        return self.client.copy(self.path, dst)

    def remove(self):
        """
        Removes the file from the remote repository.

        Returns
        -------
        str
            The path of the removed file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To remove a file you need to be a registered user
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> root = client.get('@personal')
        >>> path = 'root-example/dir2/ds-4d.b2nd'
        >>> root.upload(path)
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> file = root[path]
        >>> file.remove()
        '@personal/root-example/dir2/ds-4d.b2nd'
        >>> path in root
        False
        """
        return self.client.remove(self.path)


class Dataset(File, blosc2.Operand):
    def __init__(self, root, path):
        """
        Represents a dataset within a Blosc2 container.

        This class is not intended to be instantiated directly; it should be accessed through a
        :class:`Root` instance.

        Parameters
        ----------
        root : Root
            The root repository.
        path : str
            The path of the dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> ds = root['ds-1d.b2nd']
        >>> ds.dtype
        'int64'
        >>> ds.shape
        (1000,)
        >>> ds.chunks
        (100,)
        >>> ds.blocks
        (10,)
        """
        super().__init__(root, path)

    def __str__(self):
        return self.path.as_posix()

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f"<Dataset: {self.path}>"

    @property
    def dtype(self):
        """
        The data type of the dataset.
        """
        try:
            return self.meta["dtype"]
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'dtype'.") from e

    @property
    def shape(self):
        """
        The shape of the dataset.
        """
        try:
            return tuple(self.meta["shape"])
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'shape'.") from e

    @property
    def chunks(self):
        """
        The chunkshape of the compressed dataset.
        """
        try:
            return tuple(self.meta["chunks"])
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'chunks'.") from e

    @property
    def blocks(self):
        """
        The blockshape of the compressed dataset.
        """
        try:
            return tuple(self.meta["blocks"])
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'blocks'.") from e

    def append(self, data):
        """
        Appends data to the dataset.

        Parameters
        ----------
        data : blosc2.NDArray, numpy.ndarray, sequence
            The data to append to the dataset.

        Returns
        -------
        out: Caterva2.Dataset
            A pointer to the (modified) dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To append data to a dataset you need to be a registered user
        >>> client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        >>> data = client.copy('@public/examples/ds-1d.b2nd', '@personal/ds-1d.b2nd')
        >>> dataset = client.get('@personal')['ds-1d.b2nd']
        >>> dataset.append([1, 2, 3])
        (1003,)
        """
        return self.client.append(self.path, data)


class BasicAuth:
    """
    Basic authentication for HTTP requests.

    Parameters
    ----------
    username : str, optional
        The username for authentication.
    password : str, optional
        The password for authentication.
    """

    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password


class Client:
    def __init__(self, urlbase, auth=None, timeout=5):
        """
        Creates a client for server in urlbase.

        Parameters
        ----------
        urlbase : str
            Base URL of the server to query.
        auth : tuple, BasicAuth, optional

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client("https://cat2.cloud/demo")
        >>> auth_client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        """
        http2 = sys.platform != "emscripten"
        self.httpx_client = httpx.Client(http2=http2)

        self.urlbase = utils.urlbase_type(urlbase)
        self.cookie = None
        self.timeout = timeout
        if auth is not None:
            if isinstance(auth, BasicAuth):
                username = auth.username
                password = auth.password
            elif isinstance(auth, tuple):
                username = auth[0]
                password = auth[1]
            else:
                raise ValueError("auth must be BasicAuth or a tuple (username, password)")
            if username and password:
                self.cookie = self._get_auth_cookie(
                    {"username": username, "password": password}, timeout=self.timeout
                )

    def close(self):
        """
        Close httpx.Client instance associated with Caterva2 Client.

        Parameters
        ----------

        Returns
        -------
            None
        """
        self.httpx_client.close()

    def __enter__(self):
        """Enter context manager - HTTP clients created lazily on first use."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - close HTTP clients."""
        self.close()
        return False

    def _fetch_data(self, path, urlbase, params, auth_cookie=None, as_blosc2=False, timeout=5):
        response = self._xget(
            f"{urlbase}/api/fetch/{path}", params=params, auth_cookie=auth_cookie, timeout=timeout
        )
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

    def _get_auth_cookie(self, user_auth, timeout=5):
        """
        Authenticate to a server as a user and get an authorization cookie.

        Authentication fields will usually be ``username`` and ``password``.

        Parameters
        ----------
        user_auth : dict
            A mapping of fields and values used as data to be posted for
            authenticating the user.

        Returns
        -------
        str or None
            An authentication token that may be used as a cookie in further
            requests to the server. Returns None in browser environments
            (Pyodide) where cookies are managed automatically by the browser.
        """
        client = self.httpx_client
        url = f"{self.urlbase}/auth/jwt/login"

        if hasattr(user_auth, "_asdict"):
            user_auth = user_auth._asdict()
        try:
            resp = client.post(url, data=user_auth, timeout=timeout)
        except httpx.ReadTimeout as e:
            raise TimeoutError(
                f"Timeout after {timeout} seconds while trying to access {url}. "
                f"Try increasing the timeout (currently {timeout} s) for Client instance for large datasets."
            ) from e
        resp.raise_for_status()

        # Try cookies first
        cookies = list(resp.cookies.items())
        if cookies:
            return "=".join(cookies[0])

        # Check Set-Cookie header
        set_cookie = resp.headers.get("set-cookie")
        if set_cookie:
            return set_cookie.split(";")[0]

        # For 204 in Pyodide, cookies are set automatically by browser
        # Return None instead of empty string to indicate browser-managed auth
        if resp.status_code == 204 and sys.platform == "emscripten":
            return None

        raise RuntimeError(
            f"Authentication failed: no authentication token received. "
            f"Status: {resp.status_code}, Headers: {dict(resp.headers)}"
        )

    def _get(
        self,
        url,
        params=None,
        headers=None,
        timeout=5,
        model=None,
        auth_cookie=None,
        return_response=False,
    ):
        response = self._xget(url, params, headers, timeout, auth_cookie)
        if return_response:
            return response

        json = response.json()
        return json if model is None else model(**json)

    def _post(self, url, json=None, auth_cookie=None, timeout=5):
        client = self.httpx_client
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

    def _xget(self, url, params=None, headers=None, timeout=5, auth_cookie=None):
        client = self.httpx_client
        # Only set Cookie header if auth_cookie is not None
        # In Pyodide with 204 response, auth_cookie will be None and browser manages cookies
        if auth_cookie is not None:
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

    def get_roots(self):
        """
        Retrieves the list of available roots.

        Returns
        -------
        dict
            Dictionary mapping available root names to their details:
            - ``name``: the root name

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> roots_dict = client.get_roots()
        >>> sorted(roots_dict.keys())
        ['@public', 'b2tests', 'example', 'h5example', 'h5lung_j2k', 'h5numbers_j2k']
        >>> roots_dict['b2tests']
        {'name': 'b2tests'}
        """
        urlbase, _ = _format_paths(self.urlbase)
        return self._get(f"{self.urlbase}/api/roots", auth_cookie=self.cookie, timeout=self.timeout)

    def get(self, path):
        """
        Returns an object for the given path or object.

        Parameters
        ----------
        path : Path | Dataset | File | Root
            Either the desired object, or Path to the root, file or dataset.

        Returns
        -------
        Object : Root, File, Dataset
            Object representing the root, file or dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> root = client.get('example')
        >>> root.name
        'example'
        >>> file = client.get('example/README.md')
        >>> file.name
        'README.md'
        >>> ds = client.get('example/ds-1d.b2nd')
        >>> ds[:10]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        if isinstance(path, (File, Dataset, Root)):
            return path
        # Normalize the path to a POSIX path
        path = pathlib.PurePosixPath(path).as_posix()
        # Check if the path is a root or a file/dataset
        if "/" not in path:
            return Root(self, path)

        # If not a root, assume it's a file/dataset
        root_name, file_path = path.split("/", 1)
        root = Root(self, root_name)
        return root[file_path]

    def get_list(self, path):
        """
        Lists datasets in a specified path.

        Parameters
        ----------
        path : str
            Path to a root, directory or dataset.

        Returns
        -------
        list
            List of dataset names as strings, relative to the specified path.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> client.get_list('example')[:3]
        ['README.md', 'dir1/ds-2d.b2nd', 'dir1/ds-3d.b2nd']
        """
        urlbase, path = _format_paths(self.urlbase, path)
        return self._get(f"{self.urlbase}/api/list/{path}", auth_cookie=self.cookie, timeout=self.timeout)

    def get_info(self, path):
        """
        Retrieves information about a specified dataset.

        Parameters
        ----------
        path : str | Dataset | File
            Path to the dataset.

        Returns
        -------
        dict
            Dictionary of dataset properties, mapping property names to their values.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> path = 'example/ds-2d-fields.b2nd'
        >>> info = client.get_info(path)
        >>> info.keys()
        dict_keys(['shape', 'chunks', 'blocks', 'dtype', 'schunk', 'mtime'])
        >>> info['shape']
        [100, 200]
        """
        if isinstance(path, (Dataset, File)):
            path = path.path
        urlbase, path = _format_paths(self.urlbase, path)
        return self._get(f"{self.urlbase}/api/info/{path}", auth_cookie=self.cookie, timeout=self.timeout)

    def fetch(self, path, slice_=None):
        """
        Retrieves the entire content (or a specified slice) of a dataset.

        Parameters
        ----------
        path : str | Dataset
            Path or reference to the dataset.
        slice_ : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch. If None, the whole dataset is fetched.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset as a Numpy array.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> client.fetch('example/ds-2d-fields.b2nd', (slice(0, 2), slice(0, 2))
        array([[(0.0000000e+00, 1.       ), (5.0002502e-05, 1.00005  )],
               [(1.0000500e-02, 1.0100005), (1.0050503e-02, 1.0100505)]],
              dtype=[('a', '<f4'), ('b', '<f8')])
        """
        # Does the same as get_slice but forces return of np array
        return self.get_slice(path, key=slice_, as_blosc2=False)

    def get_slice(self, path, key=None, as_blosc2=True, field=None):
        """Get a slice of a File/Dataset.

        Parameters
        ----------
        path: str, Dataset, File
            Desired object to slice.
        key : int, slice, sequence of slices or str
            The slice to retrieve.  If a single slice is provided, it will be
            applied to the first dimension.  If a sequence of slices is
            provided, each slice will be applied to the corresponding
            dimension. If str, is interpreted as filter.
        as_blosc2 : bool
            If True (default), the result will be returned as a Blosc2 object
            (either a `SChunk` or `NDArray`).  If False, it will be returned
            as a NumPy array (equivalent to `self[key]`).
        field: str
            Shortcut to access a field in a structured array. If provided, `key` is ignored.

        Returns
        -------
        NDArray or SChunk or numpy.ndarray
            A new Blosc2 object containing the requested slice.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> client.get_slice('example/ds-2d-fields.b2nd', (slice(0, 2), slice(0, 2))[:]
        array([[(0.0000000e+00, 1.       ), (5.0002502e-05, 1.00005  )],
               [(1.0000500e-02, 1.0100005), (1.0050503e-02, 1.0100505)]],
              dtype=[('a', '<f4'), ('b', '<f8')])
        """
        if isinstance(path, (Dataset, File)):
            path = path.path
        urlbase, path = _format_paths(self.urlbase, path)
        if field:  # blosc2 doesn't support indexing of multiple fields
            return self._fetch_data(
                path,
                urlbase,
                {"field": field},
                auth_cookie=self.cookie,
                as_blosc2=as_blosc2,
                timeout=self.timeout,
            )
        if isinstance(key, str):
            # The key can still be a slice expression in string format (like for CLI utils)
            params = {"slice_": key} if _looks_like_slice(key) else {"filter": key}
            return self._fetch_data(
                path,
                urlbase,
                params=params,
                auth_cookie=self.cookie,
                as_blosc2=as_blosc2,
                timeout=self.timeout,
            )
        else:  # Convert slices to strings
            slice_ = api_utils.slice_to_string(key)
            # Fetch and return the data as a Blosc2 object / NumPy array
            return self._fetch_data(
                path,
                urlbase,
                {"slice_": slice_},
                auth_cookie=self.cookie,
                as_blosc2=as_blosc2,
                timeout=self.timeout,
            )

    def get_chunk(self, path, nchunk):
        """
        Retrieves a specified compressed chunk from a file.

        Parameters
        ----------
        path : str | Dataset
            Path of the dataset or a Dataset instance.
        nchunk : int
            ID of the unidimensional chunk to retrieve.

        Returns
        -------
        bytes obj
            The compressed chunk data.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> info_schunk = client.get_info('example/ds-2d-fields.b2nd')['schunk']
        >>> info_schunk['nchunks']
        1
        >>> info_schunk['cratio']
        6.453000645300064
        >>> chunk = client.get_chunk('example/ds-2d-fields.b2nd', 0)
        >>> info_schunk['chunksize'] / len(chunk)
        6.453000645300064
        """
        if isinstance(path, Dataset):
            path = path.path
        urlbase, path = _format_paths(self.urlbase, path)
        data = self._xget(
            f"{self.urlbase}/api/chunk/{path}",
            {"nchunk": nchunk},
            auth_cookie=self.cookie,
            timeout=self.timeout,
        )
        return data.content

    def _download_url(self, url, localpath, auth_cookie=None):
        client = self.httpx_client

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
            localpath = api_utils.b2_unpack(localpath)

        return localpath

    def download(self, dataset, localpath=None):
        """
        Downloads a dataset to local storage.

        **Note:** If the dataset is a regular file and Blosc2 is installed,
        it will be downloaded and decompressed. Otherwise, it will remain
        compressed in its `.b2` format.

        Parameters
        ----------
        dataset : Path
            Path to the dataset.
        localpath : Path, optional
            Local path to save the downloaded dataset. Defaults to the current
            working directory if not specified.

        Returns
        -------
        Path
            The path to the downloaded file on local disk.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> path = 'example/ds-2d-fields.b2nd'
        >>> client = cat2.Client('https://cat2.cloud/demo')
        >>> client.download(path)
        PosixPath('example/ds-2d-fields.b2nd')
        """
        urlbase, dataset = _format_paths(self.urlbase, dataset)
        url = api_utils.get_download_url(dataset, urlbase)
        localpath = pathlib.Path(localpath) if localpath else None
        if localpath is None:
            path = "." / pathlib.Path(dataset)
        elif localpath.is_dir():
            path = localpath / dataset.name
        else:
            path = localpath
        return self._download_url(url, str(path), auth_cookie=self.cookie)

    def _upload_file(self, local_dset, remotepath, urlbase, auth_cookie=None, compute=None):
        client = self.httpx_client
        url = f"{urlbase}/api/upload/{remotepath}"

        headers = {"Cookie": auth_cookie} if auth_cookie else None
        if hasattr(local_dset, "urlpath"):  # when passed reference to on-disk object
            local_dset = local_dset if local_dset.urlpath is None else local_dset.urlpath
        if isinstance(local_dset, (str, pathlib.Path)):
            suffx = local_dset.suffix if hasattr(local_dset, "suffix") else local_dset[-5:]
            if suffx == ".b2nd":
                obj = blosc2.open(local_dset)
                if isinstance(obj, blosc2.LazyArray):  # handle LazyArrays saved on-disk
                    compute = False if compute is None else compute
                    return self._upload_lazyarr(remotepath, obj, compute=compute)
            if compute is not None:
                raise ValueError("compute argument cannot be specified for non-LazyArray objects.")
            with open(local_dset, "rb") as f:
                response = client.post(url, files={"file": f}, headers=headers)
                response.raise_for_status()
        else:
            if isinstance(local_dset, blosc2.LazyArray):
                compute = False if compute is None else compute
                return self._upload_lazyarr(remotepath, local_dset, compute=compute)
            if compute is not None:
                raise ValueError("compute argument cannot be specified for non-LazyArray objects.")
            # in-memory object
            ndarray = (
                blosc2.asarray(local_dset)
                if hasattr(local_dset, "shape")
                else blosc2.SChunk(data=local_dset)
            )
            cframe = ndarray.to_cframe()
            f = io.BytesIO(cframe)
            response = client.post(url, files={"file": f}, headers=headers)
            response.raise_for_status()
        path = pathlib.PurePosixPath(response.json())
        return self.get(path)  # return reference to object

    def upload(self, local_dset, remotepath, compute=None):
        """
        Uploads a local dataset to a remote repository.

        **Note:** If `localpath` is a regular file without a `.b2nd`,
        `.b2frame` or `.b2` extension, it will be automatically compressed
        with Blosc2 on the server, adding a `.b2` extension internally.

        Parameters
        ----------
        local_dset : Path | in-memory object
            Path to the local dataset or an in-memory object (convertible to blosc2.SChunk).
        remotepath : Path
            Remote path to upload the dataset to.
        compute: None | bool
            For LazyArray objects, boolean flag indicating whether to compute the result eagerly or not.

        Returns
        -------
        Object : File, Dataset
            Object representing the file or dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To upload a file you need to be authenticated as an already registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> newpath = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> uploaded_path = client.upload('root-example/dir2/ds-4d.b2nd', newpath)
        >>> str(uploaded_path) == newpath
        True
        """
        urlbase, remotepath = _format_paths(self.urlbase, remotepath)
        return self._upload_file(
            local_dset,
            remotepath,
            urlbase,
            auth_cookie=self.cookie,
            compute=compute,
        )

    def _load_from_url(self, urlpath, remotepath, urlbase, auth_cookie=None):
        client = self.httpx_client
        url = f"{urlbase}/api/load_from_url/{remotepath}"

        headers = {"Cookie": auth_cookie} if auth_cookie else None
        response = client.post(url, data={"remote_url": urlpath}, headers=headers)
        response.raise_for_status()
        path = pathlib.PurePosixPath(response.json())
        return self.get(path)  # return reference to object

    def load_from_url(self, urlpath, dataset):
        """
        Loads a remote dataset to a remote repository.

        Parameters
        ----------
        urlpath : Path
            Url to the remote third party dataset.
        dataset : Path
            Remote path to place the dataset into.

        Returns
        -------
        Object : File, Dataset
            Object representing the file or dataset.
        """
        urlbase, _ = _format_paths(self.urlbase)
        return self._load_from_url(
            urlpath,
            dataset,
            urlbase,
            auth_cookie=self.cookie,
        )

    def append(self, remotepath, data):
        """
        Appends data to the remote location.

        Parameters
        ----------
        remotepath : Path
            Remote path of the dataset to enlarge.
        data : blosc2.NDArray, np.ndarray, sequence
            The data to append.

        Returns
        -------
        out: Dataset
            Object representing the modified dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To upload a file you need to be authenticated as an already registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> path = '@personal/ds-1d.b2nd'
        >>> client.copy('@public/examples/ds-1d.b2nd', path)
        PurePosixPath('@personal/ds-1d.b2nd')
        >>> ndarray = blosc2.arange(0, 10)
        >>> client.append(path, ndarray)
        (1010,)
        """
        if not hasattr(data, "shape"):
            array = np.asarray(data)
        else:
            array = data

        ndarray = blosc2.asarray(array)
        cframe = ndarray.to_cframe()
        file = io.BytesIO(cframe)
        old_shape = self.get(remotepath).shape
        append_shape = array.shape
        loc_shape = (old_shape[0] + append_shape[0],) + old_shape[1:]

        client = self.httpx_client
        url = f"{self.urlbase}/api/append/{remotepath}"
        headers = {"Cookie": self.cookie}
        response = client.post(url, files={"file": file}, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        new_shape = tuple(response.json())
        if loc_shape == new_shape:
            return self.get(remotepath)
        else:
            raise RuntimeError(
                f"Append shape incorrect: server-side shape is {new_shape} but should be {loc_shape}."
            )

    def unfold(self, remotepath):
        """
        Unfolds a dataset in the remote repository.

        The container is always unfolded into a directory with the same name as the
        container, but without the extension.

        Parameters
        ----------
        remotepath : Path | File
            Path of the dataset to unfold.

        Returns
        -------
        out : str
            Root of the unfolded dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To unfold a file you need to be a registered user
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> client.unfold('@personal/dir/data.h5')
        PurePosixPath('@personal/dir/data')
        """
        if isinstance(remotepath, File):
            remotepath = remotepath.path
        urlbase, path = _format_paths(self.urlbase, remotepath)
        result = self._post(
            f"{self.urlbase}/api/unfold/{path}", auth_cookie=self.cookie, timeout=self.timeout
        )
        return PurePosixPath(result)  # return path to top directory of dset

    def remove(self, path):
        """
        Removes a dataset or the contents of a directory from a remote repository.

        **Note:** When a directory is removed, only its contents are deleted;
        the directory itself remains. This behavior allows for future
        uploads to the same directory. It is subject to in future versions.

        Parameters
        ----------
        path : Path | File instance
            Path of the dataset (or dataset itself) or directory to remove.

        Returns
        -------
        Path
            The path that was removed.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To remove a file you need to be a registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> uploaded_path = client.upload('root-example/dir2/ds-4d.b2nd', path)
        >>> removed_path = client.remove(path)
        >>> removed_path == path
        True
        """
        if isinstance(path, File):
            path = path.path
        urlbase, path = _format_paths(self.urlbase, path)
        result = self._post(
            f"{self.urlbase}/api/remove/{path}", auth_cookie=self.cookie, timeout=self.timeout
        )
        return pathlib.PurePosixPath(result)  # path from which file removed

    def move(self, src, dst):
        """
        Moves a dataset or directory to a new location.

        Parameters
        ----------
        src : Path | File instance
            Path of the source dataset (or dataset itself) of the dataset or directory.
        dst : Path
            The destination path for the dataset or directory.

        Returns
        -------
        Object : Dataset, File
            Reference to object in new location.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To move a file you need to be a registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> uploaded_path = client.upload('root-example/dir2/ds-4d.b2nd', path)
        >>> newpath = f'@personal/dir{np.random.randint(0, 100)}/ds-4d-moved.b2nd'
        >>> moved_path = client.move(path, newpath)
        >>> str(moved_path) == newpath
        True
        >>> path.replace('@personal/', '') in client.get_list('@personal')
        False
        """
        if isinstance(src, File):
            src = src.path
        urlbase, _ = _format_paths(self.urlbase)
        result = self._post(
            f"{self.urlbase}/api/move/",
            {"src": str(src), "dst": str(dst)},
            auth_cookie=self.cookie,
            timeout=self.timeout,
        )
        path = pathlib.PurePosixPath(result)
        return self.get(path)  # get reference to object

    def copy(self, src, dst):
        """
        Copies a dataset or directory to a new location.

        Parameters
        ----------
        src : Path | File instance
            Path of the source dataset (or dataset itself) of the dataset or directory.
        dst : Path
            Destination path for the dataset or directory.

        Returns
        -------
        Object : Dataset, File
            Reference to copied object in copy location.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To copy a file you need to be a registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> src_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> uploaded_path = client.upload('root-example/dir2/ds-4d.b2nd', src_path)
        >>> copy_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d-copy.b2nd'
        >>> copied_path = client.copy(src_path, copy_path)
        >>> str(copied_path) == copy_path
        True
        >>> datasets = client.get_list('@personal')
        >>> src_path.replace('@personal/', '') in datasets
        True
        >>> copy_path.replace('@personal/', '') in datasets
        True
        """
        if isinstance(src, File):
            src = src.path
        urlbase, _ = _format_paths(self.urlbase)
        result = self._post(
            f"{self.urlbase}/api/copy/",
            {"src": str(src), "dst": str(dst)},
            auth_cookie=self.cookie,
            timeout=self.timeout,
        )
        path = pathlib.PurePosixPath(result)
        return self.get(path)  # get reference to object

    def _upload_lazyarr(self, remotepath, expression, compute=False):
        """
        Creates a lazy expression dataset.

        A dataset at the specified path will be created or overwritten if already
        exists.

        Parameters
        ----------
        remotepath : str
            Path to save the lazy expression to.
        expression : blosc2.LazyExpr | blosc2.LazyUDF
            Expression to be evaluated.
        operands : dict
            Mapping of variables in the expression to their corresponding dataset paths.
        compute : bool, optional
            If false, generate lazyexpr and do not compute anything.
            If true, compute lazy expression on creation and save (full) result.
            Default false.

        Returns
        -------
        Object: Dataset
            Pointer to server-hosted lazy dataset.
        """
        urlbase, remotepath = _format_paths(self.urlbase, remotepath)
        operands = expression.operands if hasattr(expression, "operands") else expression.inputs_dict
        if operands is not None:
            operands = {k: str(v) for k, v in operands.items()}
        else:
            operands = {}
        if isinstance(expression, blosc2.LazyExpr):
            expr = {
                "name": None,
                "expression": expression.expression,
                "func": None,
                "operands": operands,
                "dtype": str(expression.dtype),
                "shape": expression.shape,
                "compute": compute,
            }
        elif isinstance(expression, blosc2.LazyUDF):
            expr = {
                "name": expression.func.__name__,
                "expression": None,
                "func": textwrap.dedent(inspect.getsource(expression.func)).lstrip(),
                "operands": operands,
                "dtype": str(expression.dtype),
                "shape": expression.shape,
                "compute": compute,
            }
        else:
            raise ValueError("expr must be instance of blosc2.LazyUDF or blosc2.LazyExpr.")

        dataset = self._post(
            f"{self.urlbase}/api/upload_lazyarr/{remotepath}",
            expr,
            auth_cookie=self.cookie,
            timeout=self.timeout,
        )
        path = pathlib.PurePosixPath(dataset).as_posix()
        return self.get(path)  # return reference to object

    def adduser(self, newuser, password=None, superuser=False):
        """
        Adds a user to the server.

        Parameters
        ----------
        newuser : str
            Username of the user to add.
        password : str, optional
            Password for the user to add.
        superuser : bool, optional
            Indicates if the user is a superuser.

        Returns
        -------
        str
            An explanatory message about the operation's success or failure.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To add a user you need to be a superuser
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> username = f'user{np.random.randint(0, 100)}@example.com'
        >>> message = client.adduser(username, 'foo')
        >>> f"User added: username='{username}' password='foo' superuser=False" == message
        True
        """
        urlbase, _ = _format_paths(self.urlbase)
        return self._post(
            f"{self.urlbase}/api/adduser/",
            {"username": newuser, "password": password, "superuser": superuser},
            auth_cookie=self.cookie,
        )

    def deluser(self, user):
        """
        Deletes a user from the server.

        Parameters
        ----------
        username : str
            Username of the user to delete.

        Returns
        -------
        str
            An explanatory message about the operation's success or failure.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To delete a user you need to be a superuser
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> username = f'user{np.random.randint(0, 100)}@example.com'
        >>> _ = client.adduser(username, 'foo')
        >>> message = client.deluser(username)
        >>> message == f"User deleted: {username}"
        True
        """
        urlbase, _ = _format_paths(self.urlbase)
        return self._get(f"{self.urlbase}/api/deluser/{user}", auth_cookie=self.cookie)

    def listusers(self, username=None):
        """
        Lists the users in the server.

        Parameters
        ----------
        username : str, optional
            Username of the specific user to list.

        Returns
        -------
        list of dict
            A list of user dictionaries in the server.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To list the users you need to be a superuser
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> users = client.listusers()
        >>> sorted(users[0].keys())
        ['email', 'hashed_password', 'id', 'is_active', 'is_superuser', 'is_verified']
        >>> username = f'user{np.random.randint(0, 100)}@example.com'
        >>> _ = client.adduser(username, 'foo')
        >>> updated_users = client.listusers()
        >>> len(users) + 1 == len(updated_users)
        True
        >>> user_info = client.listusers(username)
        >>> user_info[0]['is_superuser']
        False
        >>> superuser_info = client.listusers('superuser@example.com')
        >>> superuser_info[0]['is_superuser']
        True
        """
        urlbase, _ = _format_paths(self.urlbase)
        url = f"{self.urlbase}/api/listusers/" + (f"?username={username}" if username else "")
        return self._get(url, auth_cookie=self.cookie)
