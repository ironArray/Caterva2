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

import contextlib
import functools
import pathlib
import sys

from caterva2 import api_utils, utils

# Defaults
bro_host_default = "localhost:8000"
"""The default HTTP endpoint for the broker (URL host & port)."""

pub_host_default = "localhost:8001"
"""The default HTTP endpoint for the publisher (URL host & port)."""

sub_host_default = "localhost:8002"
"""The default HTTP endpoint for the subscriber (URL host & port)."""

sub_urlbase_default = f"http://{sub_host_default}"
"""The default base of URLs provided by the subscriber."""

_subscriber_data = {
    "urlbase": sub_urlbase_default,
    "auth_cookie": "",
}
"""Caterva2 subscriber data saved by context manager."""


def _format_paths(urlbase, path=None):
    if urlbase is None:
        if sys.platform == "emscripten":
            urlbase = ""
        else:
            urlbase = _subscriber_data["urlbase"]

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
    Retrieves the list of available roots.

    Parameters
    ----------

    urlbase : str, optional
        Base URL of the subscriber to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie used for authorization.

    Returns
    -------
    dict
        Dictionary mapping available root names to their details:
        - ``name``: the root name
        - ``http``: the HTTP endpoint
        - ``subscribed``: whether it is subscribed or not.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> roots_dict = cat2.get_roots("https://demo.caterva2.net")
    >>> sorted(roots_dict.keys())
    ['@public', 'b2tests', 'example', 'h5example', 'h5lung_j2k', 'h5numbers_j2k']
    >>> cat2.subscribe('b2tests', "https://demo.caterva2.net")
    'Ok'
    >>> roots_dict['b2tests']
    {'name': 'b2tests', 'http': 'localhost:8014', 'subscribed': True}
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f"{urlbase}/api/roots", auth_cookie=auth_cookie)


def subscribe(root, urlbase=None, auth_cookie=None):
    """
    Subscribes to a specified root.

    Parameters
    ----------
    root : str
        Name of the root to subscribe to.
    urlbase : str, optional
        Base URL of the subscriber to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie used for authorization.

    Returns
    -------
    str
        Server response as a string.

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
    return api_utils.post(f"{urlbase}/api/subscribe/{root}", auth_cookie=auth_cookie)


def get_list(path, urlbase=None, auth_cookie=None):
    """
    Lists datasets in a specified path.

    Parameters
    ----------
    path : str
        Path to a root, directory or dataset.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization.

    Returns
    -------
    list
        List of dataset names as strings, relative to the specified path.

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
    return api_utils.get(f"{urlbase}/api/list/{path}", auth_cookie=auth_cookie)


def get_info(path, urlbase=None, auth_cookie=None):
    """
    Retrieves information about a specified dataset.

    Parameters
    ----------
    path : str
        Path to the dataset.
    urlbase : str, optional
        Base URL of to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization.

    Returns
    -------
    dict
        Dictionary of dataset properties, mapping property names to their values.

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
    return api_utils.get(f"{urlbase}/api/info/{path}", auth_cookie=auth_cookie)


def fetch(path, urlbase=None, slice_=None, auth_cookie=None):
    """
    Retrieves a specified slice (or the entire content) of a dataset.

    Parameters
    ----------
    path : str
        Path to the dataset.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    slice_ : str, optional
        Slice of the dataset to retrieve. Fetches the entire dataset if
        not provided.
    auth_cookie : str, optional
        HTTP cookie for authorization.

    Returns
    -------
    numpy.ndarray
        The requested slice of the dataset as a Numpy array.

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
    return api_utils.fetch_data(path, urlbase, {"slice_": slice_}, auth_cookie=auth_cookie)


