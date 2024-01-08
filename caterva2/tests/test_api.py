###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#º
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import os

import blosc2
import pytest

import caterva2 as cat2
import numpy as np

from .services import TEST_PUBLISHED_ROOT as published_root


def test_roots(services):
    roots = cat2.get_roots()
    assert roots[published_root]['name'] == published_root
    assert roots[published_root]['http'] == cat2.pub_host_default

def test_root(services):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    assert myroot.name == published_root
    assert myroot.host == cat2.sub_host_default

def test_list(services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    example = examples_dir
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes

def test_file(services):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    file = myroot['README.md']
    assert file.name == 'README.md'
    assert file.host == cat2.sub_host_default


def test_dataset_frame(services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot['ds-hello.b2frame']
    assert ds.name == 'ds-hello.b2frame'
    assert ds.host == cat2.sub_host_default

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    # assert ds[1] == a[1]  # TODO: this test does not work yet
    assert ds[:1] == a[:1]
    assert ds[0:10] == a[0:10]
    assert ds[10:20] == a[10:20]
    assert ds[:] == a
    assert ds[10:20:1] == a[10:20:1]
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        np.testing.assert_array_equal(ds[::2], a[::2])
        assert ds[::2] == a[::2]
        assert str(e_info.value) == 'Only step=1 is supported'

def test_dataset_1d(services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot['ds-1d.b2nd']
    assert ds.name == 'ds-1d.b2nd'
    assert ds.host == cat2.sub_host_default

    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[1], a[1])
    np.testing.assert_array_equal(ds[:1], a[:1])
    np.testing.assert_array_equal(ds[0:10], a[0:10])
    np.testing.assert_array_equal(ds[10:20], a[10:20])
    np.testing.assert_array_equal(ds[:], a)
    np.testing.assert_array_equal(ds[10:20:1], a[10:20:1])
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        np.testing.assert_array_equal(ds[::2], a[::2])
        assert str(e_info.value) == 'Only step=1 is supported'


@pytest.mark.parametrize("name", ['dir1/ds-2d.b2nd', 'dir2/ds-4d.b2nd'])
def test_dataset_nd(name, services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot[name]
    example = examples_dir / ds.name
    a = blosc2.open(example)[:]
    np.testing.assert_array_equal(ds[1], a[1])
    np.testing.assert_array_equal(ds[:1], a[:1])
    np.testing.assert_array_equal(ds[0:10], a[0:10])
    # The next is out of bounds, but it is supported (by numpy too)
    np.testing.assert_array_equal(ds[10:20], a[10:20])
    np.testing.assert_array_equal(ds[:], a)
    np.testing.assert_array_equal(ds[1:5:1], a[1:5:1])
    # We don't support step != 1
    with pytest.raises(Exception) as e_info:
        np.testing.assert_array_equal(ds[::2], a[::2])
        assert str(e_info.value) == 'Only step=1 is supported'

@pytest.mark.parametrize("name", ['ds-1d.b2nd', 'dir1/ds-2d.b2nd'])
def test_download_b2nd(name, services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot[name]
    dsd = ds.download()
    assert dsd == ds.path

    # Data contents
    example = examples_dir / name
    a = blosc2.open(example)
    b = blosc2.open(dsd)
    np.testing.assert_array_equal(a[:], b[:])
    os.unlink(dsd)

def test_download_b2frame(services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot['ds-hello.b2frame']
    dsd = ds.download()
    assert dsd == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = blosc2.open(example)
    b = blosc2.open(dsd)
    assert a[:] == b[:]
    os.unlink(dsd)

def test_download_regular_file(services, examples_dir):
    myroot = cat2.Root(published_root, host=cat2.sub_host_default)
    ds = myroot['README.md']
    dsd = ds.download()
    assert dsd == ds.path

    # Data contents
    example = examples_dir / ds.name
    a = open(example).read()
    b = open(dsd).read()
    assert a[:] == b[:]
    os.unlink(dsd)
