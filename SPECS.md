# Minimal specifications

## Introduction

This document describes the minimal specifications for the project.  It is meant to describe the functionality of the client, whereas the implementation details of the client and server are left to the developer (as long as the specification works).

## Vocabulary

- **Root**: The root of a group of datasets that are published together.  It is identified by a name.
- **Dataset**: A dataset is a file that is uploaded by the client.  It is identified by a path.
  E.g. `foo/bar.b2nd` is a dataset path with root `foo`.
- **Server**: The server is the entity that follows changes in a root and allows the download of datasets from clients.
- **Client**: The client is a server consumer (e.g. a command line tool or a web interface) for the user to access the datasets; it connects to a server, and can add/download datasets (or slices) to it.

## Services

The two services (client and server) have a number of common options:

- `--listen`: the hostname and port that it listens, e.g. `localhost:8000`
- `--server`: the base of URLs provided by the server, if different from `http://<HTTP_HOST>:<HTTP_PORT>` (only for server)
- `--loglevel`: by default `warning`
- `--statedir`: directory where to store the service state files (cache, logs, pid file, etc.)

In production deployments it's recommended to use Systemd services.

## Client commands

The client must implement the following commands:

- `roots`: List all the available roots in the server.
- `list <root>`: List all the available datasets in a root.
- `url <root>`: Server URL from where a dataset can be downloaded.
- `info <dataset>`: Get metadata about a dataset.
- `show <dataset[slice]>`: Show the data of a dataset. `slice` is optional.
- `download <dataset> <output_dir>`: Get the data of a dataset and save it to a local `output_dir` folder.

## Configuration

There should be a configuration file (by default $CWD/caterva2.toml) where the configuration for each service is specified. For example:

```
[server]
listen = "localhost:8000"
urlbase = "https://cat2.example.com"  # e.g. served by reverse proxy
statedir = "_caterva2/state"
loglevel = "warning"
```


## Client implementation

The client must be implemented in Python 3 (3.11 being the minimal supported version).  It must be a library with a command line interface that connects to a server and sends commands to it.  The server must be running before the client can be used. If the server is not running, the client must print an error message and exit. The client is expected to be running before the server is started; if not, the server will only serve its cached data.

### Command line interface

- When a `roots` command is issued, the client must send a request to the server to list all the available roots.  The server will reply with a list of roots.

- When a `list` command is issued, the client must send a request to the server to list the datasets in the given root.  The server will reply with a list of datasets.

- When a `url` command is issued, the client must show the URL from where the given dataset may be downloaded.

