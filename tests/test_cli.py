import json
import subprocess


def cli(args):
    args = ['./venv/bin/python', './src/cli.py'] + args + ['--json']
    ret = subprocess.run(args, capture_output=True, text=True)
    assert ret.returncode == 0
    return json.loads(ret.stdout)


def test_cli_roots():
    out = cli(['roots'])
    assert out == ['foo']
    #assert out == ['foo/precip.b2nd', 'foo/temp.b2nd', 'foo/wind.b2nd']
