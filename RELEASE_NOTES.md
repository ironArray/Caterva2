# Release notes

## Changes from 0.1 to 0.2

* Publisher: Support several implementations for the publisher root, besides a Caterva2 directory with Blosc2 and plain files (PR #27).  A publisher root module can be run as a script to generate an example root of that kind.
* Publisher: Add HDF5 publisher root implementation to allow mounting an HDF5 file as a root.  Datasets are converted to Blosc2-compressed arrays (with `.b2nd` extension), with chunks and attributes converted on-the-fly (avoiding re-compression where possible).  This obviates the need to use `cat2import`, with which it shares all conversion code (PR #28, PR #29, PR #30).  Adapt test machinery (PR #32).
* Web client: Allow listing the datasets of multiple roots.
* Web client: Add support for display plugins that depend on the `contenttype` attribute of the dataset.
* Web client: Add tomography display plugin that allows choosing and rendering any 2D image in a 3D stack (along the first dimension).
* Web client: Use tabs to switch between dataset info and other features (like displays).
* Web client: Show dataset vlmeta (user attributes) in info tab.
* Client API: New `caterva2.api.File.vlmeta` mapping to access a dataset's variable-length metalayers (i.e. user attributes encoded using msgpack).  Add example file with vlmeta.
* Subscriber: Include vlmeta when caching a dataset.
* Install: Add `hdf5` and `blosc2-plugins` extras to enable HDF5-related functionality (for services and tools), and selected Blosc2 plugins for JPEG 2000 support (for services and clients).
* Add `test-images/encode-grok-numbers.py` script to test exporting a stack of images (tomography) into an HDF5 file using Blosc2 with the Grok JPEG 2000 codec.
* Tools: New `cat2export` tool to convert the dataset hierarchy in a Caterva2 directory root into an HDF5 file (with default Blosc2 compression parameters).  Chunk shapes are respected and chunks are not re-compressed when possible (PR #24).
* Tools: `cat2import` now supports numeric, array & empty HDF5 dataset attributes, converting them to native Python objects first (PR #30).
* Tools: `cat2import` now operates chunk-by-chunk, respecting compression parameters and avoiding re-compression when possible (PR #26).
* Subscriber: Undo root being part of dataset path in clients (PR #25).
* Subscriber: Allow to run when a publisher is unreachable.
* CI: Test on ARM64 besides AMD64.
* Docs: Assorted enhancements, including diagrams (PR #23).
* Install: Fix minimum required Python version back to 3.10.
* Many other minor fixes.

## Initial 0.1 release

* First public release.
* Implemented many of the functionality in the [specifications](SPECS.md) file.
* Simple API for creating new clients.
* Tests for both clients and servers.
* Simple web client.
* Basic documentation.
* A client (`cat2cli`) to query datasets.
* A tool (`cat2import`) for importing datasets.