- When an `info` command is issued, the client must send a request to the server to get the metadata of the given dataset.  The server will reply with the [metadata](#metadata).  See below for the [metadata](#metadata) format.

- When a `show` command is issued, the client must send a request to the server to retrieve the data of the given dataset.  The server will reply with the data.  The format is inferred from the extension of the output file: `.b2nd` for Blosc2 NDim and `.b2frame` for Blosc2 frames; an n-dim NumPy array and a 1-dim NumPy array will be shown respectively.  All other extensions will be delivered as a raw buffer (e.g. `foo/path/README.md` will be shown as text).

- When a `download` command is issued, the client must send a request to the server to retrieve the data of the dataset.  The server will reply with the data and client should be responsible to store it in its local `<output_dir>` folder. The name of the file will be the same as the dataset path (e.g. `foo/bar.b2nd` will be stored as `<output_dir>/foo/bar.b2nd`).

## Metadata

- `meta`: The metadata of the dataset.

`.b2nd` files are read as `NDArray` instances, and `meta` is a dictionary with the following fields:

```
In [15]: b = blosc2.uninit(shape=[1000], chunks=[100], blocks=[10], dtype=np.int16)
In [16]: dict(shape=b.shape, chunks=b.chunks, blocks=b.blocks, dtype=b.dtype, cparams=b.schunk.cparams)
Out[16]:
{'shape': (1000,),
 'chunks': (100,),
 'blocks': (10,),
 'dtype': dtype('int16'),
 'cparams': {'codec': <Codec.ZSTD: 5>,
  'codec_meta': 0,
  'clevel': 1,
  'use_dict': 0,
  'typesize': 2,
  'nthreads': 4,
  'blocksize': 20,
  'splitmode': <SplitMode.ALWAYS_SPLIT: 1>,
  'filters': [<Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.SHUFFLE: 1>],
  'filters_meta': [0, 0, 0, 0, 0, 0]}}
```

For `.b2frame`, or `.b2` files (the latter is mandatory as an additional file extension in generic files), they are read as `SChunk` instances, and `meta` is a dictionary with the following fields:

```
In [17]: sc = blosc2.SChunk(chunksize=100)
In [18]: sc.fill_special(8 * 100, special_value=blosc2.SpecialValue.UNINIT)
Out[18]: 8
In [19]: dict(chunksize=c.chunksize, typesize=c.typesize, cparams=c.cparams)
Out[19]:
{'chunksize': 100,
 'typesize': 1,
 'cparams': {'codec': <Codec.ZSTD: 5>,
  'codec_meta': 0,
  'clevel': 1,
  'use_dict': 0,
  'typesize': 1,
  'nthreads': 4,
  'blocksize': 0,
  'splitmode': <SplitMode.ALWAYS_SPLIT: 1>,
  'filters': [<Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.NOFILTER: 0>,
   <Filter.SHUFFLE: 1>],
  'filters_meta': [0, 0, 0, 0, 0, 0]}}
```

- `vlmeta`: The so-called variable length metadata (aka user metadata).  This is the same for both `.b2nd` and `.b2frame` files. E.g.:

```
In [20]: b.schunk.vlmeta['new_meta'] = "my data"
In [21]: dict(b.schunk.vlmeta)
Out[21]: {'new_meta': 'my data'}
```

## Root data repository example

You can find an example of a data root in the `root-example` folder.  It contains several datasets:

- `README.md`: A text file.

- `ds-hello.b2frame`: A SChunk containing a data buffer.  Constructed as:

      blosc2.SChunk(chunksize=100, data=b"Hello world!"*100, urlpath="ds-hello.b2frame", mode="w")

- `ds-1d.b2nd`: A 1D array (int64). Constructed as:

      a = np.arange(1000, dtype="int64")
      blosc2.asarray(a, chunks=(100,), blocks=(10,), urlpath="ds-1d.b2nd", mode="w")

- `ds-1d-b.b2nd`: A 1D array (6-byte strings). Constructed as:

      a = np.array([b"foobar"] * 1000)
      blosc2.asarray(a, chunks=(100,), blocks=(10,), urlpath="ds-1d-b.b2nd", mode="w")

- `ds-sc-attr.b2nd`: A scalar (string) with variable-length metalayers (user attributes). Constructed as:

      a = np.str_("foobar")
      b = blosc2.asarray(a, urlpath=path / "ds-sc-attr.b2nd", mode="w")
      for k, v in dict(a=1, b="foo", c=123.456).items():
          b.schunk.vlmeta[k] = v

- `dir1/ds-2d.b2nd`: A 2D array (uint16).  Constructed as:

      a = np.arange(200, dtype="uint16").reshape(10, 20)
      blosc2.asarray(a, chunks=(5, 5), blocks=(2, 3), urlpath="dir1/ds-2d.b2nd", mode="w")

- `dir1/ds-3d.b2nd`: A 3D array (float32). Constructed as:

      a = np.arange(60, dtype="float32").reshape(3, 4, 5)
      blosc2.asarray(a, chunks=(2, 3, 4), blocks=(2, 2, 2), urlpath="dir1/ds-3d.b2nd", mode="w")

- `dir2/ds-4d.b2nd`: A 4D array (complex128). Constructed as:

      a = np.arange(120, dtype="complex128").reshape(2, 3, 4, 5)
      blosc2.asarray(a+a*1j, chunks=(1, 2, 3, 4), blocks=(1, 2, 2, 2), urlpath="dir2/ds-4d.b2nd", mode="w")

## Data transmission

Whenever possible, data should be transmitted in [Blosc2 frame format](https://github.com/Blosc/c-blosc2/blob/main/README_CFRAME_FORMAT.rst).  That is, when a dataset (or a slice of it) is requested, the server should send the data in Blosc2 frame format.  The client should be able to read the data in this format return it to the user.  As Blosc2 frames can be read as-is, there will be no penalty in de-serializing the data.

## Compressing general files

For compressing general files, we can use the `SChunk` class.  E.g.:

```
In [1]: text = open("root-example/README.md", mode="rb").read()

In [2]: import blosc2

In [3]: schunk = blosc2.SChunk(urlpath="root-example/README.md.b2", mode="w")

In [4]: schunk.append_data(text)
Out[4]: 1

In [5]: text == blosc2.open("root-example/README.md.b2")[:]
Out[5]: True

```

For the time being, `.b2` files can be made in one shot (i.e. a single `schunk.append_data()` call), but in a next version we should be able to compress files larger than available memory by using a chunked algorithm (i.e. reading and writing chunk-by-chunk).

## Internal database (TODO: this is obsolete)

There will be an internal database for clients and servers for storing different metadata.  It will be a JSON file called `$(cwd)/_caterva2/db.json` and it will contain the following fields (J. David: please check this):

* `version`: The version of the database.
* `roots`: A list of roots.  Each root is a dictionary with the following fields:
  * `name`: The name of the root.
  * `url`: The client URL where the root is accessible (e.g. `http://localhost:5000/foo`).
  * `mtime`: The modification time of the root in the client.
  * `datasets`: A list of datasets.  Each dataset is a dictionary with the following fields:
    * `path`: The path of the dataset.
    * `mtime`: The modification time of the dataset in the client.
    * `meta`: The metadata of the dataset.
    * `vlmeta`: The variable length metadata of the dataset.

The ``meta`` and ``vlmeta`` fields above are the same as described in the [Metadata](#metadata) section above. They are purely informational at this point, but they will be used in a next version for searching and filtering datasets ([TinyDB](https://tinydb.readthedocs.io/en/latest/) can be used for this).

## TODO

- Revise the document for clarity and completeness.
- The original pubsub model may still live in parts if this document; try to remove it.
- Add examples of the client commands.
