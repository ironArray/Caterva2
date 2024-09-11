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

from .services import TEST_CATERVA2_ROOT
from .. import api_utils


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


def test_roots(services, pub_host, sub_urlbase, sub_jwt_cookie):
    roots = cat2.get_roots(sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert roots[TEST_CATERVA2_ROOT]['name'] == TEST_CATERVA2_ROOT
    assert roots[TEST_CATERVA2_ROOT]['http'] == pub_host
    assert roots['@public']['name'] == '@public'
    assert roots['@public']['http'] == ''
    if sub_jwt_cookie:
        # Special roots (only available when authenticated)
        assert roots['@scratch']['name'] == '@scratch'
        assert roots['@scratch']['http'] == ''
        assert roots['@shared']['name'] == '@shared'
        assert roots['@shared']['http'] == ''


def test_root(services, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    assert myroot.name == TEST_CATERVA2_ROOT
    assert myroot.urlbase == sub_urlbase
    if sub_user:
        myscratch = cat2.Root('@scratch', sub_urlbase, sub_user)
        assert myscratch.name == '@scratch'
        assert myscratch.urlbase == sub_urlbase
        myshared = cat2.Root('@shared', sub_urlbase, sub_user)
        assert myshared.name == '@shared'
        assert myshared.urlbase == sub_urlbase


def test_list(services, examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    example = examples_dir
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes
    if sub_user:
        myscratch = cat2.Root('@scratch', sub_urlbase, sub_user)
        # In previous tests we have created some files in the scratch area
        assert len(myscratch.node_list) >= 0
        myshared = cat2.Root('@shared', sub_urlbase, sub_user)
        assert set(myshared.node_list) == set()


def test_file(services, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    file = myroot['README.md']
    assert file.name == 'README.md'
    assert file.urlbase == sub_urlbase


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(10, 20, 1)])
def test_index_dataset_frame(slice_, services, examples_dir, sub_urlbase,
                             sub_user):
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


def test_dataset_step_diff_1(services, examples_dir, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
    assert ds.urlbase == sub_urlbase
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        _ = ds[::2]
        assert str(e_info.value) == 'Only step=1 is supported'


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1)])
def test_index_dataset_1d(slice_, services, examples_dir, sub_urlbase,
                          sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
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
def test_index_dataset_nd(slice_, name, services, examples_dir, sub_urlbase,
                          sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[slice_], a[slice_])
    np.testing.assert_array_equal(ds.fetch(slice_), a[slice_])


@pytest.mark.parametrize("slice_", [1, slice(None, 1), slice(0, 10), slice(10, 20), slice(None),
                                    slice(1, 5, 1)])
def test_index_regular_file(slice_, services, examples_dir, sub_urlbase,
                            sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
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
def test_download_b2nd(name, services, examples_dir, sub_urlbase,
                       sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
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


def test_download_b2frame(services, examples_dir, sub_urlbase,
                          sub_user, sub_jwt_cookie, tmp_path):
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
def test_download_localpath(fnames, services, examples_dir, sub_urlbase,
                            sub_user, sub_jwt_cookie, tmp_path):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
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


def test_download_regular_file(services, examples_dir, sub_urlbase,
                               sub_user, sub_jwt_cookie, tmp_path):
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


@pytest.mark.parametrize("fnames", [('ds-1d.b2nd', None),
                                    ('ds-hello.b2frame', None),
                                    ('README.md', None),
                                    ('README.md', 'README2.md'),
                                    ('dir1/ds-2d.b2nd', None),
                                    ('dir1/ds-2d.b2nd', 'dir2/ds-2d.b2nd'),
                                    ('dir1/ds-2d.b2nd', 'dir2/dir3/dir4/ds-2d2.b2nd'),
                                    ('dir1/ds-3d.b2nd', 'dir2/dir3/dir4/'),
                                    ])
@pytest.mark.parametrize("root", ['@scratch', '@shared'])
@pytest.mark.parametrize("remove", [False, True])
def test_upload(fnames, remove, root, services, examples_dir,
                sub_urlbase, sub_user, sub_jwt_cookie, tmp_path):
    if not sub_jwt_cookie:
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
                assert str(e_info.value) == 'Not Found'


@pytest.mark.parametrize("name", ['ds-1d.b2nd',
                                  'ds-hello.b2frame',
                                  'README.md'])
def test_vlmeta(name, services, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot[name]
    schunk_meta = ds.meta.get('schunk', ds.meta)
    assert ds.vlmeta is schunk_meta['vlmeta']


def test_vlmeta_data(services, sub_urlbase, sub_user):
    myroot = cat2.Root(TEST_CATERVA2_ROOT, sub_urlbase, sub_user)
    ds = myroot['ds-sc-attr.b2nd']
    assert ds.vlmeta == dict(a=1, b="foo", c=123.456)


### Lazy expressions

def test_lazyexpr(services, sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = 'ds'
    oppt = f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'
    expression = f'{opnm} + 0'
    operands = {opnm: oppt}
    lxname = 'my_expr'

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase,
                   auth_cookie=sub_jwt_cookie)
    opinfo = cat2.get_info(oppt, sub_urlbase, auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase,
                           auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f'@scratch/{lxname}.b2nd')

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


def test_lazyexpr_getchunk(services, sub_urlbase, sub_jwt_cookie):
    if not sub_jwt_cookie:
        pytest.skip("authentication support needed")

    opnm = 'ds'
    oppt = f'{TEST_CATERVA2_ROOT}/ds-1d.b2nd'
    expression = f'{opnm} - 0'
    operands = {opnm: oppt}
    lxname = 'my_expr'

    cat2.subscribe(TEST_CATERVA2_ROOT, sub_urlbase,
                   auth_cookie=sub_jwt_cookie)
    lxpath = cat2.lazyexpr(lxname, expression, operands, sub_urlbase,
                           auth_cookie=sub_jwt_cookie)
    assert lxpath == pathlib.Path(f'@scratch/{lxname}.b2nd')

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


def test_expr_from_expr(services, sub_urlbase, sub_jwt_cookie):
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
    assert lxpath == pathlib.Path(f'@scratch/{lxname}.b2nd')

    expression2 = f'{opnm} * 2'
    operands2 = {opnm: lxpath}
    lxname = 'expr_from_expr'
    lxpath2 = cat2.lazyexpr(lxname, expression2, operands2, sub_urlbase, auth_cookie=sub_jwt_cookie)
    assert lxpath2 == pathlib.Path(f'@scratch/{lxname}.b2nd')

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
