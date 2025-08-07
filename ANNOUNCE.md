Announcing Caterva2 2025.8.7
=============================

Caterva2 is a high-performance storage and computation system for
Blosc2 data repositories.

This is a major release that includes a big refactoring for getting rid of
the PubSub code (which, to be frank, was not bringing too much benefit).
We have added a new `cat2agent` to watch a directory and sync changes to a
Caterva2 server.

Finally, fixed a few bugs and added some new features, such as the
`cat2agent` command line client, which allows you to watch a directory and
sync changes to a Caterva2 server. This is particularly useful for
automatically uploading new datasets to a Caterva2 server, or for keeping
a local copy of a remote Caterva2 server.

For more info, you can have a look at the release notes in:

https://github.com/ironArray/Caterva2/releases

More info and examples are available in the README:

https://github.com/ironArray/Caterva2#readme

## What is it?

Caterva2 is a server written in Python meant for sharing Blosc2 and HDF5
datasets.  There are several interfaces to Caterva2, including a web GUI,
a REST API, a Python API, and a command-line client. With Caterva2, you can
easily share your datasets with your colleagues, or even the public.

Caterva2 is distributed using the AGPL license, see
https://github.com/ironArray/Caterva2/blob/main/LICENSE.txt
for details.

## Follow us

We send announcements on Mastodon: https://mastodon.social/@ironArray,
and LinkedIn: https://www.linkedin.com/company/77649425/admin/feed/posts/

You can contact us at: https://ironarray.io


-- The ironArray Team
   Make compression better
