###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Helpers for notebooks served through JupyterLite.

Kept out of ``server.py`` so they can be imported without triggering the
server module's import-time login check (which requires ``CATERVA2_SECRET``).
"""

import io

import nbformat

PYODIDE_BOOTSTRAP_CELL_SOURCE = """# Install blosc2 and caterva2 in Pyodide environments (automatically added)
import sys
if sys.platform == "emscripten":
    import requests
    import micropip

    # Ensure micropip understands PEP 783 (pyemscripten) wheel tags, used by
    # blosc2 wasm32 wheels since 4.4.3.  micropip is pure Python, so it can
    # upgrade itself even on Pyodide runtimes that bundle an older version.
    from packaging.version import Version
    if Version(micropip.__version__) < Version("0.11.1"):
        await micropip.install("micropip>=0.11.1", reinstall=True)
        for mod in [m for m in sys.modules if m.split(".")[0] == "micropip"]:
            del sys.modules[mod]
        import micropip

    # Install latest blosc2
    blosc_latest_url = "https://blosc.github.io/python-blosc2/wheels/latest.txt"
    blosc_wheel_name = requests.get(blosc_latest_url).text.strip()
    blosc_wheel_url = f"https://blosc.github.io/python-blosc2/wheels/{blosc_wheel_name}"
    await micropip.install(blosc_wheel_url)
    print(f"Installed {blosc_wheel_name} successfully!")

    # Install latest caterva2
    caterva_latest_url = "https://ironarray.github.io/Caterva2/wheels/latest.txt"
    caterva_wheel_name = requests.get(caterva_latest_url).text.strip()
    caterva_wheel_url = f"https://ironarray.github.io/Caterva2/wheels/{caterva_wheel_name}"
    await micropip.install(caterva_wheel_url)
    print(f"Installed {caterva_wheel_name} successfully!")
"""


def inject_pyodide_bootstrap_cell(content: bytes) -> bytes:
    """Inject a bootstrap cell in notebooks served through JupyterLite."""
    try:
        notebook = nbformat.reads(content.decode("utf-8"), as_version=4)
    except Exception:
        return content

    for cell in notebook.cells:
        if cell.get("metadata", {}).get("caterva2_pyodide_bootstrap"):
            return content

    bootstrap_cell = nbformat.v4.new_code_cell(PYODIDE_BOOTSTRAP_CELL_SOURCE)
    bootstrap_cell["metadata"]["caterva2_pyodide_bootstrap"] = True
    notebook.cells.insert(0, bootstrap_cell)

    file = io.StringIO()
    nbformat.write(notebook, file)
    return file.getvalue().encode("utf-8")
