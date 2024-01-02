import os
import pathlib
import json
import subprocess


def cli(args, binary=False):
    cli_path = pathlib.Path(os.environ['CATERVA2_SOURCE']) / 'src' / 'cli.py'
    args = ['python', str(cli_path)] + args
    if not binary:
        args += ['--json']
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    out = ret.stdout
    return out if binary else json.loads(out)


def test_roots(services):
    out = cli(['roots'])
    assert out == {'foo': {'name': 'foo', 'http': 'localhost:8001', 'subscribed': None}}

def test_url(services):
    out = cli(['url', 'foo'])
    assert out == ['http://localhost:8001']

def test_subscribe(services):
    # Subscribe once
    out = cli(['subscribe', 'foo'])
    assert out == 'Ok'

    # Subscribe again, should be a noop
    out = cli(['subscribe', 'foo'])
    assert out == 'Ok'

    # Show
    a = cli(['show', 'foo/ds-1d.b2nd'], binary=True)
    b = cli(['show', 'foo/ds-1d.b2nd'], binary=True)
    assert a == b
