.. _ref-API-Client:

Client class
============

A client is a remote repository. It is the main entry point for using the Caterva2 API.

.. currentmodule:: caterva2


.. autoclass:: Client
    :members:
    :exclude-members: get, get_roots, get_list, fetch, get_chunk, download, upload, adduser, deluser, listusers, lay_out, fill_chunk, written_chunks, publish

    :Special Methods:
    .. autosummary::

       __init__
       get
       get_roots
       get_list
       fetch
       get_chunk
       download
       upload
       adduser
       deluser
       listusers
       lay_out
       fill_chunk
       written_chunks
       publish


    Constructor
    ----------
    .. automethod:: __init__


    Getting roots, files, datasets
    ----------------------------------------------
    .. automethod:: get
    .. automethod:: get_roots
    .. automethod:: get_list


    Fetch / download / upload datasets
    ----------------------------------
    .. automethod:: fetch
    .. automethod:: get_chunk
    .. automethod:: download
    .. automethod:: upload


    Filling an array from several writers
    -------------------------------------
    An array is laid out empty and then filled a chunk at a time, by as many
    processes at once as it has chunks.  Every chunk of a laid-out array is a
    slot nothing has been written to; a writer claims one by writing it, and a
    second write to the same slot is refused, so two writers that both believe
    they own a chunk are resolved by the array rather than by anything either of
    them holds.  Where the server is configured to publish, the array is copied
    out as one finished frame as soon as its last chunk lands.

    .. automethod:: lay_out
    .. automethod:: fill_chunk
    .. automethod:: written_chunks
    .. automethod:: publish


    User management
    ---------------
    .. automethod:: adduser
    .. automethod:: deluser
    .. automethod:: listusers


    Utility methods
    ---------------
