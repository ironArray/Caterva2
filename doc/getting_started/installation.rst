Installation
============
You can install Caterva2 wheels via PyPI using Pip or clone the GitHub repository.

Pip
+++

.. code-block::

    python -m pip install caterva2

Source code
+++++++++++

.. code-block:: console

    git clone https://github.com/Blosc/caterva2
    cd caterva2
    python -m build
    python -m pip install dist/caterva2-*.whl

That's all. You can proceed with testing section now.

Testing
-------

After installing, you can quickly check that the package is sane by
running the tests:

.. code-block:: console

    python -c "import caterva2 as cat2; cat2.test(verbose=True)"
