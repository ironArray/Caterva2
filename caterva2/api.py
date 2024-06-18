###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
"""
This module provides a Python API to Caterva2.
"""

import functools
import pathlib

from caterva2 import api_utils, utils


# Defaults
bro_host_default = 'localhost:8000'
"""The default HTTP endpoint for the broker (URL host & port)."""

pub_host_default = 'localhost:8001'
"""The default HTTP endpoint for the publisher (URL host & port)."""

sub_host_default = 'localhost:8002'
"""The default HTTP endpoint for the subscriber (URL host & port)."""

sub_urlbase_default = f'http://{sub_host_default}/'
"""The default base for URLs provided by the subscriber (slash-terminated)."""


def _format_paths(sub_url, path=None):
    if sub_url is not None:
        if isinstance(sub_url, pathlib.Path):
            sub_url = sub_url.as_posix()
        if not sub_url.endswith("/"):
            sub_url += "/"
            sub_url = pathlib.Path(sub_url)
    if path is not None:
        p = path.as_posix() if isinstance(path, pathlib.Path) else path
        if p.startswith("/"):
            raise ValueError("The path should not start with a slash")
        if p.endswith("/"):
            raise ValueError("The path should not end with a slash")
    return sub_url, path


def get_roots(sub_url=sub_urlbase_default, auth_cookie=None):
    """
    Get the list of available roots.

    Parameters
    ----------

    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        The list of available roots.

    """
    sub_url, _ = _format_paths(sub_url)
    return api_utils.get(f'{sub_url}api/roots', auth_cookie=auth_cookie)


def subscribe(root, sub_url=sub_urlbase_default, auth_cookie=None):
    """
    Subscribe to a root.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The response from the server.
    """
    sub_url, root = _format_paths(sub_url, root)
    return api_utils.post(f'{sub_url}api/subscribe/{root}',
                          auth_cookie=auth_cookie)


def get_list(root, sub_url=sub_urlbase_default, auth_cookie=None):
    """
    List the nodes in a root.

    Parameters
    ----------
    root : str
        The name of the root to list.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    list
        The list of nodes in the root.
    """
    sub_url, root = _format_paths(sub_url, root)
    return api_utils.get(f'{sub_url}api/list/{root}',
                         auth_cookie=auth_cookie)


def get_info(dataset, sub_url=sub_urlbase_default, auth_cookie=None):
    """
    Get information about a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        The information about the dataset.
    """
    sub_url, dataset = _format_paths(sub_url, dataset)
    return api_utils.get(f'{sub_url}api/info/{dataset}',
                         auth_cookie=auth_cookie)


def fetch(dataset, sub_url=sub_urlbase_default, slice_=None,
          auth_cookie=None):
    """
    Fetch a slice of a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    slice_ : str
        The slice to fetch.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    numpy.ndarray
        The slice of the dataset.
    """
    sub_url, dataset = _format_paths(sub_url, dataset)
    data = api_utils.fetch_data(dataset, sub_url,
                                {'slice_': slice_},
                                auth_cookie=auth_cookie)
    return data


def download(dataset, sub_url=sub_urlbase_default, auth_cookie=None):
    """
    Download a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The path to the downloaded file.

    Note: If dataset is a regular file, it will be downloaded and decompressed if blosc2
     is installed. Otherwise, it will be downloaded as-is from the internal caches (i.e.
     compressed with Blosc2, and with the `.b2` extension).
    """
    sub_url, dataset = _format_paths(sub_url, dataset)
    url = api_utils.get_download_url(dataset, sub_url)
    return api_utils.download_url(url, dataset, try_unpack=api_utils.blosc2_is_here,
                                  auth_cookie=auth_cookie)


class Root:
    """
    A root is a remote repository that can be subscribed to.

    If a non-empty `user_auth` mapping is given, its items are used as data to be posted
    for authenticating the user and get an authorization token for further requests.
    """
    def __init__(self, name, sub_url=sub_urlbase_default, user_auth=None):
        sub_url, name = _format_paths(sub_url, name)
        self.name = name
        self.sub_url = utils.urlbase_type(sub_url)
        self.auth_cookie = (
            api_utils.get_auth_cookie(sub_url, user_auth)
            if user_auth else None)

        ret = api_utils.post(f'{sub_url}api/subscribe/{name}',
                             auth_cookie=self.auth_cookie)
        if ret != 'Ok':
            roots = get_roots(sub_url)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
        self.node_list = api_utils.get(f'{sub_url}api/list/{name}',
                                       auth_cookie=self.auth_cookie)

    def __repr__(self):
        return f'<Root: {self.name}>'

    def __getitem__(self, node):
        """
        Get a file or dataset from the root.
        """
        if node.endswith((".b2nd", ".b2frame")):
            return Dataset(node, root=self.name, sub_url=self.sub_url,
                           auth_cookie=self.auth_cookie)
        else:
            return File(node, root=self.name, sub_url=self.sub_url,
                        auth_cookie=self.auth_cookie)


