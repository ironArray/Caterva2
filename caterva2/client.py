import functools
import io
import pathlib
import sys
from collections.abc import Sequence

import blosc2
import numpy as np
from blosc2 import NDArray, SChunk

from . import api_utils, utils

sub_urlbase_default = "http://localhost:8002"
"""The default base of URLs provided by the subscriber."""


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


class Root:
    def __init__(self, client, name):
        """
        Represents a remote repository that can be subscribed to.

        Parameters
        ----------
        client : Client
            The client used to interact with the remote repository.
        name : str
            Name of the root to subscribe to.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client("https://demo.caterva2.net")
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
        return api_utils.get(f"{self.urlbase}/api/list/{self.name}", auth_cookie=self.cookie)

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
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root = client.get('example')
        >>> len(root)
        12
        """
        return len(self.file_list)

    def __str__(self):
        return self.name

    def upload(self, localpath, remotepath=None):
        """
        Uploads a local file to this root.

        Parameters
        ----------
        localpath : Path
            Path of the local file to upload.
        remotepath : Path, optional
            Remote path where the file will be uploaded.  If not provided, the
            file will be uploaded to the top level of this root.

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
            if pathlib.PurePosixPath(localpath).is_absolute():
                raise ValueError("When `dataset` is not specified, `localpath` must be a relative path")
            remotepath = pathlib.PurePosixPath(self.name) / localpath
        else:
            remotepath = pathlib.PurePosixPath(self.name) / pathlib.PurePosixPath(remotepath)
        uploadpath = self.client.upload(localpath, remotepath)
        # Remove the first component of the upload path (the root name) and return a new File/Dataset
        return self[str(uploadpath.relative_to(self.name))]


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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root = client.get('example')
        >>> file = root['README.md']
        >>> file
        <File: example/README.md>
        >>> file.name
        'README.md'
        >>> file.urlbase
        'https://demo.caterva2.net'
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
        self.meta = api_utils.get(f"{self.urlbase}/api/info/{self.path}", auth_cookie=self.cookie)
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
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root = client.get('example')
        >>> file = root['ds-1d.b2nd']
        >>> file.get_download_url()
        'https://demo.caterva2.net/api/fetch/example/ds-1d.b2nd'
        """
        return api_utils.get_download_url(self.path, self.urlbase)

    def __getitem__(self, key):
        """
        Retrieves a slice of the dataset.

        Parameters
        ----------
        key : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root = client.get('example')
        >>> ds = root['ds-1d.b2nd']
        >>> ds[1]
        array(1)
        >>> ds[:1]
        array([0])
        >>> ds[0:10]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        return self.slice(key, as_blosc2=False)

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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root = client.get('example')
        >>> ds = root['ds-1d.b2nd']
        >>> ds.slice(1)
        <blosc2.ndarray.NDArray object at 0x10747efd0>
        >>> ds.slice(1)[()]
        array(1)
        >>> ds.slice(slice(0, 10))[:]
        array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        """
        # Convert slices to strings
        slice_ = api_utils.slice_to_string(key)
        # Fetch and return the data as a Blosc2 object / NumPy array
        return api_utils.fetch_data(
            self.path, self.urlbase, {"slice_": slice_}, auth_cookie=self.cookie, as_blosc2=as_blosc2
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
        >>> client = cat2.Client('https://demo.caterva2.net')
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


class Dataset(File):
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
        >>> client = cat2.Client('https://demo.caterva2.net')
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

    def __repr__(self):
        # TODO: add more info about dims, types, etc.
        return f"<Dataset: {self.path}>"

    @property
    def dtype(self):
        try:
            return self.meta["dtype"]
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'dtype'.") from e

    @property
    def shape(self):
        try:
            return tuple(self.meta["shape"])
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'shape'.") from e

    @property
    def chunks(self):
        try:
            return tuple(self.meta["chunks"])
        except KeyError as e:
            raise AttributeError("'Dataset' object has no attribute 'chunks'.") from e

    @property
    def blocks(self):
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
        tuple
            The new shape of the dataset.

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
    def __init__(self, urlbase, auth=None):
        """
        Creates a client for server in urlbase.

        Parameters
        ----------
        urlbase : str, optional
            Base URL of the subscriber to query. Default to
            :py:obj:`caterva2.sub_urlbase_default`.
        auth : tuple, BasicAuth, optional

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client("https://cat2.cloud/demo")
        >>> auth_client = cat2.Client("https://cat2.cloud/demo", ("joedoe@example.com", "foobar"))
        """
        self.urlbase = utils.urlbase_type(urlbase)
        self.cookie = None
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
                self.cookie = api_utils.get_auth_cookie(
                    self.urlbase, {"username": username, "password": password}
                )

    def get_roots(self):
        """
        Retrieves the list of available roots.

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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> roots_dict = client.get_roots()
        >>> sorted(roots_dict.keys())
        ['@public', 'b2tests', 'example', 'h5example', 'h5lung_j2k', 'h5numbers_j2k']
        >>> client.subscribe('b2tests')
        'Ok'
        >>> roots_dict['b2tests']
        {'name': 'b2tests', 'http': 'localhost:8014', 'subscribed': True}
        """
        urlbase, _ = _format_paths(self.urlbase)
        return api_utils.get(f"{self.urlbase}/api/roots", auth_cookie=self.cookie)

    def _get_root(self, name):
        """
        Retrieves a specified root name.

        Parameters
        ----------
        name : str
            Name of the root to retrieve.

        Returns
        -------
        Root
            An instance of :class:`Root`.

        """
        if "/" in name:
            raise ValueError("Root names cannot contain slashes")
        # It is a root, subscribe to it
        ret = self.subscribe(name)
        if ret != "Ok":
            roots = self.get_roots()
            raise ValueError(f"Could not subscribe to root {name} (only {roots.keys()} available)")
        return Root(self, name)

    def get(self, path):
        """
        Returns an object for the given path.

        Parameters
        ----------
        path : Path
            Path to the root, file or dataset.

        Returns
        -------
        Object : Root, File, Dataset
            Object representing the root, file or dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        # Normalize the path to a POSIX path
        path = pathlib.PurePosixPath(path).as_posix()
        # Check if the path is a root or a file/dataset
        if "/" not in path:
            return self._get_root(path)
        # If not a root, assume it's a file/dataset
        root_name = path.split("/")[0]
        root = self._get_root(root_name)
        file_path = path[len(root_name) + 1 :]
        return root[file_path]

    def subscribe(self, root):
        """
        Subscribes to a specified root.

        Parameters
        ----------
        root : str
            Name of the root to subscribe to.

        Returns
        -------
        str
            Server response as a string.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> root_name = 'h5numbers_j2k'
        >>> client.subscribe(root_name)
        'Ok'
        >>> client.get_roots()[root_name]
        {'name': 'h5numbers_j2k', 'http': 'localhost:8011', 'subscribed': True}
        """
        urlbase, root = _format_paths(self.urlbase, root)
        return api_utils.post(f"{self.urlbase}/api/subscribe/{root}", auth_cookie=self.cookie)

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
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> client.subscribe('example')
        'Ok'
        >>> client.get_list('example')[:3]
        ['README.md', 'dir1/ds-2d.b2nd', 'dir1/ds-3d.b2nd']
        """
        urlbase, path = _format_paths(self.urlbase, path)
        return api_utils.get(f"{self.urlbase}/api/list/{path}", auth_cookie=self.cookie)

    def get_info(self, path):
        """
        Retrieves information about a specified dataset.

        Parameters
        ----------
        path : str
            Path to the dataset.

        Returns
        -------
        dict
            Dictionary of dataset properties, mapping property names to their values.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> client.subscribe('example')
        'Ok'
        >>> path = 'example/ds-2d-fields.b2nd'
        >>> info = client.get_info(path)
        >>> info.keys()
        dict_keys(['shape', 'chunks', 'blocks', 'dtype', 'schunk', 'mtime'])
        >>> info['shape']
        [100, 200]
        """
        urlbase, path = _format_paths(self.urlbase, path)
        return api_utils.get(f"{self.urlbase}/api/info/{path}", auth_cookie=self.cookie)

    def fetch(self, path, slice_=None):
        """
        Retrieves the entire content (or a specified slice) of a dataset.

        Parameters
        ----------
        path : str
            Path to the dataset.
        urlbase : str, optional
            Base URL to query. Defaults to
            :py:obj:`caterva2.sub_urlbase_default`.
        slice_ : int, slice, tuple of ints and slices, or None
            Specifies the slice to fetch. If None, the whole dataset is fetched.

        Returns
        -------
        numpy.ndarray
            The requested slice of the dataset as a Numpy array.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> client.fetch('example/ds-2d-fields.b2nd', (slice(0, 2), slice(0, 2))
        array([[(0.0000000e+00, 1.       ), (5.0002502e-05, 1.00005  )],
               [(1.0000500e-02, 1.0100005), (1.0050503e-02, 1.0100505)]],
              dtype=[('a', '<f4'), ('b', '<f8')])
        """
        urlbase, path = _format_paths(self.urlbase, path)
        slice_ = api_utils.slice_to_string(slice_)  # convert to string
        return api_utils.fetch_data(path, urlbase, {"slice_": slice_}, auth_cookie=self.cookie)

    def get_chunk(self, path, nchunk):
        """
        Retrieves a specified compressed chunk from a file.

        Parameters
        ----------
        path : str
            Path of the dataset.
        nchunk : int
            ID of the unidimensional chunk to retrieve.

        Returns
        -------
        bytes obj
            The compressed chunk data.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> client = cat2.Client('https://demo.caterva2.net')
        >>> client.subscribe('example')
        'Ok'
        >>> info_schunk = client.get_info('example/ds-2d-fields.b2nd')['schunk']
        >>> info_schunk['nchunks']
        1
        >>> info_schunk['cratio']
        6.453000645300064
        >>> chunk = client.get_chunk('example/ds-2d-fields.b2nd', 0)
        >>> info_schunk['chunksize'] / len(chunk)
        6.453000645300064
        """
        urlbase, path = _format_paths(self.urlbase, path)
        data = api_utils._xget(
            f"{self.urlbase}/api/chunk/{path}", {"nchunk": nchunk}, auth_cookie=self.cookie
        )
        return data.content

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
            The path to the downloaded file.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> path = 'example/ds-2d-fields.b2nd'
        >>> client = cat2.Client('https://demo.caterva2.net')
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
        return api_utils.download_url(
            url, str(path), try_unpack=api_utils.blosc2_is_here, auth_cookie=self.cookie
        )

    def upload(self, localpath, dataset):
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

        Returns
        -------
        Path
            Path of the uploaded file on the server.

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
        urlbase, dataset = _format_paths(self.urlbase, dataset)
        return api_utils.upload_file(
            localpath,
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
        tuple
            The new shape of the dataset.

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

        client, url = api_utils.get_client_and_url(None, f"{self.urlbase}/api/append/{remotepath}")
        headers = {"Cookie": self.cookie}
        response = client.post(url, files={"file": file}, headers=headers)
        response.raise_for_status()
        return tuple(response.json())

    def remove(self, path):
        """
        Removes a dataset or the contents of a directory from a remote repository.

        **Note:** When a directory is removed, only its contents are deleted;
        the directory itself remains. This behavior allows for future
        uploads to the same directory. It is subject to in future versions.

        Parameters
        ----------
        path : Path
            Path of the dataset or directory to remove.

        Returns
        -------
        str
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
        urlbase, path = _format_paths(self.urlbase, path)
        return api_utils.post(f"{self.urlbase}/api/remove/{path}", auth_cookie=self.cookie)

    def move(self, src, dst):
        """
        Moves a dataset or directory to a new location.

        Parameters
        ----------
        src : Path
            Source path of the dataset or directory.
        dst : Path
            The destination path for the dataset or directory.

        Returns
        -------
        str
            New path of the moved dataset or directory.

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
        urlbase, _ = _format_paths(self.urlbase)
        result = api_utils.post(
            f"{self.urlbase}/api/move/",
            {"src": str(src), "dst": str(dst)},
            auth_cookie=self.cookie,
        )
        return pathlib.PurePosixPath(result)

    def copy(self, src, dst):
        """
        Copies a dataset or directory to a new location.

        Parameters
        ----------
        src : Path
            Source path of the dataset or directory.
        dst : Path
            Destination path for the dataset or directory.

        Returns
        -------
        str
            New path of the copied dataset or directory.

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
        urlbase, _ = _format_paths(self.urlbase)
        result = api_utils.post(
            f"{self.urlbase}/api/copy/", {"src": str(src), "dst": str(dst)}, auth_cookie=self.cookie
        )
        return pathlib.PurePosixPath(result)

    def lazyexpr(self, name, expression, operands=None, compute=False):
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
        compute : bool, optional
            If false, generate lazyexpr and do not compute anything.
            If true, compute lazy expression on creation and save (full) result.
            Default false.

        Returns
        -------
        Path
            Path of the created dataset.

        Examples
        --------
        >>> import caterva2 as cat2
        >>> import numpy as np
        >>> # To create a lazyexpr you need to be a registered used
        >>> client = cat2.Client('https://cat2.cloud/demo', ("joedoe@example.com", "foobar"))
        >>> src_path = f'@personal/dir{np.random.randint(0, 100)}/ds-4d.b2nd'
        >>> path = client.upload('root-example/dir1/ds-2d.b2nd', src_path)
        >>> client.lazyexpr('example-expr', 'a + a', {'a': path})
        PurePosixPath('@personal/example-expr.b2nd')
        >>> 'example-expr.b2nd' in client.get_list('@personal')
        True
        """
        urlbase, _ = _format_paths(self.urlbase)
        # Convert possible Path objects in operands to strings so that they can be serialized
        if operands is not None:
            operands = {k: str(v) for k, v in operands.items()}
        else:
            operands = {}
        expr = {"name": name, "expression": expression, "operands": operands, "compute": compute}
        dataset = api_utils.post(f"{self.urlbase}/api/lazyexpr/", expr, auth_cookie=self.cookie)
        return pathlib.PurePosixPath(dataset)

    def adduser(self, newuser, password=None, superuser=False):
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
        return api_utils.post(
            f"{self.urlbase}/api/adduser/",
            {"username": newuser, "password": password, "superuser": superuser},
            auth_cookie=self.cookie,
        )

    def deluser(self, user):
        """
        Deletes a user from the subscriber.

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
        return api_utils.get(f"{self.urlbase}/api/deluser/{user}", auth_cookie=self.cookie)

    def listusers(self, username=None):
        """
        Lists the users in the subscriber.

        Parameters
        ----------
        username : str, optional
            Username of the specific user to list.

        Returns
        -------
        list of dict
            A list of user dictionaries in the subscriber.

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
        return api_utils.get(url, auth_cookie=self.cookie)
