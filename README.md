# Caterva2: On-demand access to Blosc2/HDF5 data repositories

## What is it?

Caterva2 is a service for sharing [Blosc2][] and [HDF5][] datasets with authenticated users, work groups, or the public. It provides multiple interfaces: web GUI, REST API, Python API, and command-line client.

<img src="./doc/_static/caterva2-block-diagram2.png" alt="Figure: Caterva2 block diagram" width="100%"/>

Use it remotely or locally to access datasets in a directory hierarchy or share them across your network.

<img src="./doc/_static/caterva2-data-sharing.png" alt="Figure: How data can be shared" width="50%"/>

The Python API is recommended for building custom clients, while the web GUI offers a user-friendly interface for browsing datasets.

<img src="./doc/_static/web-tomo-view.png" alt="Figure: web viewer for tomography" width="100%"/>

[Blosc2]: https://www.blosc.org/pages/blosc-in-depth/
    "What Is Blosc? (Blosc blog)"

[HDF5]: https://www.hdfgroup.org/solutions/hdf5/
    "HDF5 (HDF Group)"

## Caterva2 Clients

The Caterva2 package provides a lightweight library for building custom clients. Choose the interface that best fits your needs:

- **[Web GUI](https://ironarray.io/caterva2-doc/tutorials/web-client.html)** - Browser-based interface
  <img src="./doc/_static/web-data-view.png" alt="Figure: web data browser and viewer" width="100%"/>

- **[Python API](https://ironarray.io/caterva2-doc/tutorials/API.html)** - Programmatic access
  ```python
  import caterva2 as cat2

  client = cat2.Client("https://cat2.cloud/demo")
  print(client.get("@public/examples/numbers_color.b2nd")[2])
  ```

- **[Command-line client](https://ironarray.io/caterva2-doc/tutorials/cli.html)** - Terminal interface
  ```sh
  cat2-client --server https://cat2.cloud/demo info @public/examples/numbers_color.b2nd
  ```

- **[REST API](https://ironarray.io/caterva2-doc/tutorials/RESTAPI.html)** - HTTP interface (use with [Postman](https://www.postman.com/), [curl](https://curl.se/), etc.)
  See the live API docs at [cat2.cloud/demo/docs](https://cat2.cloud/demo/docs).

All interfaces support authentication for accessing private datasets (see "User authentication" below).

## Installation

### For Users

**Client only** (Python API and CLI tools):
```sh
pip install caterva2[clients]
```

**Test the installation** (includes client, server, and test suite):
```sh
pip install caterva2[tests]
python -m caterva2.tests
CATERVA2_SECRET=c2sikrit python -m caterva2.tests  # with authentication
```

### For Developers

**Install from source** (includes server, clients, and test suite):
```sh
git clone https://github.com/ironArray/Caterva2
cd Caterva2
pip install -e .[tests]
python -m pytest
CATERVA2_SECRET=c2sikrit python -m pytest  # with authentication
```

### Available Extras

Append `[extra1,extra2,...]` to any install command:

- `clients` - CLI and terminal tools
- `server` - Server service
- `tests` - Test suite (includes server and clients)
- `blosc2-plugins` - JPEG 2000 support via blosc2-grok

**Note:** Test runs create a `_caterva2_tests` directory with state files for inspection.

## Quick start

See [Caterva2 documentation](https://ironarray.io/caterva2-doc/index.html) for detailed tutorials.

**Setup:**
1. Install with server and client support:
   ```sh
   pip install caterva2[server,clients]
   ```

2. Copy configuration files:
   - `caterva2.sample.toml` → `caterva2.toml` (client config)
   - `caterva2-server.sample.toml` → `caterva2-server.toml` (server config)

   Place in current directory, `~/`, or `/etc/`. Use `--conf` to specify alternate locations.

3. Start the server:
   ```sh
   CATERVA2_SECRET=c2sikrit cat2-server &
   ```
   `CATERVA2_SECRET` is required for user authentication (see below).

**Server directory structure:**

```sh
tree _caterva2
_caterva2
└── state
    ├── db.json          # metadata
    ├── db.sqlite        # metadata
    ├── media            # temporary files for web GUI
    ├── personal         # user-specific datasets
    ├── public           # publicly shared datasets
    └── shared           # group-shared datasets
```

**Populate with example datasets:**

```sh
cp -r root-example/ _caterva2/state/public/
```

Browse them at [http://localhost:8000/?roots=@public](http://localhost:8000/?roots=@public)

### User authentication

Enable authentication by setting `CATERVA2_SECRET` when starting the server. This enables user management but does not verify email addresses or support password recovery.

**Create a user:**
```sh
cat2-admin adduser user@example.com foobar11
```

**Authenticate clients:**
- **Web GUI** - Login prompt on access
- **Python API** - Pass credentials to client:
  ```python
  client = cat2.Client("http://localhost:8000", ("user@example.com", "foobar11"))
  ```
- **CLI** - Use `--user` and `--pass` options

### Command-line client

**List available roots:**
```sh
cat2-client --user user@example.com --pass foobar11 roots
```
<details>
<summary>Show output</summary>

```
@public
@personal
@shared
```
</details>

**List datasets:**
```sh
cat2-client list @public
```
<details>
<summary>Show output</summary>

```
examples/README.md
examples/Wutujing-River.jpg
examples/cat2cloud-brochure.pdf
examples/cube-1k-1k-1k.b2nd
examples/cubeA.b2nd
examples/cubeB.b2nd
examples/dir1/ds-2d.b2nd
examples/dir1/ds-3d.b2nd
examples/dir2/ds-4d.b2nd
examples/ds-1d-b.b2nd
examples/ds-1d-fields.b2nd
examples/ds-1d.b2nd
examples/ds-2d-fields.b2nd
examples/ds-hello.b2frame
examples/ds-sc-attr.b2nd
examples/gaia-ly.b2nd
examples/hdf5root-example.h5
examples/ironpill_nb.ipynb
examples/kevlar-tomo.b2nd
examples/lazyarray-large.png
examples/lung-jpeg2000_10x.b2nd
examples/numbers_color.b2nd
examples/numbers_gray.b2nd
examples/sa-1M.b2nd
examples/slice-time.ipynb
examples/tomo-guess-test.b2nd
large/gaia-3d.b2nd
large/slice-gaia-3d.ipynb
```
</details>

**Browse directory tree:**
```sh
cat2-client tree @public
```
<details>
<summary>Show output</summary>

```
├── examples
│   ├── README.md
│   ├── Wutujing-River.jpg
│   ├── cat2cloud-brochure.pdf
│   ├── cube-1k-1k-1k.b2nd
│   ├── cubeA.b2nd
│   ├── cubeB.b2nd
│   ├── dir1
│   │   ├── ds-2d.b2nd
│   │   └── ds-3d.b2nd
│   ├── dir2
│   │   └── ds-4d.b2nd
│   ├── ds-1d-b.b2nd
│   ├── ds-1d-fields.b2nd
│   ├── ds-1d.b2nd
│   ├── ds-2d-fields.b2nd
│   ├── ds-hello.b2frame
│   ├── ds-sc-attr.b2nd
│   ├── gaia-ly.b2nd
│   ├── hdf5root-example.h5
│   ├── ironpill_nb.ipynb
│   ├── kevlar-tomo.b2nd
│   ├── lazyarray-large.png
│   ├── lung-jpeg2000_10x.b2nd
│   ├── numbers_color.b2nd
│   ├── numbers_gray.b2nd
│   ├── sa-1M.b2nd
│   ├── slice-time.ipynb
│   └── tomo-guess-test.b2nd
└── large
    ├── gaia-3d.b2nd
    └── slice-gaia-3d.ipynb
```
</details>

**Get dataset info:**
```sh
cat2-client info @public/examples/ds-1d.b2nd
```
<details>
<summary>Show output</summary>

```
Getting info for @public/examples/ds-1d.b2nd
shape : [1000]
chunks: [100]
blocks: [10]
dtype : int64
nbytes: 7.81 KiB
cbytes: 4.90 KiB
ratio : 1.59x
mtime : 2026-01-15T17:04:50.823466Z
cparams:
  codec  : ZSTD (5)
  clevel : 1
  filters: [SHUFFLE]
```
</details>

For more commands: `cat2-client --help`

## Documentation

For tutorials, API references, and examples, visit the [Caterva2 documentation](https://ironarray.io/caterva2-doc/index.html).
