from pathlib import Path

import pytest


def get_source_dir():
    return Path(__file__).parent.parent.parent


def get_examples_dir():
    return get_source_dir() / "root-example"


@pytest.fixture(scope="session")
def examples_dir():
    """Directory of read-only example files in Caterva2 source"""
    return get_examples_dir()
