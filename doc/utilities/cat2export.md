(cat2export)=
# `cat2export` -- Convert Caterva2 roots to HDF5 files

As a complementary tool to [`cat2import`](cat2import) for exchanging data in the wider Caterva2 ecosystem, it is possible to export a whole Caterva2 root (with its datasets) to a single HDF5 file, using `cat2export`.  Still in its early stages of development, it also needs the `tools` extra to be installed:

```sh
python -m pip install caterva2[tools]
```

Its invocation syntax is also very simple:

```sh
cat2export CATERVA2_ROOT HDF5_FILE
```

Where the Caterva2 root directory and its contents must be readable by the tool, and the HDF5 file must not exist, as it will be created from scratch.  Directories in the Caterva2 root will be mapped to groups of the same name in the HDF5 file, and plain files and Blosc2 arrays, frames and compressed files will be converted to HDF5 datasets of the same name (minus any ``.b2*`` extension).

While the datasets will keep their array/chunk shape and data type, there is no reliable way yet to tell whether an output HDF5 dataset made of flat bytes was originally a plain file or a Blosc2 frame or compressed file.  Also, some default Blosc2 compression parameters are used for all output HDF5 datasets (this will change in the future).

Invoking `cat2export --help` provides more hints on how Caterva features are mapped into the HDF5 file.

## Usage example

TODO
