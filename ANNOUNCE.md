Announcing Caterva2 2025.6.26
=============================

Caterva2 is a high-performance storage and computation system for
Blosc2 data repositories.

This is a major release that includes native support for HDF5 datasets.
Also, the tomography viewer has been improved to support navigation
across different axes, not just the first one.  In addition, we have
added new `concatenate` and `stack` API methods to mirror new Blosc2
capabilities.

For more info, you can have a look at the release notes in:

https://github.com/ironArray/Caterva2/releases

More info and examples are available in the README:

https://github.com/ironArray/Caterva2#readme

## What is it?

Caterva2 is a distributed system written in Python meant for sharing Blosc2
datasets among different hosts by using a publish–subscribe messaging pattern
(see https://en.wikipedia.org/wiki/Publish–subscribe_pattern).  Here,
publishers categorize datasets into root groups that are announced to the
broker and propagated to subscribers.

Subscribers can access datasets of publishers on demand on behalf of clients,
and cache them locally. This could be particularly useful for accessing remote
datasets and sharing them within a local network, thereby optimizing
communication and storage resources within work groups.

Caterva2 is distributed using the AGPL license, see
https://github.com/ironArray/Caterva2/blob/main/LICENSE.txt
for details.

## Follow us

You can follow us on Mastodon: https://mastodon.social/@ironArray,
LinkedIn: https://www.linkedin.com/company/77649425/admin/feed/posts/
or on Twitter: https://twitter.com/ironArray

You can contact us at: https://ironarray.io


-- The ironArray Team
   Make compression better
