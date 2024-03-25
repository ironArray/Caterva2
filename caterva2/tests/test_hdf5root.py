###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import blosc2
import numpy as np
import pytest

import caterva2 as cat2

from .services import TEST_HDF5_ROOT


hdf5root = pytest.importorskip('caterva2.services.hdf5root',
                               reason="HDF5 support not present")


@pytest.fixture
def sub_host(services):
    return services.get_endpoint('subscriber')


@pytest.fixture
def api_root(sub_host):
    return cat2.Root(TEST_HDF5_ROOT, host=sub_host)


def test_not_unsupported(api_root):
    for node in api_root.node_list:
        assert not node.startswith('unsupported/')


def test_ds_name_ext(api_root):
    for node in api_root.node_list:
        node.endswith('.b2nd')  # no other conversions supported yet


def test_scalar(api_root):
    ds = api_root['scalar.b2nd']
    v = ds[()]
    assert v.dtype.kind == 'f'
    assert v == pytest.approx(123.456)


def test_string(api_root):
    ds = api_root['string.b2nd']
    v = ds[()]
    assert v.dtype.kind == 'S'
    assert v == b'Hello world!'


def test_nonchunked(api_root):
    ds = api_root['arrays/2d-nochunks.b2nd']
    ds_chunks = ds.meta['chunks']
    assert ds_chunks is not None and len(ds_chunks) == 2  # auto chunking
    v = ds[:]
    a = np.arange(100, dtype='complex128').reshape(10, 10)
    a = a + a*1j
    np.testing.assert_array_equal(v, a)


def test_chunked(api_root):
    ds = api_root['arrays/2d-gzip.b2nd']
    ds_chunks = tuple(ds.meta['chunks'])
    assert ds_chunks == (4, 4)  # chunk shape is kept
    v = ds[:]
    a = np.arange(100, dtype='complex128').reshape(10, 10)
    a = a + a*1j
    np.testing.assert_array_equal(v, a)


def test_blosc2(api_root):
    ds = api_root['arrays/3d-blosc2.b2nd']
    ds_chunks = tuple(ds.meta['chunks'])
    assert ds_chunks == (4, 10, 10)  # chunk shape is kept
    # TODO: compression parameters
    # cparams = ds.meta['schunk']['cparams']
    # assert cparams['codec'] == blosc2.Codec.LZ4.value
    # assert cparams['filters'] == [0, 0, 0, 0, 0,
    #                               blosc2.Filter.BITSHUFFLE.value]
    v = ds[:]
    a = np.arange(1000, dtype='uint8').reshape(10, 10, 10)
    np.testing.assert_array_equal(v, a)


@pytest.mark.parametrize("slice_", [slice(None), 1, slice(2, 6),
                                    (slice(None, 6), slice(5, 8), slice(6))])
def test_slicing(api_root, slice_):
    ds = api_root['arrays/3d-blosc2.b2nd']
    v = ds[:]
    a = np.arange(1000, dtype='uint8').reshape(10, 10, 10)
    np.testing.assert_array_equal(v[slice_], a[slice_])


def test_vlmeta(api_root):
    ds = api_root['attrs.b2nd']
    assert len(ds.vlmeta) == 9
    m = ds.vlmeta
    assert m['Int'] == m['IntT'] == 42
    # TODO: consistent conversion of strings
    # assert m['Bin'] == m['BinT'] == b'foo'
    # assert m['Str'] == m['StrT'] == 'bar'
    assert m['Arr'] == m['ArrT'] == [[0, 1], [2, 3]]
    # TODO: consistent conversion of strings
    # assert m['NilBin'] == b''
    # assert m['NilStr'] == ''
    assert m['NilInt'] is None
