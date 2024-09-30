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
from contextlib import contextmanager

from caterva2 import api_utils, utils


# Defaults
bro_host_default = 'localhost:8000'
"""The default HTTP endpoint for the broker (URL host & port)."""

pub_host_default = 'localhost:8001'
"""The default HTTP endpoint for the publisher (URL host & port)."""

sub_host_default = 'localhost:8002'
"""The default HTTP endpoint for the subscriber (URL host & port)."""

sub_urlbase_default = f'http://{sub_host_default}'
"""The default base of URLs provided by the subscriber."""

_subscriber_data = {
    "urlbase": sub_urlbase_default,
    "auth_cookie": "",
}
"""Caterva2 subscriber data saved by context manager."""


def _format_paths(urlbase, path=None):
    if urlbase is None:
        urlbase = _subscriber_data['urlbase']
    if isinstance(urlbase, pathlib.Path):
        urlbase = urlbase.as_posix()
    if urlbase.endswith("/"):
        urlbase = urlbase[:-1]
        urlbase = pathlib.Path(urlbase)
    if path is not None:
        p = path.as_posix() if isinstance(path, pathlib.Path) else path
        if p.startswith("/"):
            raise ValueError("path cannot start with a slash")
    return urlbase, path


def get_roots(urlbase=None, auth_cookie=None):
    """
    Get the list of available roots.

    Parameters
    ----------

    urlbase : str
        The base of URLs of the subscriber to query. The default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        A mapping of available root names to their ``name``, ``http``
        endpoint and whether they are ``subscribed`` or not.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> roots_dict = cat2.get_roots("https://demo.caterva2.net")
    >>> roots_dict.keys()
    dict_keys(['b2tests', 'h5lung_j2k', 'h5numbers_j2k', 'example', 'h5example'])
    >>> roots_dict['b2tests']
    {'name': 'b2tests', 'http': 'localhost:8014', 'subscribed': True}
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f'{urlbase}/api/roots', auth_cookie=auth_cookie)


def subscribe(root, urlbase=None, auth_cookie=None):
    """
    Subscribe to a root.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The response from the server.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://demo.caterva2.net'
    >>> root_name = 'h5numbers_j2k'
    >>> cat2.subscribe(root_name, urlbase)
    'Ok'
    >>> cat2.get_roots(urlbase)[root_name]
    {'name': 'h5numbers_j2k', 'http': 'localhost:8011', 'subscribed': True}
    """
    urlbase, root = _format_paths(urlbase, root)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.post(f'{urlbase}/api/subscribe/{root}',
                          auth_cookie=auth_cookie)


def get_list(path, urlbase=None, auth_cookie=None):
    """
    List the datasets in a path.

    Parameters
    ----------
    path : str
        The path to a root, directory or dataset.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    list
        The list of datasets, as name strings relative to it.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://demo.caterva2.net'
    >>> cat2.subscribe('example', urlbase)
    'Ok'
    >>> cat2.get_list('example', urlbase)[:3]
    ['ds-2d-fields.b2nd', 'lung-jpeg2000_10x.b2nd', 'ds-1d-fields.b2nd']
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f'{urlbase}/api/list/{path}',
                         auth_cookie=auth_cookie)


def get_info(path, urlbase=None, auth_cookie=None):
    """
    Get information about a dataset.

    Parameters
    ----------
    path : str
        The path of the dataset.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    dict
        The information about the dataset, as a mapping of property names to
        their respective values.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://demo.caterva2.net'
    >>> cat2.subscribe('example', urlbase)
    'Ok'
    >>> path = 'example/ds-2d-fields.b2nd'
    >>> info = cat2.get_info(path, urlbase)
    >>> info.keys()
    dict_keys(['shape', 'chunks', 'blocks', 'dtype', 'schunk'])
    >>> info['shape']
    [100, 200]
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f'{urlbase}/api/info/{path}',
                         auth_cookie=auth_cookie)


def fetch(path, urlbase=None, slice_=None,
          auth_cookie=None):
    """
    Fetch (a slice of) the data in a dataset.

    Parameters
    ----------
    path : str
        The path of the dataset.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    slice_ : str
        The slice to fetch (the whole dataset if missing).
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    numpy.ndarray
        The slice of the dataset.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://demo.caterva2.net'
    >>> cat2.subscribe('example', urlbase)
    'Ok'
    >>> cat2.fetch('example/ds-2d-fields.b2nd', urlbase, "0:2, 0:2")
    array([[(0.0000000e+00, 1.       ), (5.0002502e-05, 1.00005  )],
       [(1.0000500e-02, 1.0100005), (1.0050503e-02, 1.0100505)]],
      dtype=[('a', '<f4'), ('b', '<f8')])
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    data = api_utils.fetch_data(path, urlbase,
                                {'slice_': slice_},
                                auth_cookie=auth_cookie)
    return data


