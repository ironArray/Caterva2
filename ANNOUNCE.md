Announcing Caterva2 0.1
=======================

This is the first public release of Caterva2, a high-performance storage
system for sharing multidimensional arrays compressed with Blosc2.

For more info, you can have a look at the release notes in:

https://github.com/Blosc/Caterva2/releases

More info and examples are available in the README:

https://github.com/Blosc/Caterva2#readme

## What is it?

Caterva2 is a distributed system for on-demand access to remote Blosc2 data repositories.
It uses a [publish–subscribe](https://en.wikipedia.org/wiki/Publish–subscribe_pattern)
messaging pattern, where publishers categorize datasets in root groups that are announced
to the broker and the subscribers. Also, every subscriber exposes a REST interface that allows
clients to access the datasets.

Caterva2 is distributed using the AGPL license, see
https://github.com/Blosc/Caterva2/blob/main/LICENSE.txt
for details.

## People

Caterva2 is mainly developed and maintained by the Blosc Development Team:

* Francesc Alted: main specifications, implementation and maintenance
* J. David Ibáñez: pubsub design and initial implementation
* Ivan Vilata: ideas, implementation and maintenance
* Marta Iborra: ideas, implementation and maintenance

## Mastodon feed

Please follow https://fosstodon.org/@Blosc2 to get informed about the latest
developments.

- The Blosc Development Team

  **We make compression better**
