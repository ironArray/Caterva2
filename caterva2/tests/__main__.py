import pathlib
import sys

import pytest


def main(verbose=False):
    """Run the test suite.

    Parameters
    ----------
    verbose : bool
        If True, run the tests in verbose mode.

    Returns
    -------
    int
        Exit code of the test suite.
    """
    test_dir = pathlib.Path(__file__).parent
    verb = "-v" if verbose else ""
    return pytest.main([verb, test_dir])


if __name__ == '__main__':
    main(verbose=('--verbose' in sys.argv or '-v' in sys.argv))
