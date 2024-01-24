def main(*args, **kwargs):
    import functools
    # Avoid `RuntimeWarning` about import order of ``__main__``
    # resulting in unpredictable behaviour.
    from caterva2.tests.__main__ import main as tests_main

    functools.update_wrapper(main, tests_main)
    return tests_main(*args, **kwargs)
