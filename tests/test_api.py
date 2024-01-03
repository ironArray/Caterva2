###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################
import pytest

import caterva2 as cat2
import numpy as np
import pathlib


root_default = 'foo'


def test_roots():
    roots = cat2.get_roots()
    assert roots[root_default]['name'] == root_default
    assert roots[root_default]['http'] == cat2.pub_host_default

def test_root():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    assert myroot.name == root_default
    assert myroot.host == cat2.sub_host_default

def test_list():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    example = pathlib.Path(__file__).parent.parent / 'root-example'
    nodes = set(str(f.relative_to(str(example))) for f in example.rglob("*") if f.is_file())
    assert set(myroot.node_list) == nodes

def test_file():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    file = myroot['README.md']
    assert file.name == 'README.md'
    assert file.host == cat2.sub_host_default


def test_dataset_1d():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    ds = myroot['ds-1d.b2nd']
    assert ds.name == 'ds-1d.b2nd'
    assert ds.host == cat2.sub_host_default
    a = np.arange(1000, dtype="int64")
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


def test_dataset_2d():
    myroot = cat2.Root(root_default, host=cat2.sub_host_default)
    ds = myroot['dir1/ds-2d.b2nd']
    a = np.arange(200, dtype="uint16").reshape(10, 20)
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
