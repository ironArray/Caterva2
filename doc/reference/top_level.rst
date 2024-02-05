.. _ref-API-top-level:

Top level API
=============

This API is meant to be used from clients. It provides functions for getting roots, subscribing, listing datasets, fetching and downloading datasets.

.. currentmodule:: caterva2


Getting roots, subscribing and listing datasets
-----------------------------------------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

   get_roots
   subscribe
   get_list
   get_info


Fetch / download datasets
-------------------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    fetch
    download


Utility variables
-----------------

Variables listed below as coming from the ``api`` module are available from the top level module too.

.. autosummary::
   :toctree: autofiles/top_level/

    __version__
    api.bro_host_default
    api.pub_host_default
    api.sub_host_default
