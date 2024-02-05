.. _ref-API-Dataset:

Dataset class
=============

A dataset is a Blosc2-encoded file on a root repository (thus a :ref:`File <ref-API-File>`) representing either a flat string of bytes or an n-dimensional array.

.. currentmodule:: caterva2
.. autosummary::
    :toctree: autofiles

    Dataset
    Dataset.__getitem__
    Dataset.get_download_url
    Dataset.fetch
    Dataset.download
