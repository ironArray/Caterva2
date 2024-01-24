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
    args.append("--pyargs")
    args.append(__package__)
    return pytest.main(args)


if __name__ == '__main__':
    main()
