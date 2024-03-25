(Launching-Caterva2-services)=
# Launching Caterva2 services

To do anything useful with Caterva2, you need at least a running broker, publisher (with some datasets) and subscriber.  For the following tutorials we'll run our own services in the local machine, with the publisher serving some example datasets included in the Caterva2 package (the `root-example` directory).

First of all, you need to install Caterva2 with the `services` extra:

```sh
python -m pip install caterva2[services]
```

The easiest way to run a set of services with example datasets is to launch the services used by tests:

```sh
python -m caterva2.tests.services
```

This will run a broker, a publisher and a subscriber listening for HTTP requests on `localhost:8000`, `localhost:8001` and `localhost:8002` respectively.  They will put their private files under the `_caterva2` directory, respectively in `broker`, `publisher.foo` and `subscriber`.  The publisher will be serving a root called `foo`, whose datasets (copied from `root-example`) sit in `_caterva2/data.foo`.  You may want to browse that directory.

**Note:** You may provide command-line arguments to `caterva2.tests.services` to use a different state directory, root name and dataset source instead of `_caterva2` and `foo=root-example`.  Multiple `ROOT_NAME=DATASET_SOURCE` arguments may be given, each to be served by a different publisher (run `caterva2.tests.services` with `--help` for more information).  Moreover, you may customize some service settings (except those set by the previous arguments) via a `caterva2.toml` configuration file in the current directory (see [](caterva2.toml) and [](Running-independent-Caterva2-services) for more information).

Since this terminal will be used by services to output their logs, you will need to run other commands in other terminals.  When you want to stop the services, go back to their terminal and press Ctrl+C (this should work for any service mentioned in other tutorials).
