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
    args = sys.argv.copy()
    if verbose:
        args.append("-v")
    args.append(pathlib.Path(__file__).parent)
    return pytest.main(args)


if __name__ == '__main__':
    main()
