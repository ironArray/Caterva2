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
"""The default base of URLs provided by the subscriber (slash-terminated)."""


def _format_paths(urlbase, path=None):
    if urlbase is not None:
        if isinstance(urlbase, pathlib.Path):
            urlbase = urlbase.as_posix()
        if not urlbase.endswith("/"):
            urlbase += "/"
            urlbase = pathlib.Path(urlbase)
    if path is not None:
        p = path.as_posix() if isinstance(path, pathlib.Path) else path
        if p.startswith("/"):
            raise ValueError("The path should not start with a slash")
        if p.endswith("/"):
            raise ValueError("The path should not end with a slash")
    return urlbase, path


def get_roots(urlbase=sub_urlbase_default, auth_cookie=None):
    """
    Get the list of available roots.

    Parameters
    ----------

    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        A mapping of available root names to their ``name``, ``http``
        endpoint and whether they are ``subscribed`` or not.

    """
    urlbase, _ = _format_paths(urlbase)
    return api_utils.get(f'{urlbase}api/roots', auth_cookie=auth_cookie)


def subscribe(root, urlbase=sub_urlbase_default, auth_cookie=None):
    """
    Subscribe to a root.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The response from the server.
    """
    urlbase, root = _format_paths(urlbase, root)
    return api_utils.post(f'{urlbase}api/subscribe/{root}',
                          auth_cookie=auth_cookie)


def get_list(root, urlbase=sub_urlbase_default, auth_cookie=None):
    """
    List the nodes in a root.

    Parameters
    ----------
    root : str
        The name of the root to list.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    list
        The list of nodes in the root, as name strings relative to it.
    """
    urlbase, root = _format_paths(urlbase, root)
    return api_utils.get(f'{urlbase}api/list/{root}',
                         auth_cookie=auth_cookie)


def get_info(path, urlbase=sub_urlbase_default, auth_cookie=None):
    """
    Get information about a dataset.

    Parameters
    ----------
    path : str
        The path of the dataset.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        The information about the dataset, as a mapping of property names to
        their respective values.
    """
    urlbase, path = _format_paths(urlbase, path)
    return api_utils.get(f'{urlbase}api/info/{path}',
                         auth_cookie=auth_cookie)


def fetch(path, urlbase=sub_urlbase_default, slice_=None,
          auth_cookie=None):
    """
    Fetch (a slice of) the data in a dataset.

    Parameters
    ----------
    path : str
        The path of the dataset.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    slice_ : str
        The slice to fetch (the whole dataset if missing).
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    numpy.ndarray
        The slice of the dataset.
    """
    urlbase, path = _format_paths(urlbase, path)
    data = api_utils.fetch_data(path, urlbase,
                                {'slice_': slice_},
                                auth_cookie=auth_cookie)
    return data


def download(path, urlbase=sub_urlbase_default, auth_cookie=None):
    """
    Download a dataset to storage.

    **Note:** If the dataset is a regular file, it will be downloaded and
    decompressed if Blosc2 is installed.  Otherwise, it will be downloaded
    as-is from the internal caches (i.e. compressed with Blosc2, and with the
    `.b2` extension).

    Parameters
    ----------
    path : str
        The path of the dataset.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The path to the downloaded file.
    """
    urlbase, path = _format_paths(urlbase, path)
    url = api_utils.get_download_url(path, urlbase)
    return api_utils.download_url(url, path, try_unpack=api_utils.blosc2_is_here,
                                  auth_cookie=auth_cookie)


def lazyexpr(name, expression, operands,
             urlbase=sub_urlbase_default, auth_cookie=None):
    """
    Create a lazy expression dataset in scratch space.

    A dataset with the given name is created anew (or overwritten if already
    existing).

    Parameters
    ----------
    name : str
        The name of the dataset to be created (without extension).
    expression : str
        The expression to be evaluated.  It must result in a lazy expression.
    operands : dict
        A mapping of the variables used in the expression to the dataset paths
        that they refer to.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The path of the created dataset.
    """
    urlbase, _ = _format_paths(urlbase)
    expr = dict(name=name, expression=expression, operands=operands)
    return api_utils.post(f'{urlbase}api/lazyexpr/', expr,
                          auth_cookie=auth_cookie)