def get_chunk(path, nchunk, urlbase=None, auth_cookie=None):
    """
    Retrieves a specified compressed chunk from a dataset.

    Parameters
    ----------
    path : str
        Path of the dataset.
    nchunk : int
        ID of the unidimensional chunk to retrieve.
    urlbase : str, optional
        Base URL to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization.

    Returns
    -------
    bytes obj
        The compressed chunk data.

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
    data = api_utils._xget(f"{urlbase}/api/chunk/{path}", {"nchunk": nchunk}, auth_cookie=auth_cookie)
    return data.content


def download(dataset, localpath=None, urlbase=None, auth_cookie=None):
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
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization.

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
        path = "." / dataset
    elif localpath.is_dir():
        path = localpath / dataset.name
    else:
        path = localpath
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.download_url(
        url, str(path), try_unpack=api_utils.blosc2_is_here, auth_cookie=auth_cookie
    )


def upload(localpath, dataset, urlbase=None, auth_cookie=None):
    """
    Uploads a local dataset to a remote repository.

    **Note:** If `localpath` is a regular file without a `.b2nd`,
    `.b2frame` or `.b2` extension, it will be automatically compressed
    with Blosc2 on the server, adding a `.b2` extension internally.

    Parameters
    ----------
    localpath : Path
        Path to the local dataset.
    dataset : Path
        Remote path to upload the dataset to.
    urlbase : str, optional
        Base URL to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization.
        Must be provided unless already set in
        a :py_obj:`caterva2.c2context`.

    Returns
    -------
    Path
        Path of the uploaded file on ther server.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To upload a file you need to be authenticated as an already registered used
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> newpath = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
    >>> uploaded_path = cat2.upload('root-example/dir2/ds-4d.b2nd', newpath, urlbase, auth_cookie)
    >>> str(uploaded_path) == newpath
    True
    """
    urlbase, dataset = _format_paths(urlbase, dataset)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.upload_file(
        localpath,
        dataset,
        urlbase,
        try_pack=api_utils.blosc2_is_here,
        auth_cookie=auth_cookie,
    )


def remove(path, urlbase=None, auth_cookie=None):
    """
    Removes a dataset or the contents of a directory from a remote repository.

    **Note:** When a directory is removed, only its contents are deleted;
    the directory itself remains. This behavior allows for future
    uploads to the same directory. It is subject to in future versions.

    Parameters
    ----------
    path : Path
        Path of the dataset or directory to remove.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`.

    Returns
    -------
    str
        The path that was removed.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To remove a file you need to be a registered used
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
    >>> uploaded_path = cat2.upload('root-example/dir2/ds-4d.b2nd', path, urlbase, auth_cookie)
    >>> removed_path = cat2.remove(path, urlbase, auth_cookie)
    >>> removed_path == path
    True
    """
    urlbase, path = _format_paths(urlbase, path)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.post(f"{urlbase}/api/remove/{path}", auth_cookie=auth_cookie)


def move(src, dst, urlbase=None, auth_cookie=None):
    """
    Moves a dataset or directory to a new location.

    Parameters
    ----------
    src : Path
        Source path of the dataset or directory.
    dst : Path
        The destination path for the dataset or directory.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`.

    Returns
    -------
    str
        New path of the moved dataset or directory.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To move a file you need to be a registered used
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
    >>> uploaded_path = cat2.upload('root-example/dir2/ds-4d.b2nd', path, urlbase, auth_cookie)
    >>> newpath = f'@personal/dir{np.random.randint(0, 100)}/ds-4d-moved.b2nd'
    >>> moved_path = cat2.move(path, newpath, urlbase, auth_cookie)
    >>> str(moved_path) == newpath
    True
    >>> path.replace('@personal/', '') in cat2.get_list('@personal', urlbase, auth_cookie)
    False
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    result = api_utils.post(
        f"{urlbase}/api/move/",
        {"src": str(src), "dst": str(dst)},
        auth_cookie=auth_cookie,
    )
    return pathlib.Path(result)