def get_chunk(path, nchunk, urlbase=None, auth_cookie=None):
    """
    Get the unidimensional compressed chunk of a dataset.

    Parameters
    ----------
    path : str
        The path of the dataset.
    nchunk : int
        The unidimensional chunk id to get.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    bytes obj
        The compressed chunk.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> urlbase = 'https://demo.caterva2.net'
    >>> cat2.subscribe('example', urlbase)
    'Ok'
    >>> info_schunk = cat2.get_info('example/ds-2d-fields.b2nd', urlbase)['schunk']
    >>> info_schunk['nchunks']
    1
    >>> info_schunk['cratio']
    6.453000645300064
    >>> chunk = cat2.get_chunk('example/ds-2d-fields.b2nd', 0,  urlbase)
    >>> info_schunk['chunksize'] / len(chunk)
    6.453000645300064
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    data = api_utils._xget(f'{urlbase}/api/chunk/{path}', {'nchunk': nchunk},
                           auth_cookie=auth_cookie)
    return data.content


def download(dataset, localpath=None, urlbase=None, auth_cookie=None):
    """
    Download a dataset to storage.

    **Note:** If the dataset is a regular file, it will be downloaded and
    decompressed if Blosc2 is installed.  Otherwise, it will be downloaded
    as-is (i.e. compressed with Blosc2, and with the `.b2` extension).

    Parameters
    ----------
    dataset : Path
        The path of the dataset.
    localpath : Path
        The path to download the dataset to.  If not provided,
        the dataset will be downloaded to the current working directory.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    Path
        The path to the downloaded file.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import pathlib
    >>> urlbase = 'https://demo.caterva2.net'
    >>> path = 'example/ds-2d-fields.b2nd'
    >>> cat2.subscribe('example', urlbase)
    'Ok'
    >>> cat2.download(pathlib.Path(path), urlbase=urlbase)
    PosixPath('example/ds-2d-fields.b2nd')
    """
    urlbase, dataset = _format_paths(urlbase, dataset)
    url = api_utils.get_download_url(dataset, urlbase)
    localpath = pathlib.Path(localpath) if localpath else None
    if localpath is None:
        path = '.' / dataset
    elif localpath.is_dir():
        path = localpath / dataset.name
    else:
        path = localpath
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.download_url(url, str(path),
                                  try_unpack=api_utils.blosc2_is_here,
                                  auth_cookie=auth_cookie)

def upload(localpath, dataset, urlbase=None, auth_cookie=None):
    """
    Upload a local dataset to a remote repository.

    **Note:** If `localpath` is a regular file (i.e. without a `.b2nd`,
    `.b2frame` or `.b2` extension), it will be compressed with Blosc2 on the
    server side (i.e. it will have the `.b2` extension appended internally,
    although this won't be visible when using the web, API or CLI interfaces).

    Parameters
    ----------
    localpath : Path
        The path of the local dataset.
    dataset : Path
        The remote path to upload the dataset to.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    Path
        The path of the uploaded file.
    """
    urlbase, dataset = _format_paths(urlbase, dataset)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.upload_file(localpath, dataset, urlbase, try_pack=api_utils.blosc2_is_here,
                                 auth_cookie=auth_cookie)


def remove(path, urlbase=None, auth_cookie=None):
    """
    Remove a dataset or directory path from a remote repository.

    Note that when a directory is removed, only its contents are removed.
    The directory itself is not removed. This can be handy for future
    uploads to the same directory.  This is preliminary and may change in
    future versions.

    Parameters
    ----------
    path : Path
        The path of the dataset or directory.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The removed path.

    Examples
    --------
    >>> remove('@public/ds-1d.b2nd')
    '@public/ds-1d.b2nd'
    >>> 'ds-1d.b2nd' in get_list('@public')
    False
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.post(f'{urlbase}/api/remove/{path}',
                          auth_cookie=auth_cookie)


def move(src, dst, urlbase=None, auth_cookie=None):
    """
    Move a dataset or directory to a new location.

    Parameters
    ----------
    src : Path
        The source path of the dataset or directory.
    dst : Path
        The destination path of the dataset or directory.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The new path of the dataset.

    Examples
    --------
    >>> move('@public/ds-1d.b2nd', '@public/mypath/myds.b2nd')
    '@public/mypath/myds.b2nd'
    >>> 'ds-1d.b2nd' in get_list('@public')
    False
    >>> move('@public/dir1', '@public/mypath')
    '@public/mypath/dir1'
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    result =  api_utils.post(f'{urlbase}/api/move/',
                             {'src': str(src), 'dst': str(dst)},
                             auth_cookie=auth_cookie)
    return pathlib.Path(result)


def copy(src, dst, urlbase=None, auth_cookie=None):
    """
    Copy a dataset or directory to a new location.

    Parameters
    ----------
    src : Path
        The source path of the dataset or directory.
    dst : Path
        The destination path of the dataset or directory.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        The new path of the dataset.

    Examples
    --------
    >>> copy('@public/ds-1d.b2nd', '@public/mypath/myds.b2nd')
    '@public/mypath/myds.b2nd'
    >>> copy('@public/dir1', '@public/mypath/')
    '@public/mypath/dir1'
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    result =  api_utils.post(f'{urlbase}/api/copy/',
                             {'src': str(src), 'dst': str(dst)},
                             auth_cookie=auth_cookie)
    return pathlib.Path(result)


def lazyexpr(name, expression, operands,
             urlbase=None, auth_cookie=None):
    """
    Create a lazy expression dataset in personal space.

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
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    Path
        The path of the created dataset.
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    # Convert possible Path objects in operands to strings so that they can be serialized
    operands = {k: str(v) for k, v in operands.items()}
    expr = dict(name=name, expression=expression, operands=operands)
    dataset = api_utils.post(f'{urlbase}/api/lazyexpr/', expr, auth_cookie=auth_cookie)
    return pathlib.Path(dataset)


def adduser(newuser, password=None, superuser=False, urlbase=None, auth_cookie=None):
    """
    Add a user to the subscriber.

    Parameters
    ----------
    newuser : str
        The username of the user to add.
    password : str
        The password of the user to add.
    superuser : bool
        Whether the user is a superuser or not.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        An explanatory message.
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.post(f'{urlbase}/api/adduser/',
                          {'username': newuser, 'password': password, 'superuser': superuser},
                          auth_cookie=auth_cookie)


def deluser(user, urlbase=None, auth_cookie=None):
    """
    Delete a user from the subscriber.

    Parameters
    ----------
    user : str
        The username of the user to delete.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    str
        An explanatory message.
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f'{urlbase}/api/deluser/{user}', auth_cookie=auth_cookie)


def listusers(urlbase=None, auth_cookie=None):
    """
    List the users in the subscriber.

    Parameters
    ----------
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str
        An optional HTTP cookie for authorizing access.

    Returns
    -------
    list of dict
        The list of users in the subscriber.
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f'{urlbase}/api/listusers', auth_cookie=auth_cookie)


