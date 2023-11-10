import json
import subprocess


def test_list_all():
    ret = subprocess.run(['./venv/bin/python', './src/cli.py', 'list', '-a'], capture_output=True, text=True)
    assert ret.returncode == 0
    out = json.loads(ret.stdout)
    assert out == ['foo/precip.b2nd', 'foo/temp.b2nd', 'foo/wind.b2nd']
