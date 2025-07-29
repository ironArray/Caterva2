###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import contextlib
import os
import pathlib

import blosc2
import httpx
import numexpr as ne
import numpy as np
import pytest

import caterva2 as cat2

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR


@pytest.fixture
def fill_public(client, examples_dir):
    # Manually copy some files to the public area (TEST_STATE_DIR)
    dest_dir = pathlib.Path(TEST_STATE_DIR) / "subscriber/public"
    fnames = [str(fname.relative_to(examples_dir)) for fname in examples_dir.rglob("*") if fname.is_file()]
    for fname in fnames:
        orig = examples_dir / fname
        data = orig.read_bytes()
        if not fname.endswith(("b2nd", "b2frame", "h5")):
            fname += ".b2"
            schunk = blosc2.SChunk(data=data)
            data = schunk.to_cframe()
        dest = dest_dir / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    # We need a user here in case we want to remove files from @public
    return fnames, client.get("@public")


@pytest.fixture
def fill_auth(auth_client, fill_public):
    if not auth_client:
        return None
    fnames, _ = fill_public
    return fnames, auth_client.get("@public")


def test_roots(client, auth_client):
    client = auth_client if auth_client else client
    roots = client.get_roots()
    assert roots["@public"]["name"] == "@public"

    # Special roots (only available when authenticated)
    if auth_client:
        assert roots["@personal"]["name"] == "@personal"
        assert roots["@shared"]["name"] == "@shared"


def test_get_root(client, auth_client):
    mypublic = client.get("@public")
    assert mypublic.name == "@public"
    assert mypublic.urlbase == client.urlbase
    if auth_client:
        mypersonal = auth_client.get("@personal")
        assert mypersonal.name == "@personal"
        assert mypersonal.urlbase == auth_client.urlbase
        myshared = auth_client.get("@shared")
        assert myshared.name == "@shared"
        assert myshared.urlbase == auth_client.urlbase


def test_get_file(client, fill_public):
    myfile = client.get("@public/README.md")
    assert myfile.name == "README.md"


def test_get_dataset(client, fill_public):
    myds = client.get("@public/ds-1d.b2nd")
    assert myds.name == "ds-1d.b2nd"
    assert isinstance(myds, cat2.Dataset)
    assert myds.shape == (1000,)
    assert myds.dtype == np.dtype("int64")
    assert myds.chunks == (100,)
    assert myds.blocks == (10,)
    assert myds.urlbase == client.urlbase


def test_list(client, auth_client, examples_dir):
    if auth_client:
        mypersonal = auth_client.get("@personal")
        # In previous tests we have created some files in the personal area
        assert len(mypersonal.file_list) >= 0
        myshared = auth_client.get("@shared")
        assert set(myshared.file_list) == set()


def test_list_public(client, fill_public):
    fnames, mypublic = fill_public
    assert set(mypublic.file_list) == set(fnames)
    # Test toplevel list
    flist = client.get_list("@public")
    assert set(flist) == set(fnames)
    # Test directory list
    assert client.get_list("@public/dir1") == ["ds-2d.b2nd", "ds-3d.b2nd"]
    # Test directory list with trailing slash
    assert client.get_list("@public/dir1/") == ["ds-2d.b2nd", "ds-3d.b2nd"]
    # Test single dataset list
    assert client.get_list("@public/dir1/ds-2d.b2nd") == ["ds-2d.b2nd"]


def test_file_public(client, fill_public):
    fnames, mypublic = fill_public
    for fname in fnames:
        file = mypublic[fname]
        assert file.name == fname
        assert file.urlbase == client.urlbase


def test_dataset_info(client, fill_public):
    fnames, mypublic = fill_public
    for fname in fnames:
        if fname.endswith(".b2nd"):
            data = mypublic[fname]
            info = client.get_info("@public/" + fname)
            assert data.dtype == info["dtype"]
            assert data.shape == tuple(info["shape"])
            assert data.blocks == tuple(info["blocks"])
            assert data.chunks == tuple(info["chunks"])


