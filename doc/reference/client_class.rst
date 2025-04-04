.. _ref-API-Client:

Client class
============

A client is a remote repository that can be subscribed to. It is the main entry point for using the Caterva2 API.

.. currentmodule:: caterva2.client

Constructor
----------

.. autosummary::
    :toctree: autofiles

    Client.__init__


Getting roots, subscribing and listing datasets
-----------------------------------------------

.. autosummary::
    :toctree: autofiles

    Client.get_roots
    Client.subscribe
    Client.get_list
    Client.get_info



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


Utility functions
-----------------

.. autosummary::
    :toctree: autofiles

    Client.copy
    Client.move
    Client.remove
    Client.append


Evaluating expressions
----------------------

.. autosummary::
    :toctree: autofiles

    Client.lazyexpr