def copy(src, dst, urlbase=None, auth_cookie=None):
    """
    Copies a dataset or directory to a new location.

    Parameters
    ----------
    src : Path
        Source path of the dataset or directory.
    dst : Path
        Destination path for the dataset or directory.
    urlbase : str, optional
        Base URL to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`.

    Returns
    -------
    str
        New path of the copied dataset or directory.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To copy a file you need to be a registered used
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> src_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
    >>> uploaded_path = cat2.upload('root-example/dir2/ds-4d.b2nd', src_path, urlbase, auth_cookie)
    >>> copy_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d-copy.b2nd'
    >>> copied_path = cat2.copy(src_path, copy_path, urlbase, auth_cookie)
    >>> str(copied_path) == copy_path
    True
    >>> datasets = cat2.get_list('@personal', urlbase, auth_cookie)
    >>> src_path.replace('@personal/', '') in datasets
    True
    >>> copy_path.replace('@personal/', '') in datasets
    True
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    result = api_utils.post(
        f"{urlbase}/api/copy/", {"src": str(src), "dst": str(dst)}, auth_cookie=auth_cookie
    )
    return pathlib.Path(result)


def lazyexpr(name, expression, operands, urlbase=None, auth_cookie=None):
    """
    Creates a lazy expression dataset in personal space.

    A dataset with the specified name will be created or overwritten if already
    exists.

    Parameters
    ----------
    name : str
        Name of the dataset to be created (without extension).
    expression : str
        Expression to be evaluated, which must yield a lazy expression.
    operands : dict
        Mapping of variables in the expression to their corresponding dataset paths.
    urlbase : str, optional
        Base URL to query. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`.

    Returns
    -------
    Path
        Path of the created dataset.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To create a lazyexpr you need to be a registered used
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
    >>> src_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
    >>> path = cat2.upload('root-example/dir1/ds-2d.b2nd', src_path, urlbase, auth_cookie)
    >>> cat2.lazyexpr('example-expr', 'a + a', {'a': path}, urlbase, auth_cookie)
    PosixPath('@personal/example-expr.b2nd')
    >>> 'example-expr.b2nd' in cat2.get_list('@personal', urlbase, auth_cookie)
    True
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    # Convert possible Path objects in operands to strings so that they can be serialized
    operands = {k: str(v) for k, v in operands.items()}
    expr = {"name": name, "expression": expression, "operands": operands}
    dataset = api_utils.post(f"{urlbase}/api/lazyexpr/", expr, auth_cookie=auth_cookie)
    return pathlib.Path(dataset)


def adduser(newuser, password=None, superuser=False, urlbase=None, auth_cookie=None):
    """
    Adds a user to the subscriber.

    Parameters
    ----------
    newuser : str
        Username of the user to add.
    password : str, optional
        Password for the user to add.
    superuser : bool, optional
        Indicates if the user is a superuser.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`. The auth_cookie used must be
        from a superuser.

    Returns
    -------
    str
        An explanatory message about the operation's success or failure.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To add a user you need to be a superuser
    >>> # This example is intended to work when the subscriber is running locally
    >>> super_user = {'username': 'superuser@example.com', 'password': 'foo'}
    >>> super_auth = cat2.get_auth_cookie(cat2.sub_urlbase_default, user_auth=super_user)
    >>> username = f'user{np.random.randint(0, 100)}@example.com'
    >>> message = cat2.adduser(username, 'foo', auth_cookie=super_auth)
    >>> f"User added: username='{username}' password='foo' superuser=False" == message
    True
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.post(
        f"{urlbase}/api/adduser/",
        {"username": newuser, "password": password, "superuser": superuser},
        auth_cookie=auth_cookie,
    )


