What is it?
===========

Caterva2 is a distributed system written in Python meant for sharing `Blosc2 <https://www.blosc.org/pages/blosc-in-depth/>`_ datasets among different hosts by using a `publish–subscribe <https://en.wikipedia.org/wiki/Publish–subscribe_pattern>`_ messaging pattern.  Here, publishers categorize datasets into root groups that are announced to the broker and propagated to subscribers.  Also, every subscriber exposes a REST interface that allows clients to access the datasets.

**Note:** Currently, this project is in early alpha stage, and it is not meant for production use yet.
In case you are interested in Caterva2, please contact us at contact@blosc.org.

More information in the `Caterva2 README <https://github.com/Blosc/Caterva2>`_.
