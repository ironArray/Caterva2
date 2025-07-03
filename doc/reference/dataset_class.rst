.. _ref-API-Dataset:

Dataset class
=============

A dataset is a Blosc2-encoded file on a root repository (thus a :ref:`File <ref-API-File>`) representing either a flat string of bytes or an n-dimensional array.


.. currentmodule:: caterva2

.. autoclass:: Dataset
    :members:
    :inherited-members:
    :exclude-members: client, urlbase, cookie
    :show-inheritance:
    :member-order: groupwise

    :Special Methods:
    .. autosummary::
        __init__
        __getitem__

    Constructor
    -----------
    .. automethod:: __init__

    Utility Methods
    -----------
    .. automethod:: __getitem__
