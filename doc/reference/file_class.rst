.. _ref-API-File:

File class
==========

A file is either a Blosc2 dataset or a regular file on a root repository.

.. currentmodule:: caterva2

.. autoclass:: File
    :members:
    :exclude-members: client, urlbase, cookie
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
