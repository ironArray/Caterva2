REST API Reference
==================

.. toctree::
    :maxdepth: 1

A REST API is provided by the Caterva2 server. It is a simple HTTP API that allows you to interact with the Caterva2 server using standard HTTP methods. The API is designed to be easy to use and understand, and it provides a simple way to access the functionality of the Caterva2 server from any programming language that can make HTTP requests.

Visit the most updated version of the REST API at: https://cat2.cloud/demo/docs.

It is important to note that the REST API is not intended to be used as a replacement for the :doc:`Caterva2 Python client API <index>`. The Python client API provides a more convenient and efficient way to interact with the Caterva2 server, and it is recommended for most use cases. However, the REST API can be useful in certain situations, such as when you need to access the Caterva2 server from a programming language that does not have a Caterva2 client library, or when you need to integrate Caterva2 with other systems that use HTTP.

User attributes
---------------

``GET /api/info/{path}`` includes an ``attrs`` mapping for Blosc2 arrays and
frames, HDF5 dataset leaves, B2Z array leaves, tables, and saved remote proxies.
It contains user attributes without adapter, cache, or fill bookkeeping.
The existing ``schunk.vlmeta`` (or top-level ``vlmeta`` for frames and tables)
remains available for clients that use its protocol information.

Saved remote proxy attributes are read from the carrier's stored snapshot;
serving them does not resolve the remote source or refresh its metadata.

The Python client's ``File.attrs`` and ``Dataset.attrs`` use this mapping from
cached metadata. Python-Blosc2 exposes it through ``C2Array.attrs`` and
``RemoteArray.attrs`` (also available as ``RemoteArray.vlmeta``). These properties
provide read access, not server-side attribute writes. Clients fall back to
legacy variable metadata when ``attrs`` is absent or null; an empty mapping
means the dataset has no public attributes.
