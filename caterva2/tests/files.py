import pytest

from pathlib import Path

try:
    from caterva2.services import hdf5root
except ImportError:
    hdf5root = None


def get_source_dir():
    return Path(__file__).parent.parent.parent


def get_examples_dir():
    return get_source_dir() / 'root-example'


@pytest.fixture(scope='session')
def examples_dir():
    """Directory of read-only example files in Caterva2 source"""
    return get_examples_dir()


@pytest.fixture(scope='session')
def examples_hdf5(tmp_path_factory):
    """HDF5 file with example datasets"""
    if hdf5root is None:
        return None
    h5fpath = tmp_path_factory.mktemp('hdf5') / 'root-example.h5'
    hdf5root.create_example_root(h5fpath)
    return h5fpath
