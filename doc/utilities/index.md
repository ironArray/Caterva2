# Utilities

Although the main role of the Caterva2 package is to provide a Python library for coding Caterva2 clients in Python, it also includes additional utilities to help with particular tasks.  The sections below cover some of them.  Please mind that they may be part of extra Caterva2 features with additional dependencies and requirements.

## `cat2cli` -- Command-line Caterva2 client

This program allows interacting with a Caterva2 subscriber from the command line, in interactive shell sessions or invoked by other programs.  To use it, the `clients` extra needs to be installed:

```sh
python -m pip install caterva2[clients]
```

Running `cat2cli --help` should provide a list of supported commands that may be invoked like this:

```
cat2cli [GENERIC_OPTION...] COMMAND [COMMAND_OPTION...] COMMAND_ARGUMENTS...
```

A relevant generic option (besides `--help` itself) is `--host`, which overrides the subscriber address used by default.  It should have the `HOST:PORT` format (with IPv6 addresses between square brackets), for example `sub.edu.example.org:3126`.

`cat2cli` may use a TOML configuration file (`caterva2.toml` in the current directory unless overridden with the `--conf` option).  Currently, it may only get the subscriber address from there (`http` setting in `[subscriber]` section).  Command-line options override settings read from the configuration file.

A relevant command option is `--help`, which shows the options and arguments accepted by a particular command.  Another one is `--json`, which forces the output of commands that accept it to be in JSON format, as that may be more amenable for parsing by other programs.

For a short tutorial on `cat2cli`, see [](Using-the-command-line-client).

(cat2import)=
## `cat2import` -- Convert HDF5 files to Caterva2 roots

In the ecosystem where Caterva2 belongs, it is common to work with HDF5 files containing multidimensional numerical data stored as datasets arranged in arbitrary hierarchies.  Caterva2 is designed to distribute such type of datasets (and some others), with similar features such as compression, chunking and arbitrary attributes, and also grouped in hierarchies.

`cat2import` is a simple tool (still in its early stages of development) targeted at exporting a single HDF5 file into an equivalent Caterva2 root.  To use it, the `tools` extra needs to be installed:

```sh
python -m pip install caterva2[tools]
```

For the moment it has a very simple invocation syntax:

```
cat2import HDF5_FILE CATERVA2_ROOT
```

Where the HDF5 file must exist beforehand, and the Caterva2 root directory must not, as it will be created from scratch.  HDF5 groups will be mapped to directories of the same name in the Caterva2 root, and datasets to Blosc2 array files with the same name, plus a `.b2nd` extension.  There is currently no way of controlling the involved compression algorithm or parameters, nor the chunking/blocking of data, so defaults are be used (this will change in the future).

Invoking `cat2import --help` provides more hints on which HDF5 features are supported, and how they are mapped into the Caterva2 root.

### Usage example

We shall export one of the HDF5 files in the PyTables test suite: [ex-noattr.h5](https://github.com/PyTables/PyTables/raw/master/tables/tests/ex-noattr.h5).  Download it, place it into some working directory and open a shell into that directory.  Since Caterva2 tools depend on h5py, we'll use it to look at the hierarchy in the file:

```sh
python -c 'import h5py; h5py.File("ex-noattr.h5").visit(print)'
```

This should show a couple of groups with datasets in them:

```
columns
columns/TDC
columns/name
columns/pressure
detector
detector/table
```

Now we shall export `ex-noattr.h5` into a new Caterva2 root called `ex-noattr`.  Just run:

```sh
cat2import ex-noattr.h5 ex-noattr
```

The program only outputs the following error:

```
ERROR:root:Failed to convert dataset to Blosc2 ND array: 'detector/table' -> ValueError('invalid shape in fixed-type tuple.')
```

However, it does finish successfully and it generates the requested root.  Running `find ex-noattr` to print all files and directories under it shows something like this:

```
ex-noattr
ex-noattr/columns
ex-noattr/columns/TDC.b2nd
ex-noattr/columns/pressure.b2nd
ex-noattr/columns/name.b2nd
ex-noattr/detector
```

So the program was able to export all groups and datasets save for `detector/table`, hence the previous error.  In general, `cat2import` tries to convert each group, dataset or attribute at hand, but if it fails because of some error or the conversion not being supported, it just reports the issue and continues with the next object.  This behavior will be refined in the future.

Now you should be able to configure `ex-noattr` as the data directory for your Caterva2 publisher.  See [](Running-independent-Caterva2-services) for more information.
