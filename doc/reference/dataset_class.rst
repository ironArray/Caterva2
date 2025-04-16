.. _ref-API-Dataset:

Dataset class
=============

A dataset is a Blosc2-encoded file on a root repository (thus a :ref:`File <ref-API-File>`) representing either a flat string of bytes or an n-dimensional array.

.. currentmodule:: caterva2.client.Dataset


Methods
-------

.. autosummary::
    :toctree: autofiles
    :nosignatures:

    __init__
    __getitem__
    slice
    append
    get_download_url
    download
    vlmeta

Attributes
----------

.. autosummary::
    :toctree: autofiles

    shape
    chunks
    blocks
    dtype
