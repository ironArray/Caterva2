# Release notes

## Changes from 2025.11.17.1 to 2025.12.3

* Upload in-memory objects
* Upload ``LazyUDF`` objects
* Change to upload API and return Dataset/File object rather than path in most cases
* New Image plugin
* Use prompt box as calculator

## Changes from 2025.11.17 to 2025.11.17.1

* Hot-fix release to solve authentication issues in pyodide

## Changes from 2025.8.7 to 2025.11.17

### New features

* Fetch and download of HDF5 is possible now.  The HDF5 datasets are
  fetched as HDF5Proxy instances, which allows to access the data
  on-demand, without downloading the whole dataset at once.

* The client API now requires blosc2, and sends a "Accept-Encoding: blosc2"
  header. That allows the server to send Blosc2-compressed data directly,
  avoiding the overhead of sending uncompressed data through the network.

* Extended lazy expression functionalities, in web and python client

* Loading files from third-party url directly to server enables via ``load_from_url``

### New command-line tools

Command line tools have been restructured.  Now there are three main tools:

* New `cat2-client` command-line tool to interact with a Caterva2 server
  from the command line.  It supports listing datasets, downloading them,
  uploading new datasets, deleting datasets, browsing datasets, and many
  other features.

* New `cat2-server` command-line tool to run a Caterva2 server
  from the command line.  Server configuration is possible through
  a caterva2-server.toml file, where you can specify the urlbase, quota
  limits, authentication settings, and other options.

* New `cat2-admin` command-line tool to manage users in a Caterva2 server.
  For the time being it only supports adding new users, but more features will
  be added in the future.

* New `cat2-agent` command-line tool to run a Caterva2 agent.
  An agent is a lightweight client that can synchronize datasets between
  a local directory and a Caterva2 server.

Previous command-line tools like `cat2cli` or `cat2sub` have been deprecated
in favor of the previously mentioned tools.

### Fixes and improvements

* Don't leak secrets in /api/listusers.

* Error handling has been improved in several places.  Now,
  more informative error messages are shown when something goes wrong.

* Fix 500 error in /api/info/<path-to-dir> (return 404).

* Add mtime property to `info` command in `cat2-client`.

* Paginate and colorize values for `show` command in `cat2-client`.

* New `tree` command in `cat2-client` to show the dataset hierarchy
  as a tree.

* New `browse` command in `cat2-client` to browse a dataset on a
  local browser (with support for tomographies).

* New `handle` command in `cat2-client` to show the dataset handle
  (unique identifier) of a dataset.

* Improve presentation of the prompt help for the web client.

### Documentation improvements

* Comprehensive overhaul of quick start tutorial in README:
  https://ironarray.io/caterva2-doc/getting_started/index.html#quick-start

* Updated tutorial for the client configuration (caterva2.toml):
  https://ironarray.io/caterva2-doc/tutorials/configuration.html

* Updated tutorial for the command-line tools:
  https://ironarray.io/caterva2-doc/tutorials/cli.html

* Updated tutorial for the rest API:
  https://ironarray.io/caterva2-doc/tutorials/RESTAPI.html

### Other changes

* Removed legacy `cat2import` and `cat2export` utilities.

* Remainders of the PubSub code have been removed from the codebase.  Now
  Caterva2 only supports a client-server architecture.

* Slices of regular files are not supported anymore.  Only Blosc2-compressed
  datasets and HDF5 datasets can be sliced.

* `Client.concatenate()` has been renamed to `Client.concat()` for consistency
  with Array API.

* `Client.subscribe()` has been removed, as it was part of the PubSub
  architecture.

* Anonymous access to the server show @public contents by default now.

* Changed --http to --listen in cli commands for starting servers.

## Changes from 2025.6.26 to 2025.8.7

### Big refactoring

* The PubSub code has been pruned. Even if powerful, PubSub added much
  complexity to the code, without providing a clear benefit.

