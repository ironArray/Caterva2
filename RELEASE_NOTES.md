# Release notes

## Changes from 0.1 to 0.1.1

#XXX version-specific blurb XXX#

* Support several implementations for the publisher root, besides a Caterva2 directory with Blosc2 and plain files (PR #27).
* Add HDF5 publisher root implementation to allow mounting an HDF5 file as a root.  Datasets are converted to Blosc2-compressed arrays (with `.b2nd` extension), with chunks and attributes converted on-the-fly (avoiding re-compression where possible).  This obviates the need to previously use `cat2import`, with which it shares all conversion code (PR #28, PR #30).
* New `caterva2.api.File.vlmeta` mapping to access a dataset's variable-length metalayers (i.e. user attributes encoded using msgpack).
* New `cat2export` tool to convert the dataset hierarchy in a Caterva2 directory root into an HDF5 file (with default Blosc2 compression parameters).  Chunk shapes are respected and chunks are not re-compressed when possible (PR #24).
* `cat2import` now operates chunk-by-chunk, respecting compression parameters and avoiding re-compression when possible (PR #26).
* `cat2import` now supports numeric, array & empty HDF5 dataset attributes, converting them to native Python objects first (PR #30).

## Initial 0.1 release

* First public release.
* Implemented many of the functionality in the [specifications](SPECS.md) file.
* Simple API for creating new clients.
* Tests for both clients and servers.
* Simple web client.
* Basic documentation.
* A client (`cat2cli`) to query datasets.
* A tool (`cat2import`) for importing datasets.