@pytest.mark.parametrize("dirpath", [None, "dir1", "dir2", "dir2/dir3/dir4"])
@pytest.mark.parametrize("final_dir", [True, False])
def test_move(auth_client, dirpath, final_dir, fill_auth):
    if not auth_client:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_auth
    myshared = auth_client.get("@shared")
    for fname in fnames:
        file = mypublic[fname]
        if final_dir:
            new_fname = f"{dirpath}" if dirpath else ""
        else:
            new_fname = f"{dirpath}/{fname}" if dirpath else fname
        newpath = file.move(f"{myshared.name}/{new_fname}")
        assert fname not in mypublic
        if final_dir:
            basename = fname.split("/")[-1]
            new_path = f"{new_fname}/{basename}" if dirpath else basename
            assert str(newpath) == f"{myshared.name}/{new_path}"
            assert myshared[new_path].path == newpath
        else:
            assert str(newpath) == f"{myshared.name}/{new_fname}"
            assert myshared[new_fname].path == newpath
    return None


@pytest.mark.parametrize("dest", ["..", ".", "foo", "foo/bar"])
def test_move_not_allowed(auth_client, dest, fill_auth):
    if not auth_client:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_auth
    for fname in fnames:
        # Move the file to a non-special root and check for an exception
        file = mypublic[fname]
        with pytest.raises(Exception) as e_info:
            _ = file.move(dest)
        assert "Bad Request" in str(e_info)
        assert fname in mypublic
    return None


@pytest.mark.parametrize("dirpath", [None, "dir1", "dir2", "dir2/dir3/dir4"])
@pytest.mark.parametrize("final_dir", [True, False])
def test_copy(auth_client, dirpath, final_dir, fill_auth):
    if not auth_client:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_auth
    myshared = auth_client.get("@shared")
    for fname in fnames:
        file = mypublic[fname]
        if final_dir:
            new_fname = f"{dirpath}" if dirpath else ""
        else:
            new_fname = f"{dirpath}/{fname}" if dirpath else fname
        newpath = file.copy(f"{myshared.name}/{new_fname}")
        assert fname in mypublic
        if final_dir:
            basename = fname.split("/")[-1]
            new_path = f"{new_fname}/{basename}" if dirpath else basename
            assert str(newpath) == f"{myshared.name}/{new_path}"
            assert myshared[new_path].path == newpath
        else:
            assert str(newpath) == f"{myshared.name}/{new_fname}"
            assert myshared[new_fname].path == newpath
    return None


def test_concat(auth_client, fill_auth, examples_dir):
    if not auth_client:
        return pytest.skip("authentication support needed")

    _, mypublic = fill_auth
    myshared = auth_client.get("@shared")
    # Copy a 1d dataset to the shared area
    file = mypublic["ds-1d.b2nd"]
    copyname = "a.b2nd"
    newpath = file.copy(f"@shared/{copyname}")
    assert newpath == myshared[copyname].path
    copyname2 = "b.b2nd"
    newpath2 = file.copy(f"@shared/{copyname2}")
    assert newpath2 == myshared[copyname2].path
    copyname3 = "c.b2nd"
    newpath3 = file.copy(f"@shared/{copyname3}")
    assert newpath3 == myshared[copyname3].path

    # Test for File class
    file = myshared[copyname]
    resultname = "result.b2nd"
    finalpath = file.concatenate([newpath2, newpath3], f"{myshared.name}/{resultname}", axis=0)

    assert myshared[resultname].shape[0] == 3 * myshared[copyname].shape[0]

    # Check the data
    fname = examples_dir / "ds-1d.b2nd"
    a = blosc2.open(fname)
    locres = np.concatenate([a[:], a[:], a[:]], axis=0)
    sfile = myshared[resultname]
    np.testing.assert_array_equal(sfile[:], locres)

    # Test for Client class
    resultname = "result2.b2nd"
    finalpath = auth_client.concatenate(
        [newpath, newpath2, newpath3], f"{myshared.name}/{resultname}", axis=0
    )

    assert myshared[resultname].shape[0] == 3 * myshared[copyname].shape[0]

    # Check the data
    sfile = myshared[resultname]
    return np.testing.assert_array_equal(sfile[:], locres)