* New `cat2agent` to watch a directory and sync changes to a Caterva2 server.
  This is particularly useful for automatically uploading new datasets to a
  Caterva2 server, or for keeping a local copy of a remote Caterva2 server.

* With the introduction of the HDF5Proxy class, HDF5Root was obsoleted and
  has been removed.

### Web frontend

* New stack/concat prompt commands.

### API changes

* No API changes for the client REST or Python APIs, so the big refactoring
  does not affect them.

### Bug fixes and improvements

* Fix "/api/chunk/{path:path}" for shared/public.

* Several minor fixes so that the behavior of the server is more consistent.

## Changes from 2025.5.2 to 2025.6.26

### New support for native HDF5 files

* The HDF5 files can be uploaded to the server and 'unfold'ed into
  component datasets, which are then available for browsing and
  downloading.

* The datasets are suffixed with `.b2nd` to indicate that
  they can be browsed and downloaded as Blosc2-compressed arrays, but
  they are actually proxies to the original HDF5 datasets.  This allows
  for a more efficient use of space and faster access to the data.

* New tutorial on how to use the HDF5 support:
  https://ironarray.io/caterva2-doc/tutorials/hdf5.html

### Web frontend

* Improvements in the tomography viewer:
  * It now supports travelling in any direction (not just the first dimension).
  * The size by default is now 512x512 pixels, which is more suitable for
    a faster browsing experience.
  * Update image with JavaScript instead of htmx for reducing latencies.

* The Download and Delete buttons now are always visible, not just on the
  Meta (previously Main) tab as before.

### API additions

* New `Client.concat` and `Client.stack` methods to be able to concatenate or
  stack datasets in the server.  This is useful for creating new datasets from
  existing ones without downloading them.

* These have been added to the REST API as well, so they can be used
  from the web client or any other client that supports the REST API.

### Bug fixes and improvements

* Add field-indexing to caterva2. Fixes #187.
* Enabled field filtering for Proxy sources.
* Full support for HDF5Proxy instances in expressions (including where()).
* Support for HDF5 proxies in lazy operands.

## Changes from 2025.04.09 to 2025.5.2

### API changes

* The `Client.lazyexpr` method has made `operands` param optional
  (it was required).  This allows to create lazy expressions without
  operands, which is useful for creating empty datasets.  It also gained

* Also, the `Client.lazyexpr` method a new `compute` parameter,
  which allows to compute the lazy expression immediately.  The default is
  `False`, which means that the lazy expression is created but not computed.

### Others

* Lazy expressions are more tolerant now when an operand disappears
  (e.g. when a dataset is deleted).  The lazy expression cannot be used,
  but can still be introspected.

* Now it is possible to make the logo configurable for the web client.  The
  logo can be dropped in `[statedir]/media/logo.png`, in the `[subscriber]`
  section of the config file.  The logo must be a PNG/JPEG/WEBP file, and it
  will be resized to fit the header.

* The Prompt box has more space now.

## Changes from 2025.02.20 to 2025.04.09

### API changes
* New `caterva2.Client` class to handle the connection to the server.
  See examples in docs: https://ironarray.io/caterva2-doc/reference/client_class.html
  The previous way to authenticate and connect to the server has been removed,
  so you need to migrate your code to use the new Client class.
* New `Dataset.slice()` method to slice a dataset.  This returns
  either a `blosc2.NDArray` or a `blosc2.Schunk` object, depending on the
  underlying data object.
* New `Dataset.append()` method to append data to a dataset.  This is
  useful for datasets that enlarge over time.

### Others
* Several fixes and improvements in the Web UI.
* Fixed a bug when asking for the first index (0) of a dataset.
* Optimized the fetch of the dataset in the web client.


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
* Implemented many of the functionality in the specifications file (``SPECS.md``).
* Simple API for creating new clients.
* Tests for both clients and servers.
* Simple web client.
* Basic documentation.
* A client (`cat2cli`) to query datasets.
* A tool (`cat2import`) for importing datasets.
