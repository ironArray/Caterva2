Installation
============
You can install Caterva2 wheels via PyPI using Pip or clone the GitHub repository.

Pip
+++

.. code-block::

    python -m pip install caterva2

If you intend to run Caterva2 service or client programs, you may enable those extra features like:

.. code-block::

    python -m pip install caterva2[services,clients]

For running the test suite, you may add ``tests`` to the extras list.

Source code
+++++++++++

.. code-block:: console

    git clone https://github.com/Blosc/caterva2
    cd caterva2
    python -m build
    python -m pip install dist/caterva2-*.whl

You may also enable extra features after the wheel file name (for instance, ``[tests]`` to run tests).

That's all. You can proceed with testing section now.

Testing
-------

After installing, you can quickly check that the package is sane by
running the tests:

.. code-block:: console

    python -m caterva2.tests -v
