# Caterva2: On-demand access to Blosc2/HDF5 data repositories

## What is it?

Caterva2 is a service meant for serving [Blosc2][] and [HDF5][] datasets among authenticated users, work groups, or the public.  There are several interfaces to Caterva2, including a web GUI, a REST API, a Python API, and a command-line client.

<img src="./doc/_static/caterva2-block-diagram2.png" alt="Figure: Caterva2 block diagram" width="100%"/>

It can be used either remotely or locally, as a simple way to access datasets in a directory hierarchy, or to share them with other users in the same network.

<img src="./doc/_static/caterva2-data-sharing.png" alt="Figure: How data can be shared" width="50%"/>

The Python API is the recommended way for building your own Caterva2 clients, whereas the web client provides a more user-friendly interface for browsing and accessing datasets.

<img src="./doc/_static/web-tomo-view.png" alt="Figure: web viewer for tomography" width="100%"/>

[Blosc2]: https://www.blosc.org/pages/blosc-in-depth/
    "What Is Blosc? (Blosc blog)"

[HDF5]: https://www.hdfgroup.org/solutions/hdf5/
    "HDF5 (HDF Group)"

## Caterva2 Clients
The main role of the Caterva2 package is to provide a simple and lightweight library to build your own Caterva2 clients. The variety of interfaces available allows you to choose the one that best fits your needs. For example, querying a dataset from source can be accomplished :
- Via the [web GUI](https://ironarray.io/caterva2-doc/tutorials/web-client.html) using a browser <img src="./doc/_static/web-data-view.png" alt="Figure: web data browser and viewer" width="100%"/>
- Via the [Python API](https://ironarray.io/caterva2-doc/tutorials/API.html)
```
import caterva2 as cat2
client = cat2.Client("https://cat2.cloud/demo")
print(client.get("@public/examples/numbers_color.b2nd")[2])
```
- Via the [command line client](https://ironarray.io/caterva2-doc/tutorials/cli.html)
```sh
$ cat2-client --server https://cat2.cloud/demo info @public/examples/numbers_color.b2nd
```
- Via the [REST API](https://ironarray.io/caterva2-doc/tutorials/RESTAPI.html) using a REST client like [Postman](https://www.postman.com/) or [curl](https://curl.se/) (see [here](https://cat2.cloud/demo/docs)).

In addition, as Caterva2 supports authentication, all client interfaces expose a way to log in and access private datasets. Administration of authenticated users may be done using the internal mechanics of Caterva2 (see section "User authentication" below).

## Installation

You may install Caterva2 in several ways:

- Pre-built wheel from PyPI:

  ```sh
  python -m pip install caterva2
  ```

- Developer setup:

  ```sh
  git clone https://github.com/ironArray/Caterva2
  cd Caterva2
  python -m pip install -e .
  ```

When a client is used (web GUI, REST API, Python API, or command line) to query datasets, it will connect to a Caterva2 **server** service, which provides access to the datasets it serves. The server services may be managed via the command line by installing the `caterva2` package with the `[server]` extra feature (we also wish to use the command line client, so we will also install the `clients` extra too):

  ```sh
  python -m pip install caterva2 [server, clients]
  ```

In general, if you intend to run Caterva2 services, client programs, or the test suite, you need to enable the proper extra features by appending `[feature1,feature2...]` to the last argument of `pip` commands above.  The following extras are supported:

- `server` for running the Caterva2 server service
- `clients` to use Caterva2 client programs (command-line or terminal)
- `blosc2-plugins` to enable extra Blosc2 features like Btune or JPEG 2000 support
- `plugins` to enable web GUI features like the tomography display
- `tests` if you want to run the Caterva2 test suite

### Testing

After installing with the `[tests]` extra, you can quickly check that the package is sane by running the test suite (that comes with the package):

```sh
$ python -m caterva2.tests
$ CATERVA2_SECRET=c2sikrit python -m caterva2.tests  # tests requiring authentication
```

You may also run tests from the root of the source code directory:

```sh
$ python -m pytest
$ CATERVA2_SECRET=c2sikrit python -m pytest  # tests requiring authentication
```

Tests will use a copy of Caterva2's `root-example` directory.  After they finish, state files will be left under the `_caterva2_tests` directory for inspection (it will be re-created when tests are run again).

## Quick start

(Find more detailed step-by-step [tutorials](Tutorials) in Caterva2 documentation.)

For this quick start, let's:

- create a virtual environment and install Caterva2 with the `[server,clients]` extras (see above).
- copy the configuration file `caterva2.sample.toml` to `caterva2.toml`, `~/.caterva2.toml`, or `/etc/caterva2.toml`.
- copy the server configuration file `caterva2-server.sample.toml` to `caterva2-server.toml`, `~/.caterva2-server.toml`, or `/etc/caterva2-server.toml`.

Clients will search for configuration in the following order: current directory (`./caterva2.toml`), home directory (`~/.caterva2.toml`), and system-wide (`/etc/caterva2.toml` on Unix). You can also specify an alternative file with the `--conf` option.  See also [configuration.md](configuration.md) in Caterva2 tutorials for more details.  Server will look for `caterva2-server.toml` instead, either in the current directory, home directory, or system-wide; you can also specify an alternative file with the `--conf` option.

Then run the server:

```sh
CATERVA2_SECRET=c2sikrit cat2-server &  # server
```

The `CATERVA2_SECRET` environment variable is mandatory and is explained below in the following section.

Now, let's see the directories that have been created by the server:

```sh
tree _caterva2
_caterva2
└── state
    ├── db.json
    ├── db.sqlite
    ├── media
    ├── personal
    ├── public
    └── shared

6 directories, 2 files
```

We see that we have a state directory with several subdirectories.  The `personal` directory is where we will store our personal datasets.  The `public` directory is where we will store datasets that are shared with the public.  The `shared` directory is where we will store datasets that are shared with other users.  The `media` directory is where the server will store temporary media files (images, videos, etc.) that are used by the web GUI for internal purposes.  The `db.json` and `db.sqlite` files are where the server will store its metadata.

For populating the server with datasets, let's copy the `root-example` directory from the Caterva2 source code:

```sh
$ cp -r root-example/ _caterva2/state/public/
$ tree _caterva2/state/public/
_caterva2/state/public/
├── dir1
│   ├── ds-2d.b2nd
│   └── ds-3d.b2nd
├── dir2
│   └── ds-4d.b2nd
├── ds-1d-b.b2nd
├── ds-1d-fields.b2nd
├── ds-1d.b2nd
├── ds-2d-fields.b2nd
├── ds-hello.b2frame
├── ds-sc-attr.b2nd
├── ex-noattr.h5
├── README.md
└── root-example.h5

3 directories, 12 files
```

Cool!  Now we have a server with some datasets to play with.  If you want to see the web GUI, open a browser and go to [http://localhost:8000/?roots=@public](http://localhost:8000/?roots=@public) for browsing these datasets.

### User authentication
The Caterva2 server includes support for authenticating users.  To enable it, run the server with the environment variable `CATERVA2_SECRET` set to some non-empty, secure string that will be used for various user management operations. Note that new accounts may be registered, but their addresses are not verified.  Password recovery does not work either.

To create a user, you can use the `cat2-admin adduser` command. For example:

```sh
$ cat2-admin adduser user@example.com foobar11
User user@example.com with id b2f6f251-d2f0-4b8e-8d17-46ff65832e98 has been added.
User 'user@example.com' added successfully.
Password: foobar11
```

Client queries then require the same user credentials:
- The user will be prompted to login when accessing the web client using a browser.
- The Python API client can be authenticated in the following way:
```
client = cat2.Client("http://localhost:8000", ('user@example.com', 'foobar11'))
```
- The command line client can be authenticated with the `--user` and `--pass` options (see below).

### The command line client
Now that the services are running, we can use the `cat2-client` client to talk
to the server. In another shell, let's list all the available roots in the system:

```sh
$ cat2-client --user "user@example.com" --pass "foobar11" roots
@public
@personal
@shared
```

To experiment a bit, let's upload a file from the `root-example`folder to the `@personal` root:

```sh
$ cat2-client --username user@example.com --password foobar11 upload root-example/ds-1d.b2nd @personal/ds-1d.b2nd
Dataset stored in @personal/ds-1d.b2nd
```

Now, let's list the datasets in the `@personal` root and see that the uploaded file appears:

```sh
$ cat2-client --username user@example.com --password foobar11 list @personal
ds-1d.b2nd
```

Let's ask the server for more info about the dataset:

```sh
$ cat2-client --username user@example.com --password foobar11 info @personal/ds-1d.b2nd
Getting info for @personal/ds-1d.b2nd
shape : [1000]
chunks: [100]
blocks: [10]
dtype : int64
nbytes: 7.81 KiB
cbytes: 4.90 KiB
ratio : 1.59x
mtime : 2025-10-03T12:08:03.962856Z
cparams:
  codec  : ZSTD (5)
  clevel : 1
  filters: [SHUFFLE]
```

This command returns dataset's metadata, including its shape, chunks, blocks, data type, and compression parameters.

You can also ask for the contents of the @public root:

```sh
$ cat2-client --username user@example.com --password foobar11 tree @public
├── README.md
├── dir1
│   ├── ds-2d.b2nd
│   └── ds-3d.b2nd
├── dir2
│   └── ds-4d.b2nd
├── ds-1d-b.b2nd
├── ds-1d-fields.b2nd
├── ds-1d.b2nd
├── ds-2d-fields.b2nd
├── ds-hello.b2frame
├── ds-sc-attr.b2nd
├── ex-noattr.h5
└── root-example.h5
```

We see the contents of the `root-example` directory that we copied before are available in the `@public` root too.

There are more commands available in the `cat2-client` client; ask for help with:

```sh
$ cat2-client --help
```

### Docs
To see how to use the Python and REST API and web GUI, check out the [Caterva2 documentation](https://ironarray.io/caterva2-doc/tutorials/API.html). You'll also find more information on how to use Caterva2, including tutorials, API references, and examples [here](https://ironarray.io/caterva2-doc/index.html).

That's all folks!
