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
import random

import blosc2
import httpx
import numpy as np
import pytest

import caterva2 as cat2

from .services import TEST_CATERVA2_ROOT, TEST_STATE_DIR

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


@pytest.fixture
def pub_host(services):
    return services.get_endpoint(f"publisher.{TEST_CATERVA2_ROOT}")


@pytest.fixture
def sub_urlbase(services):
    return services.get_urlbase("subscriber")


@pytest.fixture
def fill_public(examples_dir, sub_urlbase, sub_user):
    # Manually copy some files to the public area (TEST_STATE_DIR)
    fnames = ["README.md", "ds-1d.b2nd", "dir1/ds-2d.b2nd"]
    for fname in fnames:
        orig = examples_dir / fname
        data = orig.read_bytes()
        if not fname.endswith(("b2nd", "b2frame")):
            fname += ".b2"
            schunk = blosc2.SChunk(data=data)
            data = schunk.to_cframe()
        dest = pathlib.Path(TEST_STATE_DIR) / f"subscriber/public/{fname}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    # We need a user here in case we want to remove files from @public
    mypublic = cat2.Root("@public", sub_urlbase, sub_user)
    return fnames, mypublic


def test_subscribe(sub_urlbase, sub_jwt_cookie):
    assert cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase) == "Ok"
    assert cat2.subscribe("@public", sub_urlbase) == "Ok"
    for root in ["@personal", "@shared"]:
        if sub_jwt_cookie:
            assert cat2.subscribe(root, sub_urlbase, auth_cookie=sub_jwt_cookie) == "Ok"
        else:
            with pytest.raises(Exception) as e_info:
                _ = cat2.subscribe(root, sub_urlbase)
            assert "Unauthorized" in str(e_info)


