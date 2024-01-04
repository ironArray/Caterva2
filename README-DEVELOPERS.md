# Notes for developers

For the time being, the code is not very well documented, so this need to be fixed asap.

Also, for running the tests, one needs to run manually the broker, publisher and subscriber.
There is a `start_test_daemons.sh` script that does this, but it is not very robust.

## Running the tests

Go to the root folder of the project and run:

```shell
export PYTHONPATH=.
```

The PYTHONPATH is needed because the tests import the modules in the `caterva2` package folder.
Not sure how to fix this (or if it is necessary).

Now, start the daemons:

```shell
sh start_test_daemons.sh
```

or, if you prefer:

```shell
export PYTHONPATH=.
python src/bro.py &
python src/pub.py foo root-example &
python src/sub.py &
```

Finally, in another shell (or if you like to hear the daemons chatting), run the tests:

```shell
python -m pytest -s tests
```

For stopping the daemons, you will have to kill them manually (sorry!).
