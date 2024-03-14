###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
"""
This module provides a Python API to Caterva2.
"""

import functools
import pathlib

from caterva2 import api_utils


# Defaults
bro_host_default = 'localhost:8000'
"""The default HTTP endpoint for the broker (URL host & port)."""

pub_host_default = 'localhost:8001'
"""The default HTTP endpoint for the publisher (URL host & port)."""

sub_host_default = 'localhost:8002'
"""The default HTTP endpoint for the subscriber (URL host & port)."""


def get_roots(host=sub_host_default):
    """
    Get the list of available roots.

    Parameters
    ----------

    host : str
        The host to query.

    Returns
    -------
    dict
        The list of available roots.

    """
    return api_utils.get(f'http://{host}/api/roots')


def subscribe(root, host=sub_host_default):
    """
    Subscribe to a root.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    host : str
        The host to query.

    Returns
    -------
    str
        The response from the server.
    """
    return api_utils.post(f'http://{host}/api/subscribe/{root}')


def get_list(root, host=sub_host_default):
    """
    List the nodes in a root.

    Parameters
    ----------
    root : str
        The name of the root to list.
    host : str
        The host to query.

    Returns
    -------
    list
        The list of nodes in the root.
    """
    return api_utils.get(f'http://{host}/api/list/{root}')


def get_info(dataset, host=sub_host_default):
    """
    Get information about a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    host : str
        The host to query.

    Returns
    -------
    dict
        The information about the dataset.
    """
    return api_utils.get(f'http://{host}/api/info/{dataset}')


def fetch(dataset, host=sub_host_default, slice_=None, prefer_schunk=True):
    """
    Fetch a slice of a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    host : str
        The host to query.
    slice_ : str
        The slice to fetch.
    prefer_schunk : bool
        Whether to prefer using Blosc2 schunk serialization during data transport.
        If False, pickle will always be used instead. Default is True, so Blosc2
        serialization will be used if Blosc2 is installed (and data payload is large
        enough).

    Returns
    -------
    numpy.ndarray
        The slice of the dataset.
    """
    prefer_schunk = api_utils.blosc2_is_here and prefer_schunk
    data = api_utils.fetch_data(dataset, host,
                                {'slice_': slice_, 'prefer_schunk': prefer_schunk})
    return data


def download(dataset, host=sub_host_default):
    """
    Download a dataset.

    Parameters
    ----------
    dataset : str
        The name of the dataset.
    host : str
        The host to query.

    Returns
    -------
    str
        The path to the downloaded file.

    Note: If dataset is a regular file, it will be downloaded and decompressed if blosc2
     is installed. Otherwise, it will be downloaded as-is from the internal caches (i.e.
     compressed with Blosc2, and with the `.b2` extension).
    """
    url = api_utils.get_download_url(dataset, host)
    return api_utils.download_url(url, dataset, try_unpack=api_utils.blosc2_is_here)


class Root:
    """
    A root is a remote repository that can be subscribed to.
    """
    def __init__(self, name, host=sub_host_default):
        self.name = name
        self.host = host
        ret = api_utils.post(f'http://{host}/api/subscribe/{name}')
        if ret != 'Ok':
            roots = get_roots(host)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
        self.node_list = api_utils.get(f'http://{host}/api/list/{name}')

    def __repr__(self):
        return f'<Root: {self.name}>'

    def __getitem__(self, node):
        """
        Get a file or dataset from the root.
        """
        if node.endswith((".b2nd", ".b2frame")):
            return Dataset(node, root=self.name, host=self.host)
        else:
            return File(node, root=self.name, host=self.host)


class File:
    """
    A file is either a Blosc2 dataset or a regular file on a root repository.

    Parameters
    ----------
    name : str
        The name of the file.
    root : str
        The name of the root.
    host : str
        The host to query.

    Examples
    --------
    >>> root = cat2.Root('foo')
    >>> file = root['README.md']
    >>> file.name
    'README.md'
    >>> file.host
    'localhost:8002'
    >>> file.path
    PosixPath('foo/README.md')
    >>> file.meta['cparams']
    {'codec': 5, 'typesize': 1, 'blocksize': 32768}
    >>> file[:25]
    b'This is a simple example,'
    >>> file[0]
    b'T'
    """
    def __init__(self, name, root, host):
        self.root = root
        self.name = name
        self.host = host
        self.path = pathlib.Path(f'{self.root}/{self.name}')
        self.meta = api_utils.get(f'http://{host}/api/info/{self.path}')
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
        >>> file = root['ds-1d.b2nd']
        >>> file.vlmeta
        {}

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
        'http://localhost:8002/files/foo/ds-1d.b2nd'
        """
        download_path = api_utils.get_download_url(self.path, self.host)
        return download_path

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

    def fetch(self, slice_=None, prefer_schunk=True):
        """
        Fetch a slice of a dataset.  Can specify transport serialization.

        Similar to `__getitem__()` but this one lets specify whether to prefer using Blosc2
        schunk serialization or pickle during data transport between the subscriber and the
        client. See below.

        Parameters
        ----------
        slice_ : int, slice, tuple of ints and slices, or None
            The slice to fetch.
        prefer_schunk : bool
            Whether to prefer using Blosc2 schunk serialization during data transport.
            If False, pickle will always be used instead. Default is True, so Blosc2
            serialization will be used if Blosc2 is installed (and data payload is large
            enough).

        Returns
        -------
        numpy.ndarray
            The slice of the dataset.
        """
        slice_ = api_utils.slice_to_string(slice_)
        prefer_schunk = api_utils.blosc2_is_here and prefer_schunk
        data = api_utils.fetch_data(self.path, self.host,
                                    {'slice_': slice_, 'prefer_schunk': prefer_schunk})
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
        return api_utils.download_url(urlpath, str(self.path))


class Dataset(File):
    """
    A dataset is a Blosc2 container in a file.

    Parameters
    ----------
    name : str
        The name of the dataset.
    root : str
        The name of the root.
    host : str
        The host to query.

    Examples
    --------
    >>> root = cat2.Root('foo')
    >>> ds = root['ds-1d.b2nd']
    >>> ds.name
    'ds-1d.b2nd'
    >>> ds[1:10]
    array([1, 2, 3, 4, 5, 6, 7, 8, 9])
    """
    def __init__(self, name, root, host):
        super().__init__(name, root, host)

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f'<Dataset: {self.path}>'
