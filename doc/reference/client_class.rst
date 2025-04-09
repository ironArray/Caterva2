.. _ref-API-Client:

Client class
============

A client is a remote repository that can be subscribed to. It is the main entry point for using the Caterva2 API.

.. currentmodule:: caterva2


Constructor
----------

.. autosummary::
    :toctree: autofiles

    Client.__init__


Getting roots, files, datasets, subscribing...
----------------------------------------------

.. autosummary::
    :toctree: autofiles

    Client.get
    Client.get_roots
    Client.get_list
    Client.subscribe


Fetch / download / upload datasets
----------------------------------

.. autosummary::
    :toctree: autofiles

    Client.fetch
    Client.get_chunk
    Client.download
    Client.upload


User management
---------------

.. autosummary::
    :toctree: autofiles

    Client.adduser
    Client.deluser
    Client.listusers


Utility methods
---------------

.. autosummary::
    :toctree: autofiles

    Client.append
    Client.copy
    Client.move
    Client.remove
    Client.get_info


Evaluating expressions
----------------------

.. autosummary::
    :toctree: autofiles

    Client.lazyexpr
