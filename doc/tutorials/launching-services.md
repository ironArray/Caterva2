(Launching-Caterva2-services)=
# Launching Caterva2 services

To do anything useful with Caterva2, you need at least a running broker, publisher (with some datasets) and subscriber.  For the following tutorials we'll run our own services in the local machine, with the publisher serving some example files included in the Caterva2 package.

First of all, you need to install Caterva2 with the `services` extra:

```sh
python -m pip install caterva2[services]
```

The easiest way to run a set of services with example datasets is to launch the services used by tests:

```sh
python -m caterva2.tests.services
```

This will run a broker, a publisher and a subscriber listening for HTTP requests on `localhost:8000`, `localhost:8001` and `localhost:8002` respectively.  They will put their private files under the `_caterva2` directory, respectively in `bro`, `pub` and `sub`.  Moreover, the publisher will be serving a root called `foo`, whose datasets sit in `_caterva2/data`.  You may want to browse that directory.

Since this terminal will be used by services to output their logs, you will need to run other commands in other terminals.  When you want to stop the services, go back to their terminal and press Ctrl+C (this should work for any service mentioned in other tutorials).
