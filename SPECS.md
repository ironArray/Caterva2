# Minimal specifications

## Introduction

This document describes the minimal specifications for the project.  It is meant to describe the functionality of the client, whereas the implementation details of the publisher and subscriber are left to the developer (as long as the specification works).

## Vocabulary

- **Root**: The root of a group of datasets that are published together.  It is identified by a name.
- **Dataset**: A dataset is a file that is published by the publisher.  It is identified by a path.
  E.g. `foo/bar.b2nd` is a dataset path with root `foo`.

- **Broker**: The broker is the entity that manages the communication between publishers and subscribers.  It is also responsible for keeping a list of roots available to subscribers.
- **Publisher**: The publisher is the entity that makes datasets available to subscribers.  It is responsible for creating a root and adding datasets to it.
- **Subscriber**: The subscriber is the entity that follows changes in a root and allows the download of datasets from publishers.
- **Client**: The client is a subscriber consumer (e.g. a command line tool) for the user to access the datasets; it connects to a subscriber.

## Client commands

The client must implement the following commands:

- `roots`: List all the available roots in a broker.
- `subscribe <root>`: Request access to the datasets in a root.
- `list <root>`: List all the available datasets in a root.  Needs to be subscribed to the root.
- `url <root>`: URL for the rest API that serves the root.
- `info <dataset>`: Get metadata about a dataset.
- `get <dataset[slice]>`: Get the data of a dataset. `slice` is optional.
- `get <dataset[slice]> <output>`: Get the data of a dataset and save it to a local file. The format is inferred from the extension of the output file: `.b2nd` for Blosc2 and `.npy` for Numpy.
- `download <dataset>`: Get the *raw* data of a dataset file and save it to a file in the `downloads/` folder.

## Client implementation

The client must be implemented in Python 3 (3.9 being the minimal supported version).  It must be a library with a command line interface that connects to a subscriber and sends commands to it.  The subscriber must be running before the client is started. If the subscriber is not running, the client must print an error message and exit. The publisher *can* be running before the subscriber is started. If the publisher is not running, the subscriber will only serve its cached data.

### Command line interface

- When a `roots` command is issued, the client must send a request to the subscriber to list all the available roots.  The subscriber will reply with a list of roots (if possible, with flags indicating if a root is subscribed).

- When a `subscribe` command is issued, the client must send a request to the subscriber to subscribe to the root.  The subscriber will reply with a success or failure message.  If successful, the client must store the root metadata in its local cache.

- When a `list` command is issued, the client must send a request to the subscriber to list the datasets in the root.  The subscriber will reply with a list of datasets.

- When a `url` command is issued, the client must send a request to the subscriber to get the URL of the API rest of the root.  The subscriber will reply with the URL.

- When an `info` command is issued, the client must send a request to the subscriber to get the metadata of the dataset.  The subscriber will reply with the metadata.  See below for the metadata format.

- When a `get` command is issued, the client must send a request to the subscriber to get the data of the dataset.  The subscriber will reply with the data.

- When a `download` command is issued, the client must send a request to the subscriber to get the raw data of the dataset.  The subscriber will reply with the raw data and client should be responsible to store it in its local `downloads/` folder.

## Cache management details

Whenever the subscriber gets a request to `subscribe` to a root, it must check if metadata (not the data itself) for all the datasets in a root is already in the cache.  If it is, it must check if the root has changed in the publisher.  If it has, it must update the cache.  If it hasn't, it must use the cached data.  If the root metadata is not in the cache, it must fetch it and add it to the cache.

Metadata can be fetched and consolidated as uninitialized datasets in cache by using the API described in the #metadata section below.

There will be not an internal cache in the `subscriber`, but a folder in the filesystem.  The reason is that files in cached that are accessed frequently will be cached automatically by the OS, so no need to duplicate it (at least initially).  The folder will be called `b2cache/` and it will contain the metadata and data of the datasets.  The data and metadata will be stored in Blosc2 format.

Whenever a `get` command is issued, the subscriber must check if the data in dataset is already in the cache.  If it is, it must check if the dataset has changed in the publisher.  If it has, it must update the cache.  If it hasn't, it must use the cached data.  If the data of the dataset is not in the cache, it must fetch it and add it to the cache.

In the first implementation, `get` commands will make the subscriber download the whole data from publisher. In a next version, subscriber will download only the chunks in `[slice]` that are not in cache.


## Metadata

- `meta`: The metadata of the dataset.

For `.b2nd` files (NDArray instances), `meta` is a dictionary with the following fields:

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

For `.b2frame`, or `.b2` for short (the latter is preferred), files (SChunk instances), `meta` is a dictionary with the following fields:

```
In [17]: c = blosc2.SChunk(chunksize=100)
In [18]: c.fill_special(8 * 100, special_value=blosc2.SpecialValue.UNINIT)
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

- `vlmeta`: The so-called variable length metadata (aka user metadata).  This is the same for both `.b2nd` and `.b2` files. E.g.:

```
In [20]: b.schunk.vlmeta['new_meta'] = "my data"
In [21]: dict(b.schunk.vlmeta)
Out[21]: {'new_meta': 'my data'}
```

## Root data repository example

You can find an example of a data root in the `root-example` folder.  It contains 4 (small) datasets:

- `ds-hello.b2`: A SChunk containing a data buffer.  Constructed as:

      blosc2.SChunk(chunksize=100, data=b"Hello world!"*100, urlpath="ds-hello.b2", mode="w")

- `ds-1d.b2nd`: A 1D array (int64). Constructed as:

      a = np.arange(1000, dtype="int64"))
      blosc2.asarray(a, chunks=(100,), blocks=(10,), urlpath="ds-1d.b2nd", mode="w")

- `dir1/ds-2d.b2nd`: A 2D array (uint16).  Constructed as:

      a = np.arange(200, dtype="uint16").reshape(10, 20)
      blosc2.asarray(a, chunks=(5, 5), blocks=(2, 3), urlpath="dir1/ds-2d.b2nd", mode="w")

- `dir1/ds-3d.b2nd`: A 3D array (float32). Constructed as:

      a = np.arange(60, dtype="float32").reshape(3, 4, 5)
      blosc2.asarray(a, chunks=(2, 3, 4), blocks=(2, 2, 2), urlpath="dir1/ds-3d.b2nd", mode="w")

- `dir2/ds-4d.b2nd`: A 4D array (complex128). Constructed as:

      a = np.arange(120, dtype="complex128").reshape(2, 3, 4, 5)
      blosc2.asarray(a+a*1j, chunks=(1, 2, 3, 4), blocks=(1, 2, 2, 2), urlpath="dir2/ds-4d.b2nd", mode="w")

## Communication failures

As we will be checking for the validity of the data in the cache (see above), we will be able to implement communication failure handling in a next version.  When validity cannot be checked (broker or publisher are down), the subscriber will just serve its cached data.
