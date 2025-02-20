# Release notes

## Changes from 2025.02.13 to 2025.02.20

* Use requests instead of httpx to avoid issues with Pyodide.
* Avoid setting urlbase in Pyodide.
* New addnb command in the Prompt box to add a Jupyter notebook to the server.
* New help button for commands in the Prompt box.
* Notebooks are in read-only mode for now (but upload to server is working).

## Changes from 2025.01.30.1 to 2025.02.13

* Better urllib3 transport to run caterva2 in pyodide
* Better integration jupyter lite.
* Support for jupyter menus in new extension jupyter-cat2cloud

## Changes from 2025.01.30 to 2025.01.30.1

* Generate description for the PyPI package.

## Changes from 2024.07.01 to 2025.01.30

### Web frontend
* Support for user registration and login.
* New list/remove/move/copy commands in Prompt box.
* User management commands (adduser/deluser/listusers) added to the Prompt box. Only superusers can use these commands.
* Display file size in list of datasets.
* Much improved display of dataset information.
* Quota information displayed in top bar.
* Support for uploading tar (tar.gz and .tgz) and zip files. The files are extracted in the server automatically.
* Show 500 kind of errors in a more user-friendly way.
* Tables (arrays with structured dtypes) can be sorted by specified fields.  Also, the fields can be selected to be hidden or shown.
* Tables can be filtered by specified fields.
* Hash CSS/JS files to avoid browser cache issues.
* Jupyter notebooks can be uploaded and previewed in the server. We are working on a way to run them too; stay tuned.
* PDF files can be displayed now.
* Support for displaying image files.
* When visualizing an image, resize it to fit the window.

### Web backend
* Blosc2 Proxy class adoption. That allows the subscriber to fetch data from a publisher in chunks that are needed, instead of the whole dataset at once. This is useful for large datasets, where the subscriber may not have enough memory to hold the whole dataset.
* New @personal and @shared areas in the server, where users can create new datasets and upload files accessible to them and to a group of users, respectively.
* New @public area in the server, where users can publish datasets that are accessible to everyone.
* Allow for the subscriber to run standalone, without a publisher.
* New quota configuration in the server, to limit the amount of data that can be stored.
* Support for Unix Domain Sockets.

### Client API
* New upload/remove/move/copy commands in the subscriber.
* New adduser/deluser/listusers APIs.
* New c2context context manager to handle the connection to the server.
* Support for new lazy expressions in Python-Blosc2 3.0 (with support for saving reductions).

### Other improvements and fixes
* Minimal version is Python 3.11 now.
* Update to run with latest Python-Blosc2 3.0 release.
* Fix httpx for being able to use Caterva2 clients inside Pyodide.

## Changes from 2024.06.27 to 2024.07.01

* Fixed blosc2 dependency version to blosc2 3.0.0b1.


## Changes from 0.2 to 2024.06.27

* Web client: Improved navigation and display of dataset information.
* Web client: New Data tab to show the dataset's data. It supports NDArrays with structured dtypes as tables.
* Web client: Support for displaying MarkDown files.
* Web client: Support for authentication. See https://demo-auth.caterva2.net for an example.
* Web client: New @personal area (pseudo-root) when logged in. It allows creating new datasets and uploading files (drag-and-drop supported).
* Web client: New Prompt box to allow creating lazy datasets in the @personal area. Existing datasets can be referenced by their tag.
* Web client: New Download button to download a dataset as a file.
* Web client: New Delete button to delete a dataset from @personal area.
* Web client: Support to detect tomographies automatically using heuristics -- 3D integer datasets in greyscale and RGB(A) (using a 4th dim).
* Client API: Support for subscriber user authentication in client code.
* Client API: Support for creating lazy expressions in the @personal area.  The resulting data is not computed on creation, but on demand.


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
