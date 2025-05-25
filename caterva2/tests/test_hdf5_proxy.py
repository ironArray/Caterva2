###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import contextlib
import pathlib
import shutil

import blosc2
import h5py
import numexpr as ne
import numpy as np
import pytest

hdf5root = pytest.importorskip("caterva2.services.hdf5root", reason="HDF5 support not present")

try:
    chdir_ctxt = contextlib.chdir
except AttributeError:  # Python < 3.11
    import os

    @contextlib.contextmanager
    def chdir_ctxt(path):
        cwd = os.getcwd()
        os.chdir(path)
        yield
        os.chdir(cwd)


def get_all_datasets(f, prefix=""):
    """Recursively get all datasets in an HDF5 file."""
    datasets = []
    for name, item in f.items():
        path = f"{prefix}/{name}".lstrip("/")
        if isinstance(item, h5py.Dataset):
            datasets.append(path)
        elif isinstance(item, h5py.Group):
            # Recursively get datasets in subgroups
            datasets.extend(get_all_datasets(item, path))
    return datasets


@pytest.mark.parametrize(
    "fnames",
    [
        ("ex-noattr.h5", None),
        ("ex-noattr.h5", "ex-noattr2.h5"),  # upload with remote name
        ("root-example.h5", None),
        (None, None),  # use hdf5root.create_example_root(h5fpath)
    ],
)
@pytest.mark.parametrize("root", ["@personal", "@shared", "@public"])
@pytest.mark.parametrize("remove", [True, False])
def test_unfold(fnames, remove, root, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    # First, choose an HDF5 dataset
    localpath, remotepath = fnames
    remote_root = auth_client.get(root)
    with chdir_ctxt(tmp_path):
        if localpath is None:
            # Create a temporary HDF5 file
            localpath = "create-example-root.h5"
            path = tmp_path / localpath
            hdf5root.create_example_root(path)
        else:
            path = examples_dir / localpath
            assert path.is_file()
            # Copy the file in path to localpath
            shutil.copy2(path, localpath)
        # Now, upload the file to the remote root
        remote_ds = remote_root.upload(localpath, remotepath)
        # Unfold the file
        remote_dir = remote_ds.unfold()
        # Check whether the file has been unfolded correctly
        # with the same name as the original file, but without extension
        assert remote_dir == pathlib.PurePosixPath(remote_ds.path.stem)

        # Get the list of datasets in HDF5 file using h5py
        with h5py.File(path, "r") as f:
            # Get the list of *datasets* in the HDF5 file
            file_list = [name + ".b2nd" for name in get_all_datasets(f)]
        for file_ in file_list:
            if "unsupported" in file_:
                continue
            remote_file = remote_dir / file_
            assert remote_file in remote_root
            # Check removing the file
            if remove:
                remote_ds = remote_root[str(remote_file)]
                remote_removed = pathlib.Path(remote_ds.remove())
                assert remote_removed == remote_ds.path
                # Check that the file has been removed
                with pytest.raises(Exception) as e_info:
                    _ = remote_root[remote_removed]
                assert "Not Found" in str(e_info.value)


def create_and_unfold_hdf5(tmp_path, remote_root, create_file=True, localpath="create-example-root.h5"):
    """
    Create a temporary HDF5 file, upload it to remote, unfold it, and return the remote directory
    and list of datasets.

    Parameters:
    -----------
    tmp_path : Path
        Temporary directory path
    remote_root : RemoteRoot
        The remote root to upload to
    create_file : bool, default=True
        Whether to create a new file or use existing one
    localpath : str, default="create-example-root.h5"
        Path to the local HDF5 file

    Returns:
    --------
    remote_dir : PurePosixPath
        Path to the unfolded directory on the remote
    file_list : list
        List of dataset paths with .b2nd extension
    remote_ds : RemoteDataset
        The uploaded remote dataset
    """
    hdf5_path = tmp_path / localpath

    if create_file:
        # Create a temporary HDF5 file
        hdf5root.create_example_root(hdf5_path)

    # Upload the file to the remote root
    remote_ds = remote_root.upload(localpath)

    # Unfold the file
    remote_dir = remote_ds.unfold()

    # Check whether the file has been unfolded correctly
    # with the same name as the original file, but without extension
    assert remote_dir == pathlib.PurePosixPath(remote_ds.path.stem)

    # Get the list of datasets in HDF5 file using h5py
    with h5py.File(hdf5_path, "r") as f:
        # Get the list of datasets in the HDF5 file
        file_list = [name + ".b2nd" for name in get_all_datasets(f)]

    return hdf5_path, remote_dir, file_list


def test_unfold_download(examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    root = pathlib.Path("@shared")
    remote_root = auth_client.get(root)
    with chdir_ctxt(tmp_path):
        hdf5_path, remote_dir, file_list = create_and_unfold_hdf5(tmp_path, remote_root)
        h5f = h5py.File(hdf5_path, "r")
        for file_ in file_list:
            if "unsupported" in file_:
                continue
            remote_file = remote_dir / file_
            assert remote_file in remote_root
            # Download the file
            remote_path = root / remote_file
            local_file = auth_client.download(remote_path)
            # Check that the file has been downloaded
            assert local_file.is_file()
            # Check that the file has the same contents as the original file
            b2f = blosc2.open(local_file)
            file_noext = file_.replace(".b2nd", "")
            h5ds = h5f[file_noext]
            if hasattr(h5ds.dtype, "shape") and h5ds.shape:
                # Shape in ndim dtypes is handled differently in HDF5Proxy and h5py
                # but the values for the array are essentially the same
                assert b2f[()].tobytes() == h5ds[()].tobytes()
                continue
            assert b2f.dtype == h5ds.dtype
            assert b2f.shape == (h5ds.shape or ())
            if b2f.shape == ():
                continue
            if h5ds.chunks:
                assert b2f.chunks == h5ds.chunks
            if np.issubdtype(b2f.dtype, np.number):
                np.testing.assert_allclose(b2f[()], h5ds[()])
            else:
                assert b2f[()].tobytes() == h5ds[()].tobytes()


@pytest.mark.parametrize("fetch_or_slice", [True, False])
def test_unfold_fetch(fetch_or_slice, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    root = pathlib.Path("@shared")
    remote_root = auth_client.get(root)
    with chdir_ctxt(tmp_path):
        hdf5_path, remote_dir, file_list = create_and_unfold_hdf5(tmp_path, remote_root)
        h5f = h5py.File(hdf5_path, "r")
        for file_ in file_list:
            if "unsupported" in file_:
                continue
            file_noext = file_.replace(".b2nd", "")
            h5ds = h5f[file_noext]
            if not h5ds.shape:
                # We cannot fetch an item from a scalar
                continue
            remote_file = remote_dir / file_
            assert remote_file in remote_root
            # Fetch items of the file
            item = slice(2, 4)
            b2ds = remote_root[remote_file]
            if hasattr(h5ds.dtype, "shape") and h5ds.shape:
                # Shape in ndim dtypes is handled differently in HDF5Proxy and h5py
                # but the values for the array are essentially the same
                assert b2ds[item].tobytes() == h5ds[item].tobytes()
                continue
            if fetch_or_slice:
                slice_ = b2ds[item]
            else:
                # Fetch a slice of the file
                slice_ = b2ds.slice(item)
            assert slice_.dtype == h5ds.dtype
            assert slice_.shape == h5ds[item].shape
            if np.issubdtype(slice_.dtype, np.number):
                np.testing.assert_allclose(slice_, h5ds[item])
            else:
                assert slice_.tobytes() == h5ds[item].tobytes()


# Exercises the expression evaluation with HDF5 proxies
@pytest.mark.parametrize(
    "expression",
    [
        "a + 50",
        "a ** 2.3 + b / 2.3",
        "sqrt(a) ** sin(b)",
        "where(a < 50, a + 50, b)",
    ],
)
def test_expression(expression, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    ds_a = "arrays/3d-blosc2-a"
    ds_b = "arrays/3d-blosc2-b"
    root = pathlib.Path("@shared")
    remote_root = auth_client.get(root)
    with chdir_ctxt(tmp_path):
        hdf5_path, remote_dir, file_list = create_and_unfold_hdf5(tmp_path, remote_root)
        h5f = h5py.File(hdf5_path, "r")
        remote_a = remote_dir / (ds_a + ".b2nd")
        assert remote_a in remote_root
        remote_b = remote_dir / (ds_b + ".b2nd")
        assert remote_b in remote_root

        operands = {"a": str(remote_root) + "/" + str(remote_a), "b": str(remote_root) + "/" + str(remote_b)}
        lxpath = auth_client.lazyexpr("myexpr", expression, operands)
        assert lxpath == pathlib.Path("@personal/myexpr.b2nd")

        # Compute the expression
        result = auth_client.get_slice(lxpath)
        assert isinstance(result, blosc2.NDArray)

        # Check the data
        na = h5f[ds_a][:]
        nb = h5f[ds_b][:]
        nresult = ne.evaluate(expression, {"a": na, "b": nb})
        np.testing.assert_allclose(result[:], nresult)