def deluser(user, urlbase=None, auth_cookie=None):
    """
    Deletes a user from the subscriber.

    Parameters
    ----------
    username : str
        Username of the user to delete.
    urlbase : str
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`. The auth_cookie must be
        from a superuser.

    Returns
    -------
    str
        An explanatory message about the operation's success or failure.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To delete a user you need to be a superuser
    >>> # This example is intended to work when the subscriber is running locally
    >>> super_user = {'username': 'superuser@example.com', 'password': 'foo'}
    >>> super_auth = cat2.get_auth_cookie(cat2.sub_urlbase_default, user_auth=super_user)
    >>> username = f'user{np.random.randint(0, 100)}@example.com'
    >>> _ = cat2.adduser(username, 'foo', auth_cookie=super_auth)
    >>> message = cat2.deluser(username, auth_cookie=super_auth)
    >>> message == f"User deleted: {username}"
    True
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    return api_utils.get(f"{urlbase}/api/deluser/{user}", auth_cookie=auth_cookie)


def listusers(username=None, urlbase=None, auth_cookie=None):
    """
    Lists the users in the subscriber.

    Parameters
    ----------
    username : str, optional
        Username of the specific user to list.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie : str, optional
        HTTP cookie for authorization. Must be provided unless specified in
        a :py_obj:`caterva2.c2context`. The auth_cookie must be
        from a superuser.

    Returns
    -------
    list of dict
        A list of user dictionaries in the subscriber.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> import numpy as np
    >>> # To list the users you need to be a superuser
    >>> # This example is intended to work when the subscriber is running locally
    >>> super_user = {'username': 'superuser@example.com', 'password': 'foo'}
    >>> super_auth = cat2.get_auth_cookie(cat2.sub_urlbase_default, user_auth=super_user)
    >>> users = cat2.listusers(auth_cookie=super_auth)
    >>> sorted(users[0].keys())
    ['email', 'hashed_password', 'id', 'is_active', 'is_superuser', 'is_verified']
    >>> username = f'user{np.random.randint(0, 100)}@example.com'
    >>> _ = cat2.adduser(username, 'foo', auth_cookie=super_auth)
    >>> updated_users = cat2.listusers(auth_cookie=super_auth)
    >>> len(users) + 1 == len(updated_users)
    True
    >>> user_info = cat2.listusers(username, auth_cookie=super_auth)
    >>> user_info[0]['is_superuser']
    False
    >>> superuser_info = cat2.listusers('superuser@example.com', auth_cookie=super_auth)
    >>> superuser_info[0]['is_superuser']
    True
    """
    urlbase, _ = _format_paths(urlbase)
    auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
    url = f"{urlbase}/api/listusers/" + (f"?username={username}" if username else "")
    return api_utils.get(url, auth_cookie=auth_cookie)


class Root:
    """
    Represents a remote repository that can be subscribed to.

    Parameters
    ----------
    root : str
        Name of the root to subscribe to.
    urlbase : str, optional
        Base URL to query. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    user_auth : dict, optional
        Mapping of fields and values for user authentication. This is
        used to post data for obtaining an authorization token for further
        requests. For some actions, this must be provided unless already
        specified in a :py_obj:`caterva2.c2context`.

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
        ret = api_utils.post(f"{urlbase}/api/subscribe/{name}", auth_cookie=self.auth_cookie)
        if ret != "Ok":
            roots = get_roots(urlbase)
            raise ValueError(f"Could not subscribe to root {name}" f" (only {roots.keys()} available)")

    @property
    def file_list(self):
        """
        Retrieves a list of files in this root.
        """
        return api_utils.get(f"{self.urlbase}/api/list/{self.name}", auth_cookie=self.auth_cookie)

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
        File
            An instance of :class:`File` or :class:`Dataset`.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> root['ds-1d.b2nd']
        <Dataset: example/ds-1d.b2nd>
        """
        path = path.as_posix() if isinstance(path, pathlib.Path) else path
        if path.endswith((".b2nd", ".b2frame")):
            return Dataset(path, root=self.name, urlbase=self.urlbase, auth_cookie=self.auth_cookie)
        else:
            return File(path, root=self.name, urlbase=self.urlbase, auth_cookie=self.auth_cookie)

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
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> 'ds-1d.b2nd' in root
        True
        """
        path = path.as_posix() if isinstance(path, pathlib.Path) else path
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
        Returns the number of files in the root.

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
        Uploads a local dataset to this root.

        Parameters
        ----------
        localpath : Path
            Path of the local dataset to upload.
        dataset : Path, optional
            Remote path where the dataset will be uploaded.  If not provided, the
            dataset will be uploaded to the top level of this root.

        Returns
        -------
        File
            A instance of :class:`File` or :class:`Dataset`.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To upload a file you must be registered as a user.
        >>> urlbase = 'https://cat2.cloud/demo'
        >>> root = cat2.Root('@personal', urlbase, dict(username='user@example.com', password='foo'))
        >>> path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        True
        """
        if dataset is None:
            # localpath cannot be absolute in this case (too much prone to errors)
            if pathlib.Path(localpath).is_absolute():
                raise ValueError("When `dataset` is not specified, `localpath` must be a relative path")
            dataset = pathlib.Path(self.name) / localpath
        else:
            dataset = pathlib.Path(self.name) / pathlib.Path(dataset)
        uploadpath = upload(localpath, dataset, urlbase=self.urlbase, auth_cookie=self.auth_cookie)
        # Remove the first component of the upload path (the root name) and return a new File/Dataset
        return self[str(uploadpath.relative_to(self.name))]


class File:
    """
    Represents a file, which can be a Blosc2 dataset or a regular file on a root repository.

    This class is not intended for direct instantiation; it should be accessed through a
    :class:`Root` instance.

    Parameters
    ----------
    name : str
        The name of the file.
    root : str
        The name of the root repository.
    urlbase : str, optional
        Base URL for querying the subscriber. Defaults to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie: str, optional
        An optional cookie for HTTP request authorization.
        This must be specified for certain actions unless
        already provided in a :py_obj:`caterva2.c2context`.

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
    >>> file.meta['contiguous']
    True
    """

    def __init__(self, name, root, urlbase, auth_cookie=None):
        urlbase, name = _format_paths(urlbase, name)
        _, root = _format_paths(None, root)
        self.root = root
        self.name = name
        self.urlbase = urlbase
        self.path = pathlib.Path(f"{self.root}/{self.name}")
        self.auth_cookie = auth_cookie or _subscriber_data["auth_cookie"]
        self.meta = api_utils.get(f"{urlbase}/api/info/{self.path}", auth_cookie=self.auth_cookie)
        # TODO: 'cparams' is not always present (e.g. for .b2nd files)
        # print(f"self.meta: {self.meta['cparams']}")

    def __repr__(self):
        return f"<File: {self.path}>"

    @functools.cached_property
    def vlmeta(self):
        """
        Returns a mapping of metalayer names to their respective values.

        This is used to access variable-length metalayers (user attributes)
        associated with the file.

        >>> import caterva2 as cat2
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
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
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'https://demo.caterva2.net/api/fetch/example/ds-1d.b2nd'
        """
        return api_utils.get_download_url(self.path, self.urlbase)

    def __getitem__(self, slice_):
        """
        Retrieves a slice of the dataset.

        Parameters
        ----------
        slice_ : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset.

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
        return self.fetch(slice_=slice_)

    def fetch(self, slice_=None):
        """
        Fetches a slice of the dataset.

        This method is equivalent to `__getitem__()`.

        Parameters
        ----------
        slice_ : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset.

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
        return api_utils.fetch_data(
            self.path, self.urlbase, {"slice_": slice_}, auth_cookie=self.auth_cookie
        )

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
        >>> root = cat2.Root('example', 'https://demo.caterva2.net')
        >>> file = root['ds-1d.b2nd']
        >>> file.download()
        PosixPath('example/ds-1d.b2nd')
        >>> file.download('mydir/myarray.b2nd')
        PosixPath('mydir/myarray.b2nd')
        """
        return download(
            self.path,
            localpath=localpath,
            urlbase=self.urlbase,
            auth_cookie=self.auth_cookie,
        )

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
        >>> urlbase = 'https://cat2.cloud/demo'
        >>> root = cat2.Root('@personal', urlbase, dict(username='user@example.com', password='foo'))
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> file = root['root-example/dir2/ds-4d.b2nd']
        >>> file.move('@personal/root-example/dir1/ds-4d-moved.b2nd')
        PosixPath('@personal/root-example/dir1/ds-4d-moved.b2nd')
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        False
        >>> 'root-example/dir1/ds-4d-moved.b2nd' in root
        True
        """
        return move(self.path, dst, urlbase=self.urlbase, auth_cookie=self.auth_cookie)

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
        >>> urlbase = 'https://cat2.cloud/demo'
        >>> root = cat2.Root('@personal', urlbase, dict(username='user@example.com', password='foo'))
        >>> root.upload('root-example/dir2/ds-4d.b2nd')
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
        >>> file = cat2.File('root-example/dir2/ds-4d.b2nd', '@personal', urlbase, auth_cookie)
        >>> file.copy('@personal/root-example/dir2/ds-4d-copy.b2nd')
        PosixPath('@personal/root-example/dir2/ds-4d-copy.b2nd')
        >>> 'root-example/dir2/ds-4d.b2nd' in root
        True
        >>> 'root-example/dir2/ds-4d-copy.b2nd' in root
        True
        """
        return copy(self.path, dst, urlbase=self.urlbase, auth_cookie=self.auth_cookie)

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
        >>> urlbase = 'https://cat2.cloud/demo'
        >>> root = cat2.Root('@personal', urlbase, dict(username='user@example.com', password='foo'))
        >>> path = 'root-example/dir2/ds-4d.b2nd'
        >>> root.upload(path)
        <Dataset: @personal/root-example/dir2/ds-4d.b2nd>
        >>> auth_cookie = cat2.get_auth_cookie(urlbase, dict(username='user@example.com', password='foo'))
        >>> file = cat2.File(path, '@personal', urlbase, auth_cookie)
        >>> file.remove()
        '@personal/root-example/dir2/ds-4d.b2nd'
        >>> path in root
        False
        """
        return remove(self.path, urlbase=self.urlbase, auth_cookie=self.auth_cookie)


class Dataset(File):
    """
    Represents a dataset as a Blosc2 container within a file.

    This class is not intended to be instantiated directly; it should be accessed through a
    :class:`Root` instance.

    Parameters
    ----------
    name : str
        The name of the dataset.
    root : str
        The name of the root repository.
    urlbase : str, optional
        The base URL for the subscriber queries. Default to
        :py:obj:`caterva2.sub_urlbase_default`.
    auth_cookie: str, optional
        A cookie for authorizing HTTP requests.

    Examples
    --------
    >>> import caterva2 as cat2
    >>> ds = cat2.Dataset('ds-1d.b2nd', 'example', 'https://demo.caterva2.net')
    >>> ds.name
    'ds-1d.b2nd'
    >>> ds[1:10]
    array([1, 2, 3, 4, 5, 6, 7, 8, 9])
    """

    def __init__(self, name, root, urlbase, auth_cookie=None):
        super().__init__(name, root, urlbase, auth_cookie)

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f"<Dataset: {self.path}>"


@contextlib.contextmanager
def c2context(
    *,
    urlbase: (str | None) = None,
    username: (str | None) = None,
    password: (str | None) = None,
    auth_cookie: (str | None) = None,
) -> None:
    """
    Context manager for setting parameters in Caterva2 subscriber requests.

    Parameters not specified or set to ``None`` will inherit values from the
    previous context manager, defaulting to :py:obj:`caterva2.sub_urlbase_default`
    for the URL and `None` for the authentication cookie. Parameters set to an empty string
    will not be used in requests.

    To authorize requests, provide either an existing `auth_cookie` or both `username`
    and `password` to log in and obtain the cookie. The cookie will persist until explicitly
    reset or reobtained in a subsequent context manager invocation.

    Note that this manager is reentrant but not safe for concurrent use.

    Parameters
    ----------
    urlbase : str | None
        The base URL to use for all operations within the context.
    username : str | None
        The username for logging in to obtain an authorization token.
    password : str | None
        The password for logging in to obtain an authorization cookie.
    auth_cookie : str | None
        The cookie used as the default for operations within the context.

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
    >>> urlbase = 'https://cat2.cloud/demo'
    >>> with cat2.c2context(urlbase=urlbase, username='user@example.com', password='foo'):
    ...     print(cat2.upload('root-example/ds-2d-fields.b2nd', '@personal/fields.b2nd'))
    ...
    @personal/fields.b2nd
    """
    global _subscriber_data

    # Perform login to get an authorization token.
    if username or password:
        if auth_cookie:
            raise ValueError("Either provide a username/password or an authorizaton token")
        auth_cookie = api_utils.get_auth_cookie(urlbase, {"username": username, "password": password})

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
