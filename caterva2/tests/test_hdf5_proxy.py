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
from hdf5plugin import Blosc2 as B2Comp


def create_example_root(path):
    """Create an example HDF5 file to be used as a root."""

    with h5py.File(path, "x") as h5f:
        h5f.create_dataset("/scalar", data=123.456)
        h5f.create_dataset("/string", data=np.bytes_("Hello world!"))

        a = np.arange(100, dtype="uint8")
        h5f.create_dataset("/arrays/1d-raw", data=a)

        h5f["/arrays/soft-link"] = h5py.SoftLink("/arrays/1d-raw")

        a = np.array([b"foobar"] * 100)
        h5f.create_dataset("/arrays/1ds-blosc2", data=a, chunks=(50,), **B2Comp())

        a = np.arange(100, dtype="complex128").reshape(10, 10)
        a = a + a * 1j
        h5f.create_dataset("/arrays/2d-nochunks", data=a, chunks=None)

        a = np.arange(100, dtype="complex128").reshape(10, 10)
        a = a + a * 1j
        h5f.create_dataset("/arrays/2d-gzip", data=a, chunks=(4, 4), compression="gzip")

        a = np.arange(1000, dtype="uint8").reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2",
            data=a,
            chunks=(4, 10, 10),
            **B2Comp(cname="lz4", clevel=7, filters=B2Comp.BITSHUFFLE),
        )

        a = np.linspace(-1, 2, 1000).reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2-a",
            data=a,
            chunks=(4, 10, 10),
            **B2Comp(cname="lz4", clevel=7, filters=B2Comp.BITSHUFFLE),
        )

        a = np.linspace(-1, 2, 1000).reshape(10, 10, 10)
        h5f.create_dataset(
            "/arrays/3d-blosc2-b",
            data=a,
            chunks=(2, 5, 10),
            **B2Comp(cname="blosclz", clevel=7, filters=B2Comp.SHUFFLE),
        )

        h5f.create_dataset("/arrays/array-dtype", dtype=np.dtype(("float64", (4,))), shape=(10,))

        ds = h5f.create_dataset("/attrs", data=0)
        a = np.arange(4, dtype="uint8").reshape(2, 2)
        for k, v in {
            "Int": 42,
            "IntT": np.int16(42),
            "Bin": b"foo",
            "BinT": np.bytes_(b"foo"),
            "Str": "bar",  # StrT=np.str_("bar"),
            "Arr": a.tolist(),
            "ArrT": a,
            "NilBin": h5py.Empty("|S4"),
            # NilStr=h5py.Empty('|U4'),
            "NilInt": h5py.Empty("uint8"),
        }.items():
            ds.attrs[k] = v

        h5f.create_dataset("/arrays/empty", data=h5py.Empty("float64"))

        h5f.create_dataset("/arrays/compound-dtype", dtype=np.dtype("uint8,float64"), shape=(10,))

        a = np.arange(1, dtype="uint8").reshape((1,) * 23)
        h5f.create_dataset("/unsupported/too-many-dimensions", data=a)

        # TODO: This could be supported by mapping the vlstring dataset to an NDArray with shape=() and dtype="u1"
        h5f.create_dataset("/unsupported/vlstring", data="Hello world!")


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
        (None, None),  # use create_example_root(h5fpath)
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
    with contextlib.chdir(tmp_path):
        if localpath is None:
            # Create a temporary HDF5 file
            localpath = "create-example-root.h5"
            path = tmp_path / localpath
            create_example_root(path)
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
        create_example_root(hdf5_path)

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
    with contextlib.chdir(tmp_path):
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
            b2nd_pointer = auth_client.get(remote_path)
            if b2f.shape != ():  # skip empty datasets
                assert b2f.cbytes == b2nd_pointer.meta["schunk"]["cbytes"]
                assert b2f.cratio == b2nd_pointer.meta["schunk"]["cratio"]
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
    with contextlib.chdir(tmp_path):
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
        "matmul(a, b)",
    ],
)
def test_expression(expression, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    ds_a = "arrays/3d-blosc2-a"
    ds_b = "arrays/3d-blosc2-b"
    root = pathlib.Path("@shared")
    remote_root = auth_client.get(root)
    with contextlib.chdir(tmp_path):
        hdf5_path, remote_dir, _ = create_and_unfold_hdf5(tmp_path, remote_root)
        h5f = h5py.File(hdf5_path, "r")
        remote_a = remote_dir / (ds_a + ".b2nd")
        assert remote_a in remote_root
        remote_b = remote_dir / (ds_b + ".b2nd")
        assert remote_b in remote_root

        operands = {"a": remote_root[remote_a], "b": remote_root[remote_b]}
        lexpr = blosc2.lazyexpr(expression, operands)
        lxobj = remote_root.upload(lexpr, "myexpr.b2nd", compute=False)
        assert lxobj.path == pathlib.Path("@shared/myexpr.b2nd")
        if expression == "matmul(a, b)":  # check evaluated eagerly for linalg
            assert "expression" not in auth_client.get_info(lxobj)
        else:
            assert "expression" in auth_client.get_info(lxobj)

        # Check the data
        na = h5f[ds_a][:]
        nb = h5f[ds_b][:]
        if expression != "matmul(a, b)":
            nresult = ne.evaluate(expression, {"a": na, "b": nb})
        else:
            nresult = np.matmul(na, nb)
        np.testing.assert_allclose(lxobj[()], nresult)