def test_stack(auth_client, fill_auth, examples_dir):
    if not auth_client:
        return pytest.skip("authentication support needed")

    _, mypublic = fill_auth
    myshared = auth_client.get("@shared")
    fstr = "dir1/ds-2d.b2nd"

    # Copy a 1d dataset to the shared area
    file = mypublic[fstr]
    s = file.shape
    news = (s[0], 3, *s[1:])
    copyname = "a.b2nd"
    newpath = file.copy(f"@shared/{copyname}")
    assert newpath == myshared[copyname].path
    copyname2 = "b.b2nd"
    newpath2 = file.copy(f"@shared/{copyname2}")
    assert newpath2 == myshared[copyname2].path
    copyname3 = "c.b2nd"
    newpath3 = file.copy(f"@shared/{copyname3}")
    assert newpath3 == myshared[copyname3].path

    # Test for File class
    file = myshared[copyname]
    resultname = "result.b2nd"
    finalpath = file.stack([newpath2, newpath3], f"{myshared.name}/{resultname}", axis=1)
    assert myshared[resultname].shape[1] == 3
    assert myshared[resultname].shape == news

    # Check the data
    fname = examples_dir / fstr
    a = blosc2.open(fname)
    locres = np.stack([a[:], a[:], a[:]], axis=1)
    sfile = myshared[resultname]
    np.testing.assert_array_equal(sfile[:], locres)

    # Test for Client class
    resultname = "result2.b2nd"
    finalpath = auth_client.stack([newpath, newpath2, newpath3], f"{myshared.name}/{resultname}", axis=1)

    assert myshared[resultname].shape[1] == 3
    assert myshared[resultname].shape == news

    # Check the data
    sfile = myshared[resultname]
    return np.testing.assert_array_equal(sfile[:], locres)


@pytest.mark.parametrize("fields", [True, False])
def test_append(auth_client, fields, fill_auth, examples_dir):
    if not auth_client:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_auth
    myshared = auth_client.get("@shared")
    fname = "ds-1d.b2nd" if not fields else "ds-1d-fields.b2nd"
    # Copy a 1d dataset to the shared area
    file = mypublic[fname]
    newpath = file.copy(f"@shared/{fname}")
    assert newpath == myshared[fname].path
    # Append to the dataset
    if fields:
        data = np.asarray(
            [(1000, 1.0, b"foobar1000", False), (1001, 2.0, b"foobar1001", True)],
            dtype=[("a", "<i4"), ("b", "<f8"), ("c", "S10"), ("d", "?")],
        )
    else:
        data = [1, 2, 3]
    sfile = myshared[fname]
    new_shape = sfile.append(data)
    assert new_shape == (len(data) + file.meta["shape"][0],)

    # Check the data
    fname = examples_dir / fname
    a = blosc2.open(fname)
    b = np.concatenate([a[:], data])
    return np.testing.assert_array_equal(sfile[:], b)


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(10, 20, 1)],
)
def test_dataset_getitem_fetch(slice_, examples_dir, client, fill_public):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["ds-hello.b2frame"]
    assert ds.name == "ds-hello.b2frame"
    assert ds.urlbase == client.urlbase

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
    else:
        assert ds[slice_] == a[slice_]


def test_dataset_step_diff_1(client):
    # XXX This should fail with anonymous client, because the root is not @public
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["ds-hello.b2frame"]
    assert ds.name == "ds-hello.b2frame"
    assert ds.urlbase == client.urlbase
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:  # noqa: PT012
        _ = ds[::2]
        assert str(e_info.value) == "Only step=1 is supported"


@pytest.mark.parametrize(
    "slice_",
    [0, 1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_getitem_dataset_1d(slice_, examples_dir, client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["ds-1d.b2nd"]
    assert ds.name == "ds-1d.b2nd"
    assert ds.urlbase == client.urlbase

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])


@pytest.mark.parametrize(
    "slice_",
    [
        1,
        slice(None, 1),
        slice(0, 10),
        slice(10, 20),
        slice(None),
        slice(1, 5, 1),
        (slice(None, 10), slice(None, 20)),
    ],
)
@pytest.mark.parametrize("name", ["dir1/ds-2d.b2nd", "dir2/ds-4d.b2nd"])
def test_getitem_dataset_nd(slice_, name, examples_dir, client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])


