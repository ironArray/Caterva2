.. _ref-API-Client:

Client class
============

A client is a remote repository that can be subscribed to. It is the main entry point for using the Caterva2 API.

.. currentmodule:: caterva2


.. autoclass:: Client
    :members:
    :exclude-members: get, get_roots, get_list, subscribe, fetch, get_chunk, download, upload, adduser, deluser, listusers, lazyexpr

    :Special Methods:
    .. autosummary::

       __init__
       get
       get_roots
       get_list
       subscribe
       fetch
       get_chunk
       download
       upload
       adduser
       deluser
       listusers
       lazyexpr


    Constructor
    ----------
    .. automethod:: __init__


    Getting roots, files, datasets, subscribing...
    ----------------------------------------------
    .. automethod:: get
    .. automethod:: get_roots
    .. automethod:: get_list
    .. automethod:: subscribe


    Fetch / download / upload datasets
    ----------------------------------
    .. automethod:: fetch
    .. automethod:: get_chunk
    .. automethod:: download
    .. automethod:: upload


    User management
    ---------------
    .. automethod:: adduser
    .. automethod:: deluser
    .. automethod:: listusers


    Evaluating expressions
    ----------------------
    .. automethod:: lazyexpr

    Utility methods
    ---------------
