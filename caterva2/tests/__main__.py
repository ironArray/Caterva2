import sys

import pytest


def main(verbose=False, args=None):
    """Run the test suite.

    Parameters
    ----------
    verbose : bool
        If True, run the tests in verbose mode.
    args : list[string]
        Arguments to pass to pytest execution.

    Returns
    -------
    int
        Exit code of the test suite.
    """
    args = [] if args is None else args.copy()
    if verbose:
        args.append("-v")
    # This only happens when using this script mechanism to run tests,
    # using ``pytest --pyargs caterva2.tests`` is not affected.
    args.append("-Wignore::pytest.PytestAssertRewriteWarning")
    args.append("--pyargs")
    args.append(__package__)
    return pytest.main(args)


if __name__ == '__main__':
    main(args=sys.argv)