@pytest.mark.parametrize(
    "slice_",
    [
        1,
        slice(None, 1),
        slice(0, 10),
        slice(10, 20),
        slice(None),
        slice(1, 5, 1),
        (slice(None, 10), slice(None, 20)),
    ],
)
@pytest.mark.parametrize("name", ["dir1/ds-2d.b2nd", "dir2/ds-4d.b2nd"])
def test_slice_dataset_nd(slice_, name, examples_dir, client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    arr = ds.slice(slice_)
    assert isinstance(arr, blosc2.NDArray)
    np.testing.assert_array_equal(arr[()], a[slice_])


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_getitem_regular_file(slice_, examples_dir, client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["README.md"]

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read().encode()
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
    else:
        assert ds[slice_] == a[slice_]


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_getitem_client_regular_file(slice_, examples_dir, client):
    # Data contents
    example = examples_dir / "README.md"
    a = open(example).read().encode()
    if isinstance(slice_, int):
        assert ord(client.fetch(TEST_CATERVA2_ROOT + "/" + "README.md", slice_=slice_)) == a[slice_]
    else:
        assert client.fetch(TEST_CATERVA2_ROOT + "/" + "README.md", slice_=slice_) == a[slice_]


@pytest.mark.parametrize(
    "slice_",
    [0, 1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_getitem_client_1d(slice_, examples_dir, client):
    example = examples_dir / "ds-1d.b2nd"
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(
        client.fetch(TEST_CATERVA2_ROOT + "/" + "ds-1d.b2nd", slice_=slice_), a[slice_]
    )


@pytest.mark.parametrize(
    "slice_",
    [
        1,
        slice(None, 1),
        slice(0, 10),
        slice(10, 20),
        slice(None),
        slice(1, 5, 1),
        (slice(None, 10), slice(None, 20)),
    ],
)
@pytest.mark.parametrize("name", ["dir1/ds-2d.b2nd", "dir2/ds-4d.b2nd"])
def test_getitem_client_nd(slice_, name, examples_dir, client):
    example = examples_dir / name
    a = blosc2.open(example)[:]
    arr = client.fetch(TEST_CATERVA2_ROOT + "/" + name, slice_=slice_)
    np.testing.assert_array_equal(arr, a[slice_])


@pytest.mark.parametrize("name", ["ds-1d.b2nd", "dir1/ds-2d.b2nd"])
def test_download_b2nd(name, examples_dir, tmp_path, client, auth_client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot[name]
    with contextlib.chdir(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    with contextlib.chdir(tmp_path):
        b = blosc2.open(path)
        np.testing.assert_array_equal(a[:], b[:])

    # Using 2-step download
    urlpath = ds.get_download_url()
    data = httpx.get(urlpath, headers={"Cookie": auth_client.cookie} if auth_client else None)
    assert data.status_code == 200
    b = blosc2.ndarray_from_cframe(data.content)
    np.testing.assert_array_equal(a[:], b[:])


def test_download_b2frame(examples_dir, tmp_path, client, auth_client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["ds-hello.b2frame"]
    with contextlib.chdir(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = blosc2.open(example)
    with contextlib.chdir(tmp_path):
        b = blosc2.open(path)
        assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{client.urlbase}/api/fetch/{ds.path}"
    data = httpx.get(urlpath, headers={"Cookie": auth_client.cookie} if auth_client else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    assert a[:] == b[:]


@pytest.mark.parametrize(
    "fnames",
    [
        ("ds-1d.b2nd", "ds-1d2.b2nd"),
        ("dir1/ds-2d.b2nd", "dir2/ds-2d2.b2nd"),
        ("dir1/ds-2d.b2nd", "dir2/dir3/dir4/ds-2d2.b2nd"),
        ("dir1/ds-2d.b2nd", "dir2/dir3/dir4/"),
    ],
)
def test_download_localpath(fnames, examples_dir, tmp_path, client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    name, localpath = fnames
    ds = myroot[name]
    with contextlib.chdir(tmp_path):
        if localpath.endswith("/"):
            # Create a directory in localpath
            localpath2 = pathlib.Path(localpath)
            localpath2.mkdir(parents=True, exist_ok=True)
        path = ds.download(localpath)
        if localpath.endswith("/"):
            localpath = localpath + name.split("/")[-1]
        assert str(path) == localpath

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    with contextlib.chdir(tmp_path):
        b = blosc2.open(path)
        np.testing.assert_array_equal(a[:], b[:])


def test_download_regular_file(examples_dir, tmp_path, client, auth_client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["README.md"]
    with contextlib.chdir(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read()
    with contextlib.chdir(tmp_path):
        b = open(path).read()
        assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{client.urlbase}/api/fetch/{ds.path}"
    data = httpx.get(urlpath, headers={"Cookie": auth_client.cookie} if auth_client else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    # TODO: why do we need .decode() here?
    assert a[:] == b[:].decode()


def test_download_public_file(examples_dir, fill_public, tmp_path):
    fnames, mypublic = fill_public
    for fname in fnames:
        # TODO fetch (and download) of HDF5 files is not supported (gives a 500 error)
        if fname.endswith(".h5"):
            continue

        # Download the file
        ds = mypublic[fname]
        with contextlib.chdir(tmp_path):
            path = ds.download()
            assert path == ds.path
            # Check data contents
            example = examples_dir / ds.name
            a = open(example, "rb").read()
            b = open(path, "rb").read()
            assert a[:] == b[:]


@pytest.mark.parametrize(
    "fnames",
    [
        ("ds-1d.b2nd", None),
        ("ds-hello.b2frame", None),
        ("README.md", None),
        ("README.md", "README2.md"),
        ("dir1/ds-2d.b2nd", None),
        ("dir1/ds-2d.b2nd", "dir2/ds-2d.b2nd"),
        ("dir1/ds-2d.b2nd", "dir2/dir3/dir4/ds-2d2.b2nd"),
        ("dir1/ds-3d.b2nd", "dir2/dir3/dir4/"),
    ],
)
@pytest.mark.parametrize("root", ["@personal", "@shared", "@public"])
@pytest.mark.parametrize("remove", [False, True])
def test_upload(fnames, remove, root, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    localpath, remotepath = fnames
    remote_root = auth_client.get(root)
    myroot = auth_client.get(TEST_CATERVA2_ROOT)
    ds = myroot[localpath]
    with contextlib.chdir(tmp_path):
        path = ds.download()
        assert path == ds.path
        # Check whether path exists and is a file
        assert path.exists()
        assert path.is_file()
        # Now, upload the file to the remote root
        remote_ds = remote_root.upload(path, remotepath)
        # Check whether the file has been uploaded with the correct name
        if remotepath:
            if remotepath.endswith("/"):
                assert remote_ds.name == remotepath + path.name
            else:
                assert remote_ds.name == remotepath
        else:
            assert remote_ds.name == str(path)
        # Check removing the file
        if remove:
            remote_removed = pathlib.Path(remote_ds.remove())
            assert remote_removed == remote_ds.path
            # Check that the file has been removed
            with pytest.raises(Exception) as e_info:
                _ = remote_root[remote_removed]
            assert "Not Found" in str(e_info.value)


def test_upload_public_unauthorized(client, auth_client, examples_dir, tmp_path):
    if auth_client:
        pytest.skip("not authentication needed")

    remote_root = client.get("@public")
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["README.md"]
    with contextlib.chdir(tmp_path):
        path = ds.download()
        assert path == ds.path
        with pytest.raises(Exception) as e_info:
            _ = remote_root.upload(path)
        assert "Unauthorized" in str(e_info)


@pytest.mark.parametrize("name", ["ds-1d.b2nd", "ds-hello.b2frame", "README.md"])
def test_vlmeta(client, name):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot[name]
    schunk_meta = ds.meta.get("schunk", ds.meta)
    assert ds.vlmeta is schunk_meta["vlmeta"]


def test_vlmeta_data(client):
    myroot = client.get(TEST_CATERVA2_ROOT)
    ds = myroot["ds-sc-attr.b2nd"]
    assert ds.vlmeta == {"a": 1, "b": "foo", "c": 123.456}


### Lazy expressions


def test_lazyexpr(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} + 0"
    operands = {opnm: oppt}
    lxname = "my_expr"

    opinfo = auth_client.get_info(oppt)
    lxpath = auth_client.lazyexpr(lxname, expression, operands)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check result metadata.
    lxinfo = auth_client.get_info(lxpath)
    assert lxinfo["shape"] == opinfo["shape"]
    assert lxinfo["dtype"] == opinfo["dtype"]
    assert lxinfo["expression"] == expression
    assert lxinfo["operands"] == operands

    # Check result data.
    a = auth_client.fetch(oppt)
    b = auth_client.fetch(lxpath)
    np.testing.assert_array_equal(a[:], b[:])


# More exercises for the expression evaluation with Blosc2 arrays
@pytest.mark.parametrize(
    "expression",
    [
        "a + 50",
        "a ** 2.3 + b / 2.3",
        "sqrt(a) ** sin(b)",
        "where(a < 50, a + 50, b)",
    ],
)
def test_lazyexpr2(expression, examples_dir, tmp_path, auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    root = pathlib.Path("@shared")
    remote_root = auth_client.get(root)
    remote_dir = "arrays"
    ds_a = f"{remote_dir}/3d-blosc2-a.b2nd"
    ds_b = f"{remote_dir}/3d-blosc2-b.b2nd"

    with contextlib.chdir(tmp_path):
        os.makedirs(remote_dir, exist_ok=True)
        a = np.linspace(-1, 2, 1000).reshape(10, 10, 10)
        blosc2.asarray(a, urlpath=ds_a, chunks=(5, 10, 10))
        remote_a = remote_root.upload(ds_a)
        b = np.linspace(-1, 2, 1000).reshape(10, 10, 10)
        blosc2.asarray(b, urlpath=ds_b, chunks=(3, 5, 5))
        remote_b = remote_root.upload(ds_b)
        assert ds_a in remote_root
        assert ds_b in remote_root

        operands = {"a": remote_a, "b": remote_b}
        lxpath = auth_client.lazyexpr("myexpr", expression, operands)
        assert lxpath == pathlib.Path("@personal/myexpr.b2nd")

        # Compute the expression
        result = auth_client.get_slice(lxpath)
        assert isinstance(result, blosc2.NDArray)

        # Check the data
        nresult = ne.evaluate(expression, {"a": a, "b": b})
        np.testing.assert_allclose(result[:], nresult)


def test_lazyexpr_getchunk(auth_client, fill_public):
    if not auth_client:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} - 0"
    operands = {opnm: oppt}
    lxname = "my_expr"

    lxpath = auth_client.lazyexpr(lxname, expression, operands)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check for chunksize and dtype
    opinfo = auth_client.get_info(oppt)
    chunksize = opinfo["chunks"][0]
    dtype = opinfo["dtype"]

    # Get the first chunks
    chunk_ds = auth_client.get_chunk(oppt, 0)
    chunk_expr = auth_client.get_chunk(lxpath, 0)
    # Check data
    out = np.empty(chunksize, dtype=dtype)
    blosc2.decompress2(chunk_ds, out)
    out_expr = np.empty(chunksize, dtype=dtype)
    blosc2.decompress2(chunk_expr, out_expr)
    np.testing.assert_array_equal(out, out_expr)


def test_lazyexpr_fields(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d-fields.b2nd"

    # Test a field
    arr = auth_client.get(oppt)
    field = arr["a"]
    np.testing.assert_allclose(field[:], arr[:]["a"])

    # Test a lazyexpr
    servered = arr["(a < 500) & (b >= .1)"][:]
    downloaded = arr.slice(None)["(a < 500) & (b >= .1)"][:]
    [np.testing.assert_array_equal(servered[f], downloaded[f]) for f in downloaded.dtype.fields]


def test_lazyexpr_cache(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    root = auth_client.get("@personal")
    oppt = f"{root.name}/sa-1M.b2nd"

    N = 1000
    rng = np.random.default_rng(seed=1)
    it = ((-x + 1, x - 2, x) for x in range(N))
    sa = blosc2.fromiter(
        it, dtype=[("A", "i4"), ("B", "f4"), ("C", "f8")], shape=(N,), urlpath="sa-1M.b2nd", mode="w"
    )
    path = auth_client.upload("sa-1M.b2nd", oppt)
    arr = auth_client.get(path)

    # Test a lazyexpr
    arr = auth_client.get(oppt)
    servered = arr["(A < 500) & (B >= .1)"][:]
    downloaded = arr.slice(None)["(A < 500) & (B >= .1)"][:]
    [np.testing.assert_array_equal(servered[f], downloaded[f]) for f in downloaded.dtype.fields]

    # Overwrite the file and check that cache isn't used
    N = 10000
    rng = np.random.default_rng(seed=1)
    it = ((-x + 1, x - 2, x) for x in range(N))
    sa = blosc2.fromiter(
        it, dtype=[("A", "i4"), ("B", "f4"), ("C", "f8")], shape=(N,), urlpath="sa-1M.b2nd", mode="w"
    )
    path = auth_client.upload("sa-1M.b2nd", oppt)
    arr = auth_client.get(path)

    # Test lazyexpr again
    servered = arr["(A < - 500) & (B >= .1)"][:]
    downloaded = arr.slice(None)["(A < - 500) & (B >= .1)"][:]
    [np.testing.assert_allclose(servered[f], downloaded[f]) for f in downloaded.dtype.fields]

    # remove file
    arr.remove()


def test_expr_from_expr(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} + 1"
    operands = {opnm: oppt}
    lxname = "my_expr"

    opinfo = auth_client.get_info(oppt)
    lxpath = auth_client.lazyexpr(lxname, expression, operands)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    expression2 = f"{opnm} * 2"
    operands2 = {opnm: f"@personal/{lxname}.b2nd"}
    lxname = "expr_from_expr"
    lxpath2 = auth_client.lazyexpr(lxname, expression2, operands2)
    assert lxpath2 == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check result metadata.
    lxinfo = auth_client.get_info(lxpath)
    lxinfo2 = auth_client.get_info(lxpath2)
    assert lxinfo["shape"] == opinfo["shape"] == lxinfo2["shape"]
    assert lxinfo["dtype"] == opinfo["dtype"] == lxinfo2["dtype"]
    assert lxinfo["operands"] == operands
    assert lxinfo["expression"] == expression

    assert lxinfo2["operands"] == operands2
    assert lxinfo2["expression"] == expression2

    # Check result data.
    a = auth_client.fetch(oppt)
    b = auth_client.fetch(lxpath)
    c = auth_client.fetch(lxpath2)
    np.testing.assert_array_equal(a[:] + 1, b[:])
    np.testing.assert_array_equal((a[:] + 1) * 2, c[:])


def test_expr_no_operand(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    expression = "linspace(0, 10, num=50)"
    lxname = "my_expr"

    lxpath = auth_client.lazyexpr(lxname, expression)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")
    c = auth_client.get(lxpath)
    a = blosc2.linspace(0, 10, num=50)
    np.testing.assert_array_equal(a[:], c[:])

    # Check error when operand should be present but isn't
    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = "ds + linspace(0, 10, num=50)"
    lxname = "my_expr"

    with pytest.raises(Exception) as e_info:
        lxpath = auth_client.lazyexpr(lxname, expression)


def test_expr_force_compute(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    expression = "linspace(0, 10, num=50)"
    lxname = "my_expr"

    # Uncomputed lazyexpr is a blosc2 lazyexpr
    lxpath = auth_client.lazyexpr(lxname, expression, compute=False)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")
    c = auth_client.get(lxpath)
    assert c.meta["expression"] == expression

    # Computed lazyexpr is a blosc2 array
    lxpath = auth_client.lazyexpr(lxname, expression, compute=True)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")
    c = auth_client.get(lxpath)
    assert c.meta.get("expression", None) is None


# User management
def test_adduser(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    message = auth_client.adduser(username, password, is_superuser)
    assert "User added" in message
    lusers = auth_client.listusers()
    assert username in [user["email"] for user in lusers]
    # Delete the user for future tests
    message = auth_client.deluser(username)
    assert "User deleted" in message


def test_adduser_malformed(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    username = "test_noat_user"
    password = "testpassword"
    is_superuser = False
    with pytest.raises(Exception) as e_info:
        _ = auth_client.adduser(username, password, is_superuser)
    assert "Bad Request" in str(e_info)


def test_adduser_maxexceeded(auth_client, configuration):
    if not auth_client:
        pytest.skip("authentication support needed")

    # TODO: make this to work; currently this returns None
    # maxusers = configuration.get("subscriber.maxusers")
    # For now, keep in sync with subscriber.maxusers in caterva2/tests/caterva2-login.toml
    maxusers = 5
    # Add maxusers users; we already have one user, so the next loop should fail
    # when reaching the creation of last user
    n = 0
    for n in range(maxusers):
        # This should work fine for n < maxusers
        username = f"test{n}@user.com"
        password = "testpassword"
        is_superuser = False
        if n == maxusers - 1:  # we already have one user
            with pytest.raises(Exception) as e_info:
                _ = auth_client.adduser(username, password, is_superuser)
            assert "Bad Request" in str(e_info.value)
        else:
            message = auth_client.adduser(username, password, is_superuser)
            assert "User added" in message
    # Remove the created users
    for m in range(n):
        username = f"test{m}@user.com"
        message = auth_client.deluser(username)
        assert "User deleted" in message
    # Count the current number of users
    data = auth_client.listusers()
    assert len(data) == 1


def test_adduser_unauthorized(client, auth_client):
    if auth_client:
        pytest.skip("not authentication needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    with pytest.raises(Exception) as e_info:
        _ = client.adduser(username, password, is_superuser)
    assert "Not Found" in str(e_info)


def test_deluser(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    message = auth_client.adduser(username, password, is_superuser)
    assert "User added" in message
    # Now, delete the user
    message = auth_client.deluser(username)
    assert "User deleted" in message
    # Check that the user has been deleted
    with pytest.raises(Exception) as e_info:
        _ = auth_client.deluser(username)
    assert "Bad Request" in str(e_info)


def test_deluser_unauthorized(client, auth_client):
    if auth_client:
        pytest.skip("not authentication needed")

    username = "test@user.com"
    with pytest.raises(Exception) as e_info:
        _ = client.deluser(username)
    assert "Not Found" in str(e_info)


def test_listusers(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    message = auth_client.adduser(username, password, is_superuser)
    assert "User added" in message
    # List users
    data = auth_client.listusers()
    assert username in [user["email"] for user in data]
    # Delete the user
    message = auth_client.deluser(username)
    assert "User deleted" in message
    # List users again
    data = auth_client.listusers()
    assert username not in [user["email"] for user in data]


def test_listusers_unauthorized(client, auth_client):
    if auth_client:
        pytest.skip("not authentication needed")

    with pytest.raises(Exception) as e_info:
        _ = client.listusers()
    assert "Not Found" in str(e_info)


def test_client_timeout(auth_client):
    if not auth_client:
        pytest.skip("authentication support needed")

    lxpath = auth_client.lazyexpr("expr", "linspace(0, 100, 1000_0000)", compute=True)
    assert lxpath == pathlib.Path("@personal/expr.b2nd")
    auth_client.timeout = 0.0001
    # Try again
    with pytest.raises(Exception) as e_info:
        _ = auth_client.lazyexpr("expr", "linspace(0, 100, 1000_0000)", compute=True)
    assert "Timeout" in str(e_info)
    auth_client.timeout = 5  # Reset timeout to default value
