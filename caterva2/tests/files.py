import pytest

from pathlib import Path


def get_source_dir():
    return Path(__file__).parent.parent.parent


@pytest.fixture(scope='session')
def source_dir():
    """Directory of Caterva2 source files"""
    return get_source_dir()


def get_examples_dir(source_dir):
    return source_dir / 'root-example'


@pytest.fixture(scope='session')
def examples_dir(source_dir):
    """Directory of read-only example files in Caterva2 source"""
    return get_examples_dir(source_dir)
