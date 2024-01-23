Installation
============
You can install Python-Blosc2 wheels via PyPI using Pip or clone the GitHub repository.

Pip
+++

.. code-block::

    python -m pip install caterva2

If you plan to run tests, you may install with the ``tests`` extra:

.. code-block::

    python -m pip install caterva2[tests]

Source code
+++++++++++

.. code-block:: console

    git clone https://github.com/Blosc/caterva2
    cd caterva2
    python -m build
    python -m pip install dist/caterva2-*.whl

If you plan to run tests, you may also add ``[tests]`` right after the wheel name.

That's all. You can proceed with testing section now.

Testing
-------

After installing, you can quickly check that the package is sane by
running the tests:

.. code-block:: console

    pytest --pyargs caterva2.tests -v  # or "python -m caterva2.tests -v"
