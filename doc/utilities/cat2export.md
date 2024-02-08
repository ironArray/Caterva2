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

Since the Caterva2 package includes a sample root, we shall export it to an HDF5 file.  To get its location, run this:

```sh
python -c 'import caterva2; print(f"{caterva2.__path__[0]}/../root-example")'
```

```
.../site-packages/caterva2/../root-example  # "..." may vary for you
```

Let's see what's inside that directory:

```sh
find .../site-packages/caterva2/../root-example
```

```
.../root-example
.../root-example/README.md
.../root-example/dir1
.../root-example/dir1/ds-2d.b2nd
.../root-example/dir1/ds-3d.b2nd
.../root-example/dir2
.../root-example/dir2/ds-4d.b2nd
.../root-example/ds-1d.b2nd
.../root-example/ds-1d-b.b2nd
.../root-example/ds-hello.b2frame
```

Here we can see a small hierarchy with Blosc2 arrays (`*.b2nd`), frames (`*.b2frame`) and other files (like `README.md`).  To export it to an HDF5 file, run:

```sh
cat2export .../site-packages/caterva2/../root-example root-example.h5
```

The program produces no output, which means that it was able to convert everything in the Caterva2 root.  Currently, if `cat2export` fails to convert anything because of some error or the conversion not being supported, it just reports the issue and continues.  This behavior will be refined in the future.

Running `h5ls -r root-example.h5` should show the equivalent hierarchy that `cat2export` created in the HDF5 file:

```
/                        Group
/README.md               Dataset {68}
/dir1                    Group
/dir1/ds-2d              Dataset {10, 20}
/dir1/ds-3d              Dataset {3, 4, 5}
/dir2                    Group
/dir2/ds-4d              Dataset {2, 3, 4, 5}
/ds-1d                   Dataset {1000}
/ds-1d-b                 Dataset {1000}
/ds-hello                Dataset {1200}
```
