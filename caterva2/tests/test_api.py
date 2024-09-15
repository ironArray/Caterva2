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

import httpx

import blosc2
import pytest

import caterva2 as cat2
import numpy as np

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
    return services.get_endpoint(f'publisher.{TEST_CATERVA2_ROOT}')


@pytest.fixture
def sub_urlbase(services):
    return services.get_urlbase('subscriber')


@pytest.fixture
def fill_public(examples_dir, sub_urlbase, sub_user):
    # Manually copy some files to the public area (TEST_STATE_DIR)
    fnames = ['ds-1d.b2nd', 'dir1/ds-2d.b2nd']
    for fname in fnames:
        orig = examples_dir / fname
        dest = pathlib.Path(TEST_STATE_DIR) / f'subscriber/public/{fname}'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(orig.read_bytes())
    # We need a user here in case we want to remove files from @public
    mypublic = cat2.Root('@public', sub_urlbase, sub_user)
    return fnames, mypublic


def test_subscribe(sub_urlbase, sub_jwt_cookie):
    assert 'Ok' == cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase)
    assert 'Ok' == cat2.subscribe('@public', sub_urlbase)
    for root in ['@personal', '@shared']:
        if sub_jwt_cookie:
            assert 'Ok' == cat2.subscribe(root, sub_urlbase, auth_cookie=sub_jwt_cookie)
        else:
            with pytest.raises(Exception) as e_info:
                _ = cat2.subscribe(root, sub_urlbase)
            assert 'Unauthorized' in str(e_info)


