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

import pathlib

from caterva2 import api_utils


# Defaults
bro_host_default = 'localhost:8000'
pub_host_default = 'localhost:8001'
sub_host_default = 'localhost:8002'


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
    A file is either a Blosc2 dataset or a regular file.

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
    >>> file = root['README.md']
    >>> file.name
    'README.md'
    >>> file.host
    'localhost:8002'
    >>> file.path
    PosixPath('foo/README.md')
    """
    def __init__(self, name, root, host):
        self.root = root
        self.name = name
        self.host = host
        self.path = pathlib.Path(f'{self.root}/{self.name}')

    def __repr__(self):
        return f'<File: {self.path}>'

    def get_download_url(self, key=None):
        """
        Get the download URL for a slice of the file.

        Parameters
        ----------
        key : int or slice
            The slice to get.

        Returns
        -------
        str
            The download URL.

        Examples
        --------
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'http://localhost:8002/files/foo/ds-1d.b2nd'
        >>> file.get_download_url(1)
        'http://localhost:8002/files/downloads/foo/ds-1d[1].b2nd'
        >>> file.get_download_url(slice(0, 10))
        'http://localhost:8002/files/downloads/foo/ds-1d[:10].b2nd'
        """
        slice_ = api_utils.slice_to_string(key)
        download_path = api_utils.get_download_url(
            self.host, self.path, {'slice_': slice_, 'download': True})
        return download_path

    def download(self, key=None):
        """
        Download a slice of the file.

        Parameters
        ----------
        key : int or slice
            The slice to get.

        Returns
        -------
        PosixPath
            The path to the downloaded file.

        Examples
        --------
        >>> file = root['ds-1d.b2nd']
        >>> file.download()
        PosixPath('foo/ds-1d.b2nd')
        >>> file.download(1)
        PosixPath('foo/ds-1d[1].b2nd')
        >>> file.download(slice(0, 10))
        PosixPath('foo/ds-1d[:10].b2nd')
        """
        url = self.get_download_url(key)
        slice_ = api_utils.slice_to_string(key)
        return api_utils.download_url(url, self.path, slice_=slice_)

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
    >>> ds = root['ds-1d.b2nd']
    >>> ds.name
    'ds-1d.b2nd'
    >>> ds[1:10]
    array([1, 2, 3, 4, 5, 6, 7, 8, 9])
    """
    def __init__(self, name, root, host):
        super().__init__(name, root, host)
        self.json = api_utils.get(f'http://{host}/api/info/{self.path}')

    def __repr__(self):
        return f'<Dataset: {self.path}>'

    def __getitem__(self, key):
        """
        Get a slice of the dataset.

        Parameters
        ----------
        key : int or slice
            The slice to get.

        Returns
        -------
        numpy.ndarray
            The slice.

        Examples
        --------
        >>> ds = root['ds-1d.b2nd']
        >>> ds[1]
        array(1)
        >>> ds[:1]
        array([0])
        >>> ds[0:10]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        slice_ = api_utils.slice_to_string(key)
        data = api_utils.get_download_url(self.host, self.path, {'slice_': slice_})
        return data
