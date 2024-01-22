What is it?
===========

Caterva2 is a distributed system written in Python meant for sharing `Blosc2 <https://www.blosc.org/pages/blosc-in-depth/>`_ datasets among different hosts by using a `publish–subscribe <https://en.wikipedia.org/wiki/Publish–subscribe_pattern>`_ messaging pattern.  Here, publishers categorize datasets into root groups that are announced to the broker and propagated to subscribers.  Also, every subscriber exposes a REST interface that allows clients to access the datasets.

A Caterva2 deployment includes:

- A single **broker** service to help the rest of the system communicate (particularly publishers and subscribers).
- Several **publishers**, each one providing access to a root and the datasets that it contains.
- Several **subscribers**, each one tracking changes in multiple roots and datasets, and caching their data locally for efficient reuse.
- Several **clients**, each one asking a subscriber to track roots and datasets, and accessing dataset data and metadata.

Usually, publishers and subscribers will be apart, maybe in different networks with limited or expensive connectivity between them, while subscribers and clients will be close enough to have very fast and cheap connectivity.  Such a setup ensures that:

- Data can be efficiently distributed among different machines or networks.
- Data is only requested from their sources on demand.
- Data is cached when possible, close to interested parties.

**Note:** Currently, this project is in early alpha stage, and it is not meant for production use yet.
In case you are interested in Caterva2, please contact us at contact@blosc.org.

More information in the `Caterva2 README <https://github.com/Blosc/Caterva2>`_.