def test_roots(pub_host, sub_urlbase, sub_jwt_cookie):
    roots = cat2.get_roots(sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert roots[TEST_CATERVA2_ROOT]['name'] == TEST_CATERVA2_ROOT
    assert roots[TEST_CATERVA2_ROOT]['http'] == pub_host
    assert roots['@public']['name'] == '@public'
    assert roots['@public']['http'] == ''
    if sub_jwt_cookie:
        # Special roots (only available when authenticated)
        assert roots['@personal']['name'] == '@personal'
        assert roots['@personal']['http'] == ''
        assert roots['@shared']['name'] == '@shared'
        assert roots['@shared']['http'] == ''


def test_root(sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    assert myroot.name == TEST_CATERVA2_ROOT
    assert myroot.urlbase == sub_urlbase
    mypublic = cat2.Root('@public', sub_urlbase, sub_user)
    assert mypublic.name == '@public'
    assert mypublic.urlbase == sub_urlbase
    if sub_user:
        mypersonal = cat2.Root('@personal', sub_urlbase, sub_user)
        assert mypersonal.name == '@personal'
        assert mypersonal.urlbase == sub_urlbase
        myshared = cat2.Root('@shared', sub_urlbase, sub_user)
        assert myshared.name == '@shared'
        assert myshared.urlbase == sub_urlbase


def test_list(examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    example = examples_dir
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes
    if sub_user:
        mypersonal = cat2.Root('@personal', sub_urlbase, sub_user)
        # In previous tests we have created some files in the personal area
        assert len(mypersonal.node_list) >= 0
        myshared = cat2.Root('@shared', sub_urlbase, sub_user)
        assert set(myshared.node_list) == set()


def test_list_public(fill_public):
    fnames, mypublic = fill_public
    assert set(mypublic.node_list) == set(fnames)


def test_file(sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    file = myroot['README.md']
    assert file.name == 'README.md'
    assert file.urlbase == sub_urlbase

def test_file_public(sub_urlbase, fill_public):
    fnames, mypublic = fill_public
    for fname in fnames:
        file = mypublic[fname]
        assert file.name == fname
        assert file.urlbase == sub_urlbase


@pytest.mark.parametrize("dirpath", [None, 'dir1', 'dir2', 'dir2/dir3/dir4'])
@pytest.mark.parametrize("final_dir", [True, False])
def test_move(dirpath, final_dir, sub_urlbase, sub_user, fill_public):
    if not sub_user:
        return pytest.skip("authentication support needed")

    fnames, mypublic = fill_public
    myshared = cat2.Root('@shared', sub_urlbase, sub_user)
    for fname in fnames:
        file = mypublic[fname]
        if final_dir:
            new_fname = f'{dirpath}' if dirpath else ''
        else:
            new_fname = f'{dirpath}/{fname}' if dirpath else fname
        newpath = file.move(f"{myshared.name}/{new_fname}")
        if final_dir:
            basename = fname.split('/')[-1]
            new_path = f"{new_fname}/{basename}" if dirpath else basename
            assert str(newpath) == f"{myshared.name}/{new_path}"
            assert myshared[new_path].path == newpath
        else:
            assert str(newpath) == f"{myshared.name}/{new_fname}"
            assert myshared[new_fname].path == newpath


# New test for the move method where the destination is not a special root
@pytest.mark.parametrize("dest", ['..', '.', 'foo', 'foo/bar'])
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
        assert 'Bad Request' in str(e_info)

@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(10, 20, 1)])
def test_index_dataset_frame(slice_, examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
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
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
    assert ds.urlbase == sub_urlbase
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        _ = ds[::2]
        assert str(e_info.value) == 'Only step=1 is supported'


@pytest.mark.parametrize(
    "slice_",
    [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None), slice(1, 5, 1)],
)
def test_index_dataset_1d(slice_, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot['ds-1d.b2nd']
    assert ds.name == 'ds-1d.b2nd'
    assert ds.urlbase == sub_urlbase

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1), (slice(None, 10), slice(None, 20))])
@pytest.mark.parametrize("name", ['dir1/ds-2d.b2nd', 'dir2/ds-4d.b2nd'])
def test_index_dataset_nd(slice_, name, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1)])
def test_index_regular_file(slice_, examples_dir, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot['README.md']

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read().encode()
    if isinstance(slice_, int):
        assert ord(ds[slice_]) == a[slice_]  # TODO: why do we need ord() here?
        assert ord(ds.fetch(slice_)) == a[slice_]
    else:
        assert ds[slice_] == a[slice_]
        assert ds.fetch(slice_) == a[slice_]


@pytest.mark.parametrize("name", ['ds-1d.b2nd', 'dir1/ds-2d.b2nd'])
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
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.ndarray_from_cframe(data.content)
    np.testing.assert_array_equal(a[:], b[:])


def test_download_b2frame(examples_dir, sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['ds-hello.b2frame']
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
    assert urlpath == f"{sub_urlbase}api/fetch/{ds.path}"
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
    assert data.status_code == 200
    b = blosc2.schunk_from_cframe(data.content)
    assert a[:] == b[:]


@pytest.mark.parametrize("fnames", [
    ('ds-1d.b2nd', 'ds-1d2.b2nd'),
    ('dir1/ds-2d.b2nd', 'dir2/ds-2d2.b2nd'),
    ('dir1/ds-2d.b2nd', 'dir2/dir3/dir4/ds-2d2.b2nd'),
    ('dir1/ds-2d.b2nd', 'dir2/dir3/dir4/'),
])
def test_download_localpath(fnames, examples_dir, sub_urlbase, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    name, localpath = fnames
    ds = myroot[name]
    with chdir_ctxt(tmp_path):
        if localpath.endswith('/'):
            # Create a directory in localpath
            localpath2 = pathlib.Path(localpath)
            localpath2.mkdir(parents=True, exist_ok=True)
        path = ds.download(localpath)
        if localpath.endswith('/'):
            localpath = localpath + name.split('/')[-1]
        assert str(path) == localpath

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    with chdir_ctxt(tmp_path):
        b = blosc2.open(path)
        np.testing.assert_array_equal(a[:], b[:])


def test_download_regular_file(examples_dir, sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['README.md']
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
    assert urlpath == f"{sub_urlbase}api/fetch/{ds.path}"
    data = httpx.get(urlpath,
                     headers={'Cookie': sub_jwt_cookie} if sub_user else None)
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
            a = open(example, 'rb').read()
            b = open(path, 'rb').read()
            assert a[:] == b[:]


@pytest.mark.parametrize("fnames", [('ds-1d.b2nd', None),
                                    ('ds-hello.b2frame', None),
                                    ('README.md', None),
                                    ('README.md', 'README2.md'),
                                    ('dir1/ds-2d.b2nd', None),
                                    ('dir1/ds-2d.b2nd', 'dir2/ds-2d.b2nd'),
                                    ('dir1/ds-2d.b2nd', 'dir2/dir3/dir4/ds-2d2.b2nd'),
                                    ('dir1/ds-3d.b2nd', 'dir2/dir3/dir4/'),
                                    ])
@pytest.mark.parametrize("root", ['@personal', '@shared', '@public'])
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
        assert path.exists() and path.is_file()
        # Now, upload the file to the remote root
        remote_ds = remote_root.upload(path, remotepath)
        # Check whether the file has been uploaded with the correct name
        if remotepath:
            if remotepath.endswith('/'):
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
            assert 'Not Found' in str(e_info.value)


def test_upload_public_unauthorized(examples_dir, sub_urlbase, sub_user, tmp_path):
    if sub_user:
        pytest.skip("not authentication needed")

    remote_root = cat2.Root("@public", sub_urlbase, sub_user)
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['README.md']
    with chdir_ctxt(tmp_path):
        path = ds.download()
        assert path == ds.path
        with pytest.raises(Exception) as e_info:
            _ = remote_root.upload(path)
        assert 'Unauthorized' in str(e_info)

@pytest.mark.parametrize("name", ['ds-1d.b2nd',
                                  'ds-hello.b2frame',
                                  'README.md'])
def test_vlmeta(name, sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot[name]
    schunk_meta = ds.meta.get('schunk', ds.meta)
    assert ds.vlmeta is schunk_meta['vlmeta']


def test_vlmeta_data(sub_urlbase):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase)
    ds = myroot['ds-sc-attr.b2nd']
    assert ds.vlmeta == dict(a=1, b="foo", c=123.456)


### Lazy expressions

def test_lazyexpr(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = 'ds'
    oppt = f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'
    expression = f'{opnm} + 0'
    operands = {opnm: oppt}
    lxname = 'my_expr'

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase, auth_cookie=sub_jwt_cookie)
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase,
                           auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f'@personal/{lxname}.b2nd')

    # Check result metadata.
    lxinfo = cat2.get_info(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxinfo['shape'] == opinfo['shape']
    assert lxinfo['dtype'] == opinfo['dtype']
    assert lxinfo['expression'] == f'({expression})'.replace(opnm, 'o0')
    assert lxinfo['operands'] == dict(o0=operands[opnm])

    # Check result data.
    a = cat2.fetch(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    b = cat2.fetch(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    np.testing.assert_array_equal(a[:], b[:])


def test_lazyexpr_getchunk(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = 'ds'
    oppt = f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'
    expression = f'{opnm} - 0'
    operands = {opnm: oppt}
    lxname = 'my_expr'

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase,
                           auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f'@personal/{lxname}.b2nd')

    # Get one chunk
    chunk_ds = cat2.get_chunk(oppt, 0, sub_urlbase, auth_cookie=sub_jwt_cookie)
    chunk_expr = cat2.get_chunk(lxpath, 0, sub_urlbase, auth_cookie=sub_jwt_cookie)

    # Check result data.
    a = cat2.fetch(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    b = cat2.fetch(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    np.testing.assert_array_equal(a[:], b[:])
    out = np.empty_like(a[:])
    blosc2.decompress2(chunk_ds, out)
    out_expr = np.empty_like(a[:])
    blosc2.decompress2(chunk_expr, out_expr)
    np.testing.assert_array_equal(out, out_expr)


def test_expr_from_expr(sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = 'ds'
    oppt = f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'
    expression = f'{opnm} + 1'
    operands = {opnm: oppt}
    lxname = 'my_expr'

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase,
                   auth_cookie=sub_jwt_cookie)
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase,
                           auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f'@personal/{lxname}.b2nd')

    expression2 = f'{opnm} * 2'
    operands2 = {opnm: lxpath}
    lxname = 'expr_from_expr'
    lxpath2 = cat2.lazyexpr(lxname, expression2, operands2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath2 == pathlib.Path(f'@personal/{lxname}.b2nd')

    # Check result metadata.
    lxinfo = cat2.get_info(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxinfo2 = cat2.get_info(lxpath2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxinfo['shape'] == opinfo['shape'] == lxinfo2['shape']
    assert lxinfo['dtype'] == opinfo['dtype'] == lxinfo2['dtype']
    assert lxinfo['expression'] == f'({expression})'.replace(opnm, 'o0')
    assert lxinfo2['expression'] == '((o0 + 1) * 2)'
    assert lxinfo['operands'] == dict(o0=operands[opnm])
    assert lxinfo2['operands'] == dict(o0=operands[opnm])

    # Check result data.
    a = cat2.fetch(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    b = cat2.fetch(lxpath, sub_urlbase, auth_cookie=sub_jwt_cookie)
    c = cat2.fetch(lxpath2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    np.testing.assert_array_equal(a[:] + 1, b[:])
    np.testing.assert_array_equal((a[:] + 1) * 2, c[:])