class File:
    """
    A file is either a Blosc2 dataset or a regular file on a root repository.

    Parameters
    ----------
    name : str
        The name of the file.
    root : str
        The name of the root.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie: str
        An optional cookie to authorize requests via HTTP.

    Examples
    --------
    >>> root = cat2.Root('foo')
    >>> file = root['README.md']
    >>> file.name
    'README.md'
    >>> file.sub_url
    'http://localhost:8002/'
    >>> file.path
    PosixPath('foo/README.md')
    >>> file.meta['cparams']
    {'codec': 5, 'typesize': 1, 'blocksize': 32768}
    >>> file[:25]
    b'This is a simple example,'
    >>> file[0]
    b'T'
    """
    def __init__(self, name, root, sub_url, auth_cookie=None):
        sub_url, name = _format_paths(sub_url, name)
        _, root = _format_paths(None, root)
        self.root = root
        self.name = name
        self.sub_url = sub_url
        self.path = pathlib.Path(f'{self.root}/{self.name}')
        self.auth_cookie = auth_cookie
        self.meta = api_utils.get(f'{sub_url}api/info/{self.path}',
                                  auth_cookie=self.auth_cookie)
        # TODO: 'cparams' is not always present (e.g. for .b2nd files)
        # print(f"self.meta: {self.meta['cparams']}")

    def __repr__(self):
        return f'<File: {self.path}>'

    @functools.cached_property
    def vlmeta(self):
        """
        Access variable-length metalayers (i.e. user attributes) for a file.

        Examples
        --------
        >>> root = cat2.Root('foo')
        >>> file = root['ds-sc-attr.b2nd']
        >>> file.vlmeta
        {'a': 1, 'b': 'foo', 'c': 123.456}

        Returns
        -------
        dict
            The mapping of metalayer names to their respective values.
        """
        schunk_meta = self.meta.get('schunk', self.meta)
        return schunk_meta.get('vlmeta', {})

    def get_download_url(self):
        """
        Get the download URL for a file.

        Returns
        -------
        str
            The download URL.

        Examples
        --------
        >>> root = cat2.Root('foo')
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'http://localhost:8002/api/fetch/foo/ds-1d.b2nd'
        """
        return api_utils.get_download_url(self.path, self.sub_url)

    def __getitem__(self, slice_):
        """
        Get a slice of the dataset.

        Parameters
        ----------
        slice_ : int, slice, tuple of ints and slices, or None
            The slice to fetch.

        Returns
        -------
        numpy.ndarray
            The slice.

        Examples
        --------
        >>> root = cat2.Root('foo')
        >>> ds = root['ds-1d.b2nd']
        >>> ds[1]
        array(1)
        >>> ds[:1]
        array([0])
        >>> ds[0:10]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        data = self.fetch(slice_=slice_)
        return data

    def fetch(self, slice_=None):
        """
        Fetch a slice of a dataset.  Can specify transport serialization.

        Similar to `__getitem__()` but this one lets specify whether to prefer using Blosc2
        schunk serialization during data transport between the subscriber and the
        client. See below.

        Parameters
        ----------
        slice_ : int, slice, tuple of ints and slices, or None
            The slice to fetch.

        Returns
        -------
        numpy.ndarray
            The slice of the dataset.
        """
        slice_ = api_utils.slice_to_string(slice_)
        data = api_utils.fetch_data(self.path, self.sub_url,
                                    {'slice_': slice_},
                                    auth_cookie=self.auth_cookie)
        return data

    def download(self):
        """
        Download a file.

        Returns
        -------
        PosixPath
            The path to the downloaded file.

        Examples
        --------
        >>> root = cat2.Root('foo')
        >>> file = root['ds-1d.b2nd']
        >>> file.download()
        PosixPath('foo/ds-1d.b2nd')
        """
        urlpath = self.get_download_url()
        return api_utils.download_url(urlpath, str(self.path),
                                      auth_cookie=self.auth_cookie)


class Dataset(File):
    """
    A dataset is a Blosc2 container in a file.

    Parameters
    ----------
    name : str
        The name of the dataset.
    root : str
        The name of the root.
    sub_url : str
        The base URL (slash-terminated) of the subscriber to query.
    auth_cookie: str
        An optional cookie to authorize requests via HTTP.

    Examples
    --------
    >>> root = cat2.Root('foo')
    >>> ds = root['ds-1d.b2nd']
    >>> ds.name
    'ds-1d.b2nd'
    >>> ds[1:10]
    array([1, 2, 3, 4, 5, 6, 7, 8, 9])
    """
    def __init__(self, name, root, sub_url, auth_cookie=None):
        super().__init__(name, root, sub_url, auth_cookie)

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f'<Dataset: {self.path}>'
