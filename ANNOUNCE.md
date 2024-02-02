Announcing Caterva2 0.1
=======================

This is the first public release of Caterva2, a high-performance storage
system for sharing multidimensional arrays compressed with Blosc2.

For more info, you can have a look at the release notes in:

https://github.com/Blosc/Caterva2/releases

More info and examples are available in the README:

https://github.com/Blosc/Caterva2#readme

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