class Root:
    """
    A root is a remote repository that can be subscribed to.

    Parameters
    ----------
    root : str
        The name of the root to subscribe to.
    urlbase : str
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    user_auth : dict
        An optional mapping of fields and values to be used as data to be
        posted for authenticating the user and get an authorization token for
        further requests.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> root = cat2.Root('example', 'https://demo.caterva2.net')
    >>> root.file_list[-3:]
    ['dir2/ds-4d.b2nd', 'dir1/ds-3d.b2nd', 'dir1/ds-2d.b2nd']
    """
    def __init__(self, name, urlbase=None, user_auth=None):
        urlbase, name = _format_paths(urlbase, name)
        self.name = name
        self.urlbase = utils.urlbase_type(urlbase)
        if user_auth:
            self.auth_cookie = api_utils.get_auth_cookie(urlbase, user_auth)
        else:
            self.auth_cookie = _subscriber_data["auth_cookie"]
        ret = api_utils.post(f'{urlbase}/api/subscribe/{name}',
                             auth_cookie=self.auth_cookie)
        if ret != 'Ok':
            roots = get_roots(urlbase)
            raise ValueError(f'Could not subscribe to root {name}'
                             f' (only {roots.keys()} available)')
    @property
    def file_list(self):
        """
        A list with the files in this root.
        """
        return api_utils.get(f'{self.urlbase}/api/list/{self.name}',
                             auth_cookie=self.auth_cookie)

    def __repr__(self):
        return f'<Root: {self.name}>'

    def __getitem__(self, path):
        """
        Get a file or dataset from the root.

        Parameters
        ----------
        path : str or Path
            The path of the file or dataset.

        Returns
        -------
        File
            A :class:`File` or :class:`Dataset` instance.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> root['ds-1d.b2nd']
        <Dataset: example/ds-1d.b2nd>
        """
        path = path.as_posix() if isinstance(path, pathlib.Path) else path
        if path.endswith((".b2nd", ".b2frame")):
            return Dataset(path, root=self.name, urlbase=self.urlbase,
                           auth_cookie=self.auth_cookie)
        else:
            return File(path, root=self.name, urlbase=self.urlbase,
                        auth_cookie=self.auth_cookie)

    def __contains__(self, path):
        """
        Check if a path exists in the root.

        Parameters
        ----------
        path : str or Path
            The path of the file or dataset.

        Returns
        -------
        bool
            Whether the path exists in the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> 'ds-1d.b2nd' in root
        True
        """
        path = path.as_posix() if isinstance(path, pathlib.Path) else path
        return path in self.file_list

    def __iter__(self):
        """
        Iterate over the files and datasets in the root.

        Returns
        -------
        iter
            An iterator over the files and datasets in the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> for file in root:
        ...     print(file)
        ds-2d-fields.b2nd
        lung-jpeg2000_10x.b2nd
        ds-1d-fields.b2nd
        ds-1d.b2nd
        ds-hello.b2frame
        ds-sc-attr.b2nd
        README.md
        ds-1d-b.b2nd
        tomo-guess-test.b2nd
        dir2/ds-4d.b2nd
        dir1/ds-3d.b2nd
        dir1/ds-2d.b2nd
        """
        return iter(self.file_list)

    def __len__(self):
        """
        Return the number of files in the root.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> len(root)
        12
        """
        return len(self.file_list)

    def __str__(self):
        return self.name

    def upload(self, localpath, dataset=None):
        """
        Upload a local dataset to this root.

        Parameters
        ----------
        localpath : Path
            The path of the local dataset.
        dataset : Path
            The remote path to upload the dataset to.  If not provided, the
            dataset will be uploaded to this root in top level.

        Returns
        -------
        File
            A :class:`File` or :class:`Dataset` instance.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('@public')
        >>> root.upload('foo/mydataset.b2nd')
        File: @public/foo/mydataset.md
        """
        if dataset is None:
            # localpath cannot be absolute in this case (too much prone to errors)
            if pathlib.Path(localpath).is_absolute():
                raise ValueError(
                    "When `dataset` is not specified, `localpath` must be a relative path")
            dataset = pathlib.Path(self.name) / localpath
        else:
            dataset = pathlib.Path(self.name) / pathlib.Path(dataset)
        uploadpath = upload(localpath, dataset, urlbase=self.urlbase,
                            auth_cookie=self.auth_cookie)
        # Remove the first component of the uploadpath (the root name) and return a new File/Dataset
        return self[str(uploadpath.relative_to(self.name))]


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
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie: str
        An optional cookie to authorize requests via HTTP.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> root = cat2.Root('example', 'https://demo.caterva2.net')
    >>> file = root['README.md']
    >>> file
    <File: example/README.md>
    >>> file.name
    'README.md'
    >>> file.urlbase
    'https://demo.caterva2.net'
    >>> file.path
    PosixPath('example/README.md')
    >>> file.meta['cbytes']
    119
    """
    def __init__(self, name, root, urlbase, auth_cookie=None):
        urlbase, name = _format_paths(urlbase, name)
        _, root = _format_paths(None, root)
        self.root = root
        self.name = name
        self.urlbase = urlbase
        self.path = pathlib.Path(f'{self.root}/{self.name}')
        self.auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
        self.meta = api_utils.get(f'{urlbase}/api/info/{self.path}',
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

        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
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
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'https://demo.caterva2.net/api/fetch/example/ds-1d.b2nd'
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
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
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

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> ds = root['ds-1d.b2nd']
        >>> ds.fetch(1)
        array(1)
        >>> ds.fetch(slice(0, 10))
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        slice_ = api_utils.slice_to_string(slice_)
        data = api_utils.fetch_data(self.path, self.urlbase,
                                    {'slice_': slice_},
                                    auth_cookie=self.auth_cookie)
        return data

    def download(self, localpath=None):
        """
        Download a file to storage.

        Parameters
        ----------
        localpath : Path
            The path to download the file to.  If not provided, the file will
            be downloaded to the current working directory.

        Returns
        -------
        Path
            The path to the downloaded file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> file = root['ds-1d.b2nd']
        >>> file.download()
        PosixPath('example/ds-1d.b2nd')
        >>> file.download('mypath')  # assuming 'mypath' exists
        PosixPath('mypath/ds-1d.b2nd')
        >>> file.download('mypath/myds.b2nd')  # 'mypath' will be created if it doesn't exist
        PosixPath('mypath/myds.b2nd')
        """
        return download(self.path, localpath=localpath,
                        urlbase=self.urlbase, auth_cookie=self.auth_cookie)

    def move(self, dst):
        """
        Move a file to a new location.

        Parameters
        ----------
        dst : Path
            The destination path of the file.

        Returns
        -------
        Path
            The new path of the file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('@public')
        >>> file = root['ds-1d.b2nd']
        >>> file.move('@public/mypath/myds.b2nd')
        PosixPath('@public/mypath/myds.b2nd')
        """
        return move(self.path, dst, urlbase=self.urlbase, auth_cookie=self.auth_cookie)

    def copy(self, dst):
        """
        Copy a file to a new location.

        Parameters
        ----------
        dst : Path
            The destination path of the file.

        Returns
        -------
        Path
            The new path of the file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('@public')
        >>> file = root['ds-1d.b2nd']
        >>> file.copy('@public/mypath/myds.b2nd')
        PosixPath('@public/mypath/myds.b2nd')
        """
        return copy(self.path, dst, urlbase=self.urlbase, auth_cookie=self.auth_cookie)

    def remove(self):
        """
        Remove a file from a remote repository.

        Returns
        -------
        str
            The path of the removed file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('@public')
        >>> file = root['ds-1d.b2nd']
        >>> file.remove()
        '@public/ds-1d.b2nd'
        """
        return remove(self.path, urlbase=self.urlbase,  auth_cookie=self.auth_cookie)


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
        The base of URLs of the subscriber to query. Default is
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie: str
        An optional cookie to authorize requests via HTTP.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> root = cat2.Root('example', 'https://demo.caterva2.net')
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


@contextmanager
def c2context(
    *,
    urlbase: (str | None) = None,
    username: (str | None) = None,
    password: (str | None) = None,
    auth_cookie: (str | None) = None,
) -> None:
    """
    Context manager that sets parameters in Caterva2 subscriber requests.

    A parameter not specified or set to ``None`` inherits the value set by the
    previous context manager,
    defaulting the subscriber url to :py:obj:`caterva2.sub_urlbase_default`
    and the authentication cookie to `None`.
    Parameters set to the empty string
    are not to be used in requests (with no default either).

    If the subscriber requires authorization for requests, you may either
    provide `auth_cookie` (which you should have obtained previously from the
    subscriber), or both `username` and `password` to get that cookie by first
    logging in to the subscriber.  The cookie will be reused until explicitly
    reset or requested again in a latter context manager invocation.

    Please note that this manager is reentrant but not concurrency-safe.

    Parameters
    ----------
    urlbase : str | None
        A URL base that will be used as default for all operations inside the
        context manager.
    username : str | None
        A name to be used in credentials to login to the subscriber and get an
        authorization token from it.
    password : str | None
        A secret to be used in credentials to login to the subscriber and get
        an authorization cookie from it.
    auth_cookie : str | None
        A token that will be used as default for all operations inside the
        context manager.

    Yields
    ------
    out: None

    Examples
    --------
    >>> import caterva2 as cat2
    >>> with cat2.c2context(urlbase='https://demo.caterva2.net'):
    ...     print(cat2.get_roots()['h5lung_j2k'])
    ...
    {'name': 'h5lung_j2k', 'http': 'localhost:8012', 'subscribed': None}
    """
    global _subscriber_data

    # Perform login to get an authorization token.
    if username or password:
        if auth_cookie:
            raise ValueError("Either provide a username/password or an authorizaton token")
        auth_cookie = api_utils.get_auth_cookie(urlbase,
                                               {'username': username, 'password': password})

    try:
        old_sub_data = _subscriber_data
        new_sub_data = old_sub_data.copy()  # inherit old values
        if urlbase is not None:
            new_sub_data["urlbase"] = urlbase
        if auth_cookie is not None:
            new_sub_data["auth_cookie"] = auth_cookie
        _subscriber_data = new_sub_data
        yield
    finally:
        _subscriber_data = old_sub_data
