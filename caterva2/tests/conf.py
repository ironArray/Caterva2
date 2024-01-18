import pytest

from caterva2 import utils


def get_configuration():
    return utils.get_conf()


@pytest.fixture(scope='session')
def configuration():
    """Caterva2 configuration, if available"""
    return get_configuration()
