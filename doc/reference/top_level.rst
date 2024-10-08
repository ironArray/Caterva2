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



Fetch / download / upload datasets
----------------------------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    fetch
    get_chunk
    download
    upload

User management
---------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    adduser
    deluser
    listusers


Utility functions
-----------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    get_auth_cookie
    copy
    move
    remove


Evaluating expressions
----------------------

.. autosummary::
   :toctree: autofiles/top_level/
   :nosignatures:

    lazyexpr


Utility variables
-----------------

Variables listed below as coming from the ``api`` module are available from the top level module too.

.. autosummary::
   :toctree: autofiles/top_level/

    __version__
    api.bro_host_default
    api.pub_host_default
    api.sub_host_default
    api.sub_urlbase_default

Helper functions
----------------

These functions from the ``api_utils`` module may ease the use of the top level API.

.. autosummary::
   :toctree: autofiles/top_level/

   api_utils.get_auth_cookie
