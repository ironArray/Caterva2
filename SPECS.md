# Minimal specifications for the project

## Introduction

This document describes the minimal specifications for the project.  It is meant to describe the functionality of the client, whereas the implementation details of the publisher and subscriber are left to the developer (as long as the specification works).

## Vocabulary

- **Group**: A group is a collection of datasets that are published together.  It is identified by a name.
- **Dataset**: A dataset is a file that is published by the publisher.  It is identified by a path.

- **Broker**: The broker is the entity that manages the communication between publishers and subscribers.  It is responsible for creating groups and adding publishers and subscribers to them.
- **Publisher**: The publisher is the entity that makes datasets available to subscribers.  It is responsible for creating a group and adding datasets to it.
- **Subscriber**: The subscriber is the entity that follows changes in a group and allows the download of datasets from publishers.
- **Client**: The client is a command line interface for the user to access the datasets, it connects to a subscriber.

## Client commands

The client must implement the following commands:

- `list`: List all the available datasets.
- `list <group>`: List all the available datasets in a group.
- `info <dataset>`: Get metadata about a dataset (for the specs, see below).
- `get <dataset[slice]>`: Get the data of a dataset. `slice` is optional.
- `get <dataset[slice]> <output>`: Get the data of a dataset and save it to a file.


## Client implementation

The client must be implemented in Python 3 (3.9 being the minimal supported version).  It must be a command line interface that connects to a subscriber and sends commands to it.  The subscriber must be running before the client is started. If the subscriber is not running, the client must print an error message and exit. The publisher *can* be running before the subscriber is started. If the publisher is not running, the subscriber will only serve its cached data. Only one publisher per group will be supported initially.

When a `info` command is issued, the client must print the metadata of the dataset.  We will implement just the .b2nd files (NDArray instances in Python) for now  The metadata is a dictionary with the following fields:

- `meta`: The metadata of the dataset. E.g. if the dataset `b` is an NDArray, `meta` is the next tuple:

```
In [15]: b = blosc2.asarray(np.arange(20))
In [16]: dict(shape=b.shape, chunks=b.chunks, blocks=b.blocks, dtype=b.dtype, cparams=b._schunk.cparams)
Out[16]:
{'shape': (20,),
 'chunks': (20,),
 'blocks': (20,),
 'dtype': dtype('int64'),
 'cparams': {'codec': <Codec.ZSTD: 5>,
  'codec_meta': 0,
  'clevel': 1,
  'use_dict': 0,
  'typesize': 8,
  'nthreads': 6,
  'blocksize': 160,
  'splitmode': <SplitMode.ALWAYS_SPLIT: 1>,
  'filters': [<Filter.NOFILTER: 0>,
  <Filter.NOFILTER: 0>,
  <Filter.NOFILTER: 0>,
  <Filter.NOFILTER: 0>,
  <Filter.NOFILTER: 0>,
  <Filter.SHUFFLE: 1>],
  'filters_meta': [0, 0, 0, 0, 0, 0]}}
```

- `vlmeta`: The so-called variable length metadata (aka user metadata).  E.g.:
```
In [40]: b._schunk.vlmeta['new_meta'] = "my data"

In [41]: dict(b._schunk.vlmeta)
Out[41]: {'new_meta': 'my data'}
```

## Cache management details

Whenever an `info` or `get` command is issued, the subscriber must check if the dataset is already in the cache.  If it is, it must check if the dataset has changed in the publisher.  If it has, it must update the cache.  If it hasn't, it must use the cached data.  If the dataset is not in the cache, it must download it and add it to the cache.

`info` commands will just download the metadata and will create `uninit` datasets in cache. In the first implementation, `get` commands will make the subscriber to download the whole data from publisher. In a next version, subscriber will download only the chunks that are not in cache.

## Data group example

You can find an example of a data group in the `test-data` folder.  It contains 3 (small) datasets:

- `ds-1d.b2nd`: A 1D array.
- `dir1/ds-2d.b2nd`: A 2D array.
- `dir1/ds-4d.b2nd`: A 4D array.

## Communication failures

Initially, we will not implement any communication failure handling.  If the subscriber is not running, the client will just print an error message and exit.  If the publisher is not running, the subscriber will just serve its cached data.

