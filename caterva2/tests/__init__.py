def main(*args, **kwargs):
    """An alias for `caterva2.tests.__main__.main()`."""
    # Avoid `RuntimeWarning` about import order of ``__main__``
    # resulting in unpredictable behaviour.
    from caterva2.tests.__main__ import main as tests_main
    return tests_main(*args, **kwargs)