def test_roots(pub_host, sub_urlbase, sub_jwt_cookie):
    roots = cat2.get_roots(sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert roots[TEST_CATERVA2_ROOT]["name"] == TEST_CATERVA2_ROOT
    assert roots[TEST_CATERVA2_ROOT]["http"] == pub_host
    assert roots["@public"]["name"] == "@public"
    assert roots["@public"]["http"] == ""
    if sub_jwt_cookie:
        # Special roots (only available when authenticated)
        assert roots["@personal"]["name"] == "@personal"
        assert roots["@personal"]["http"] == ""
        assert roots["@shared"]["name"] == "@shared"
        assert roots["@shared"]["http"] == ""


def test_root(sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    assert myroot.name == TEST_CATERVA2_ROOT
    assert myroot.urlbase == sub_urlbase
    mypublic = cat2.Root("@public", sub_urlbase, sub_user)
    assert mypublic.name == "@public"
    assert mypublic.urlbase == sub_urlbase
    if sub_user:
        mypersonal = cat2.Root("@personal", sub_urlbase, sub_user)
        assert mypersonal.name == "@personal"
        assert mypersonal.urlbase == sub_urlbase
        myshared = cat2.Root("@shared", sub_urlbase, sub_user)
        assert myshared.name == "@shared"
        assert myshared.urlbase == sub_urlbase


def test_list(examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    example = examples_dir
    files = {str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file()}
    assert set(myroot.file_list) == files
    if sub_user:
        mypersonal = cat2.Root("@personal", sub_urlbase, sub_user)
        # In previous tests we have created some files in the personal area
        assert len(mypersonal.file_list) >= 0
        myshared = cat2.Root("@shared", sub_urlbase, sub_user)
        assert set(myshared.file_list) == set()


def test_list_public(fill_public, sub_urlbase):
    fnames, mypublic = fill_public
    assert set(mypublic.file_list) == set(fnames)
    # Test toplevel list
    flist = cat2.get_list("@public", sub_urlbase)
    assert len(flist) == 3
    for fname in flist:
        assert fname in fnames
    # Test directory list
    flist = cat2.get_list("@public/dir1", sub_urlbase)
    assert len(flist) == 1
    for fname in flist:
        assert fname == "ds-2d.b2nd"
    # Test directory list with trailing slash
    flist = cat2.get_list("@public/dir1/", sub_urlbase)
    assert len(flist) == 1
    for fname in flist:
        assert fname == "ds-2d.b2nd"
    # Test single dataset list
    flist = cat2.get_list("@public/dir1/ds-2d.b2nd", sub_urlbase)
    assert len(flist) == 1
    for fname in flist:
        assert fname == "ds-2d.b2nd"


def test_file(sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    file = myroot["README.md"]
    assert file.name == "README.md"
    assert file.urlbase == sub_urlbase


def test_file_public(sub_urlbase, fill_public):
    fnames, mypublic = fill_public
    for fname in fnames:
        file = mypublic[fname]
        assert file.name == fname
        assert file.urlbase == sub_urlbase


@pytest.mark.parametrize("dirpath", [None, "dir1", "dir2", "dir2/dir3/dir4"])
@pytest.mark.parametrize("final_dir", [True, False])
def test_move(dirpath, final_dir, sub_urlbase, sub_user, fill_public):
    if not sub_user:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_public
    myshared = cat2.Root("@shared", sub_urlbase, sub_user)
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
def test_move_not_allowed(dest, sub_urlbase, sub_user, fill_public):
    if not sub_user:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_public
    for fname in fnames:
        # Move the file to a non-special root and check for an exception
        file = mypublic[fname]
        with pytest.raises(Exception) as e_info:
            _ = file.move(dest)
        print(e_info)
        assert "Bad Request" in str(e_info)
        assert fname in mypublic
    return None


@pytest.mark.parametrize("dirpath", [None, "dir1", "dir2", "dir2/dir3/dir4"])
@pytest.mark.parametrize("final_dir", [True, False])
def test_copy(dirpath, final_dir, sub_urlbase, sub_user, fill_public):
    if not sub_user:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_public
    myshared = cat2.Root("@shared", sub_urlbase, sub_user)
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


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(10, 20, 1)],
)
def test_index_dataset_frame(slice_, examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot["ds-hello.b2frame"]
    assert ds.name == "ds-hello.b2frame"
    assert ds.urlbase == sub_urlbase

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
        assert ord(ds.fetch(slice_)) == a[slice_]
    else:
        assert ds[slice_] == a[slice_]
        assert ds.fetch(slice_) == a[slice_]


def test_dataset_step_diff_1(sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot["ds-hello.b2frame"]
    assert ds.name == "ds-hello.b2frame"
    assert ds.urlbase == sub_urlbase
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:  # noqa: PT012
        _ = ds[::2]
        assert str(e_info.value) == "Only step=1 is supported"


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_index_dataset_1d(slice_, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot["ds-1d.b2nd"]
    assert ds.name == "ds-1d.b2nd"
    assert ds.urlbase == sub_urlbase

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


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
def test_index_dataset_nd(slice_, name, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_index_regular_file(slice_, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot["README.md"]

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read().encode()
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
        assert ord(ds.fetch(slice_)) == a[slice_]
    else:
        assert ds[slice_] == a[slice_]
        assert ds.fetch(slice_) == a[slice_]


@pytest.mark.parametrize("name", ["ds-1d.b2nd", "dir1/ds-2d.b2nd"])
def test_download_b2nd(name, examples_dir, sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot[name]
    with chdir_ctxt(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    with chdir_ctxt(tmp_path):
        b = blosc2.open(path)
        np.testing.assert_array_equal(a[:], b[:])

    # Using 2-step download
    urlpath = ds.get_download_url()
    data = httpx.get(urlpath, headers={"Cookie": sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.ndarray_from_cframe(data.content)
    np.testing.assert_array_equal(a[:], b[:])


def test_download_b2frame(examples_dir, sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot["ds-hello.b2frame"]
    with chdir_ctxt(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = blosc2.open(example)
    with chdir_ctxt(tmp_path):
        b = blosc2.open(path)
        assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{sub_urlbase}/api/fetch/{ds.path}"
    data = httpx.get(urlpath, headers={"Cookie": sub_jwt_cookie} if sub_user else None)
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
def test_download_localpath(fnames, examples_dir, sub_urlbase, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    name, localpath = fnames
    ds = myroot[name]
    with chdir_ctxt(tmp_path):
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
    with chdir_ctxt(tmp_path):
        b = blosc2.open(path)
        np.testing.assert_array_equal(a[:], b[:])


def test_download_regular_file(examples_dir, sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot["README.md"]
    with chdir_ctxt(tmp_path):
        path = ds.download()
        assert path == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read()
    with chdir_ctxt(tmp_path):
        b = open(path).read()
        assert a[:] == b[:]

    # Using 2-step download
    urlpath = ds.get_download_url()
    assert urlpath == f"{sub_urlbase}/api/fetch/{ds.path}"
    data = httpx.get(urlpath, headers={"Cookie": sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    # TODO: why do we need .decode() here?
    assert a[:] == b[:].decode()


def test_download_public_file(examples_dir, fill_public, tmp_path):
    fnames, mypublic = fill_public
    for fname in fnames:
        # Download the file
        ds = mypublic[fname]
        with chdir_ctxt(tmp_path):
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
def test_upload(fnames, remove, root, examples_dir, sub_urlbase, sub_user, tmp_path):
    if not sub_user:
        pytest.skip("authentication support needed")

    localpath, remotepath = fnames
    remote_root = cat2.Root(root, sub_urlbase, sub_user)
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot[localpath]
    with chdir_ctxt(tmp_path):
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


def test_upload_public_unauthorized(examples_dir, sub_urlbase, sub_user, tmp_path):
    if sub_user:
        pytest.skip("not authentication needed")

    remote_root = cat2.Root("@public", sub_urlbase, sub_user)
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot["README.md"]
    with chdir_ctxt(tmp_path):
        path = ds.download()
        assert path == ds.path
        with pytest.raises(Exception) as e_info:
            _ = remote_root.upload(path)
        assert "Unauthorized" in str(e_info)


@pytest.mark.parametrize("name", ["ds-1d.b2nd", "ds-hello.b2frame", "README.md"])
def test_vlmeta(name, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot[name]
    schunk_meta = ds.meta.get("schunk", ds.meta)
    assert ds.vlmeta is schunk_meta["vlmeta"]


def test_vlmeta_data(sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot["ds-sc-attr.b2nd"]
    assert ds.vlmeta == {"a": 1, "b": "foo", "c": 123.456}


### Lazy expressions


def test_lazyexpr(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} + 0"
    operands = {opnm: oppt}
    lxname = "my_expr"

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase, auth_cookie=sub_jwt_cookie)
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check result metadata.
    lxinfo = cat2.get_info(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxinfo["shape"] == opinfo["shape"]
    assert lxinfo["dtype"] == opinfo["dtype"]
    assert lxinfo["expression"] == f"{expression}"
    assert lxinfo["operands"] == operands

    # Check result data.
    a = cat2.fetch(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    b = cat2.fetch(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    np.testing.assert_array_equal(a[:], b[:])


def test_lazyexpr_getchunk(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} - 0"
    operands = {opnm: oppt}
    lxname = "my_expr"

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check for chunksize and dtype
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    chunksize = opinfo["chunks"][0]
    dtype = opinfo["dtype"]

    # Get the first chunks
    chunk_ds = cat2.get_chunk(oppt, 0, sub_urlbase, auth_cookie=sub_jwt_cookie)
    chunk_expr = cat2.get_chunk(lxpath, 0, sub_urlbase, auth_cookie=sub_jwt_cookie)
    # Check data
    out = np.empty(chunksize, dtype=dtype)
    blosc2.decompress2(chunk_ds, out)
    out_expr = np.empty(chunksize, dtype=dtype)
    blosc2.decompress2(chunk_expr, out_expr)
    np.testing.assert_array_equal(out, out_expr)


def test_expr_from_expr(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = "ds"
    oppt = f"{TEST_CATERVA2_ROOT}/ds-1d.b2nd"
    expression = f"{opnm} + 1"
    operands = {opnm: oppt}
    lxname = "my_expr"

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase, auth_cookie=sub_jwt_cookie)
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f"@personal/{lxname}.b2nd")

    expression2 = f"{opnm} * 2"
    operands2 = {opnm: f"@personal/{lxname}.b2nd"}
    lxname = "expr_from_expr"
    lxpath2 = cat2.lazyexpr(lxname, expression2, operands2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath2 == pathlib.Path(f"@personal/{lxname}.b2nd")

    # Check result metadata.
    lxinfo = cat2.get_info(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxinfo2 = cat2.get_info(lxpath2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxinfo["shape"] == opinfo["shape"] == lxinfo2["shape"]
    assert lxinfo["dtype"] == opinfo["dtype"] == lxinfo2["dtype"]
    assert lxinfo["expression"] == f"{expression}"
    assert lxinfo2["expression"] == f"{expression2}"
    assert lxinfo["operands"] == operands
    assert lxinfo2["operands"] == operands2

    # Check result data.
    a = cat2.fetch(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    b = cat2.fetch(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    c = cat2.fetch(lxpath2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    np.testing.assert_array_equal(a[:] + 1, b[:])
    np.testing.assert_array_equal((a[:] + 1) * 2, c[:])


# User management


def test_adduser(sub_urlbase, sub_user, sub_jwt_cookie):
    if not sub_user:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    message = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
    assert "User added" in message
    with cat2.c2context(urlbase=sub_urlbase, username=username, password=password):
        lusers = cat2.listusers()
        assert username in [user["email"] for user in lusers]
    # Delete the user for future tests
    message = cat2.deluser(username, auth_cookie=sub_jwt_cookie)
    assert "User deleted" in message


def test_adduser_malformed(sub_user, sub_jwt_cookie):
    if not sub_user:
        pytest.skip("authentication support needed")

    username = "test_noat_user"
    password = "testpassword"
    is_superuser = False
    with pytest.raises(Exception) as e_info:
        _ = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
    print(e_info)
    assert "Bad Request" in str(e_info)


def test_adduser_maxexceeded(sub_user, sub_jwt_cookie, configuration):
    if not sub_user:
        pytest.skip("authentication support needed")

    # TODO: make this to work; currently this returns None
    # maxusers = configuration.get("subscriber.maxusers")
    # For now, keep in sync with subscriber.maxusers in caterva2/tests/caterva2-login.toml
    maxusers = 5
    # Add maxusers users; we already have one user, so the next loop should fail
    # when reaching the creation of last user
    for n in range(maxusers):
        # This should work fine for n < maxusers
        username = f"test{n}@user.com"
        password = "testpassword"
        is_superuser = False
        if n == maxusers - 1:  # we already have one user
            with pytest.raises(Exception) as e_info:
                _ = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
            assert "Bad Request" in str(e_info.value)
        else:
            message = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
            assert "User added" in message
    # Remove the created users
    for m in range(n):
        username = f"test{m}@user.com"
        message = cat2.deluser(username, auth_cookie=sub_jwt_cookie)
        assert "User deleted" in message
    # Count the current number of users
    data = cat2.listusers(auth_cookie=sub_jwt_cookie)
    assert len(data) == 1


def test_adduser_unauthorized(sub_user, sub_jwt_cookie):
    if sub_user:
        pytest.skip("not authentication needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    with pytest.raises(Exception) as e_info:
        _ = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
    assert "Not Found" in str(e_info)


def test_deluser(sub_user, sub_jwt_cookie):
    if not sub_user:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    message = cat2.adduser(username, password, is_superuser, auth_cookie=sub_jwt_cookie)
    assert "User added" in message
    # Now, delete the user
    message = cat2.deluser(username, auth_cookie=sub_jwt_cookie)
    assert "User deleted" in message
    # Check that the user has been deleted
    with pytest.raises(Exception) as e_info:
        _ = cat2.deluser(username, auth_cookie=sub_jwt_cookie)
    assert "Bad Request" in str(e_info)

    with pytest.raises(Exception) as e_info:  # noqa: SIM117
        with cat2.c2context(urlbase=sub_urlbase, username=username, password=password):
            _ = 0


def test_deluser_unauthorized(sub_user, sub_jwt_cookie):
    if sub_user:
        pytest.skip("not authentication needed")

    username = "test@user.com"
    with pytest.raises(Exception) as e_info:
        _ = cat2.deluser(username, auth_cookie=sub_jwt_cookie)
    assert "Not Found" in str(e_info)


def test_listusers(sub_user, sub_jwt_cookie):
    if not sub_user:
        pytest.skip("authentication support needed")

    username = "test@user.com"
    password = "testpassword"
    is_superuser = False
    with cat2.c2context(auth_cookie=sub_jwt_cookie):
        message = cat2.adduser(username, password, is_superuser)
        assert "User added" in message
    # List users
    data = cat2.listusers(auth_cookie=sub_jwt_cookie)
    assert username in [user["email"] for user in data]
    # Delete the user
    with cat2.c2context(auth_cookie=sub_jwt_cookie):
        message = cat2.deluser(username)
        assert "User deleted" in message
    # List users again
    data = cat2.listusers(auth_cookie=sub_jwt_cookie)
    assert username not in [user["email"] for user in data]


def test_listusers_unauthorized(sub_user, sub_jwt_cookie):
    if sub_user:
        pytest.skip("not authentication needed")

    with pytest.raises(Exception) as e_info:
        _ = cat2.listusers(auth_cookie=sub_jwt_cookie)
    assert "Not Found" in str(e_info)


# Depending on sub_user fixture is an easy way to start
# the server in a subprocess with the correct configuration
# (useful for running this test in isolation)
def test_c2context_demo(sub_user):
    urlbase = "https://demo.caterva2.net"
    expected_roots = cat2.get_roots(urlbase)
    root = "example"
    cat2.subscribe(root, urlbase)
    expected_paths = cat2.get_list(root, urlbase)
    path = pathlib.Path(root + "/dir1/ds-3d.b2nd")
    expected_info = cat2.get_info(path, urlbase)

    with cat2.c2context(urlbase=urlbase):
        roots = cat2.get_roots()
        assert len(roots) == len(expected_roots)
        assert all(root_ in expected_roots for root_ in roots)
        assert cat2.subscribe(root) == "Ok"
        paths_list = cat2.get_list(root)
        assert paths_list == expected_paths
        info = cat2.get_info(path)
        assert info == expected_info
        a = cat2.fetch(path)
        chunk = cat2.get_chunk(path, 0)
        local_path = cat2.download(path)
        b = blosc2.open(local_path, "r")
        np.testing.assert_array_equal(a, b[:])
        assert chunk == b.get_chunk(0)

        rootobj = cat2.Root(root)
        assert paths_list == rootobj.file_list
        dataset = rootobj["dir1/ds-3d.b2nd"]
        download_path = dataset.download()
        c = blosc2.open(download_path, "r")
        np.testing.assert_array_equal(dataset[:], c[:])
        assert chunk == c.get_chunk(0)

    roots_default = ["@public", "foo", "hdf5root"]
    roots = cat2.get_roots()
    assert len(roots) == len(roots_default)
    assert all(root in roots_default for root in roots)


def c2sub_user(urlbase):
    def rand32():
        return random.randint(0, 0x7FFFFFFF)

    username = f"user+{rand32():x}@example.com"
    password = hex(rand32())

    for _ in range(3):
        resp = httpx.post(
            f"{urlbase}/auth/register", json={"email": username, "password": password}, timeout=15
        )
        if resp.status_code != 400:
            break
        # Retry on possible username collision.
    resp.raise_for_status()

    return (
        username,
        password,
        cat2.get_auth_cookie(urlbase, {"urlbase": urlbase, "username": username, "password": password}),
    )


@pytest.mark.parametrize(
    "cookie",
    [
        True,
        False,
    ],
)
def test_c2context_demo_auth(cookie, sub_urlbase, sub_user, tmp_path):
    urlbase = "https://cat2.cloud/demo"
    username, password, auth_cookie = c2sub_user(urlbase)
    auth_cookie_ = auth_cookie
    username_ = username
    password_ = password
    if cookie:
        username_ = password_ = None
    else:
        auth_cookie_ = None
    expected_roots = cat2.get_roots(urlbase, auth_cookie)
    expected_roots_list = list(expected_roots.keys())

    localpath, remotepath = ("root-example/dir1/ds-2d.b2nd", "dir2/dir3/dir4/ds-2d2.b2nd")
    root = "@personal"
    remote_root = cat2.Root(root, urlbase, {"username": username, "password": password})
    remote_root.upload(localpath, remotepath)
    expected_paths = cat2.get_list(root, urlbase, auth_cookie)
    path = pathlib.Path(root + "/" + expected_paths[-1])
    expected_info = cat2.get_info(path, urlbase, auth_cookie)

    with cat2.c2context(urlbase=urlbase, username=username_, password=password_, auth_cookie=auth_cookie_):
        roots = cat2.get_roots()
        assert len(roots) == len(expected_roots)
        assert all(root_ in expected_roots for root_ in roots)
        assert cat2.subscribe(expected_roots_list[-1]) == "Ok"
        paths_list = cat2.get_list(root)
        assert paths_list == expected_paths
        info = cat2.get_info(path)
        assert info == expected_info
        a = cat2.fetch(path)
        chunk = cat2.get_chunk(path, 0)
        local_path = cat2.download(path)
        b = blosc2.open(local_path, "r")
        np.testing.assert_array_equal(a[:], b[:])
        assert chunk == b.get_chunk(0)
        # Root and File
        root_personal = cat2.Root(root)
        assert paths_list == root_personal.file_list
        dataset = root_personal[expected_paths[-1]]
        download_path = dataset.download()
        c = blosc2.open(download_path, "r")
        np.testing.assert_array_equal(dataset[:], c[:])
        assert chunk == c.get_chunk(0)
        # expr
        expr_path = cat2.lazyexpr("expr", "a+1", {"a": path})
        res = cat2.fetch(expr_path)
        np.testing.assert_array_equal(a[:] + 1, res[:])

        # upload
        localpath = "root-example/ds-2d-fields.b2nd"
        remotepath = root + "/dir2/ds-2d-fields.b2nd"
        remote_ds = cat2.upload(localpath, remotepath)
        # move
        new_remotepath = root + "/dir4/ds-2d-fields.b2nd"
        newpath = cat2.move(remote_ds, new_remotepath)
        assert str(newpath) == new_remotepath
        # copy
        copy_remotepath = root + "/dir4/ds-2d-fields-copy.b2nd"
        copy_newpath = cat2.copy(newpath, copy_remotepath)
        assert str(copy_newpath) == copy_remotepath
        # remove
        remote_removed = pathlib.Path(cat2.remove(copy_newpath))
        assert remote_removed == copy_newpath
        # Check that the file has been removed
        with pytest.raises(Exception) as e_info:
            _ = cat2.remove(copy_newpath)
        assert "Not Found" in str(e_info.value)

    roots_default = ["@public", "foo", "hdf5root"]
    roots = cat2.get_roots()
    assert len(roots) == len(roots_default)
    assert all(root in roots_default for root in roots)
