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
   test


Fetch / download datasets
-------------------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    fetch
    download


Utility variables
-----------------

.. autosummary::
   :toctree: autofiles/top_level/

    __version__
    bro_host_default
    pub_host_default
    sub_host_default
