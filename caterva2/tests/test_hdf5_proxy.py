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

import h5py
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