class Root:
    """
    A root is a remote repository that can be subscribed to.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    user_auth : dict
        An optional mapping of fields and values to be used as data to be
        posted for authenticating the user and get an authorization token for
        further requests.
    """
    def __init__(self, name, urlbase=sub_urlbase_default, user_auth=None):
        urlbase, name = _format_paths(urlbase, name)
        self.name = name
        self.urlbase = utils.urlbase_type(urlbase)
        self.auth_cookie = (
            api_utils.get_auth_cookie(urlbase, user_auth)
            if user_auth else None)

        ret = api_utils.post(f'{urlbase}api/subscribe/{name}',
                             auth_cookie=self.auth_cookie)
        if ret != 'Ok':
            roots = get_roots(urlbase)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
        self.node_list = api_utils.get(f'{urlbase}api/list/{name}',
                                       auth_cookie=self.auth_cookie)

    def __repr__(self):
        return f'<Root: {self.name}>'

    def __getitem__(self, node):
        """
        Get a file or dataset from the root.

        Parameters
        ----------
        node : str
            The path of the file or dataset.

        Returns
        -------
        File
            A :class:`File` or :class:`Dataset` instance.
        """
        if node.endswith((".b2nd", ".b2frame")):
            return Dataset(node, root=self.name, urlbase=self.urlbase,
                           auth_cookie=self.auth_cookie)
        else:
            return File(node, root=self.name, urlbase=self.urlbase,
                        auth_cookie=self.auth_cookie)


class File:
    """
    A file is either a Blosc2 dataset or a regular file on a root repository.

    This is not intended to be instantiated directly, but accessed via a
    :class:`Root` instance instead.

    Parameters
    ----------
    name : str
        The name of the file.
    root : str
        The name of the root.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
    auth_cookie: str
        An optional cookie to authorize requests via HTTP.

    Examples
    --------
    >>> root = cat2.Root('foo')
    >>> file = root['README.md']
    >>> file.name
    'README.md'
    >>> file.urlbase
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
    def __init__(self, name, root, urlbase, auth_cookie=None):
        urlbase, name = _format_paths(urlbase, name)
        _, root = _format_paths(None, root)
        self.root = root
        self.name = name
        self.urlbase = urlbase
        self.path = pathlib.Path(f'{self.root}/{self.name}')
        self.auth_cookie = auth_cookie
        self.meta = api_utils.get(f'{urlbase}api/info/{self.path}',
                                  auth_cookie=self.auth_cookie)
        # TODO: 'cparams' is not always present (e.g. for .b2nd files)
        # print(f"self.meta: {self.meta['cparams']}")

    def __repr__(self):
        return f'<File: {self.path}>'

    @functools.cached_property
    def vlmeta(self):
        """
        A mapping of metalayer names to their respective values.

        Used to access variable-length metalayers (i.e. user attributes) for a
        file.

        >>> root = cat2.Root('foo')
        >>> file = root['ds-sc-attr.b2nd']
        >>> file.vlmeta
        {'a': 1, 'b': 'foo', 'c': 123.456}
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
        return api_utils.get_download_url(self.path, self.urlbase)

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
            The slice of the dataset.

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
        Fetch a slice of a dataset.

        Equivalent to `__getitem__()`.

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
        data = api_utils.fetch_data(self.path, self.urlbase,
                                    {'slice_': slice_},
                                    auth_cookie=self.auth_cookie)
        return data

    def download(self):
        """
        Download a file to storage.

        Returns
        -------
        pathlib.PosixPath
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

    This is not intended to be instantiated directly, but accessed via a
    :class:`Root` instance instead.

    Parameters
    ----------
    name : str
        The name of the dataset.
    root : str
        The name of the root.
    urlbase : str
        The base of URLs (slash-terminated) of the subscriber to query.
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
    def __init__(self, name, root, urlbase, auth_cookie=None):
        super().__init__(name, root, urlbase, auth_cookie)

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f'<Dataset: {self.path}>'
