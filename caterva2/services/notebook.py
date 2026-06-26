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
    import micropip

    # Install blosc2 and caterva2 from PyPI; micropip picks the wheel matching the
    # running Pyodide ABI (e.g. cp314/pyemscripten_2026_0 on the 314.x line).
    #
    # The blosc2 >= floor is important: Pyodide bundles its own (often older) blosc2
    # in the distribution lock (e.g. 4.1.2 on 314.0.0), and micropip prefers a
    # bundled package over PyPI unless the requirement excludes it. The constraint
    # forces micropip to fetch a current release from PyPI instead of the stale
    # bundled one.
    await micropip.install(["blosc2>=4.6.0", "caterva2"])
    import blosc2
    import caterva2
    print(f"Installed blosc2 {blosc2.__version__} and caterva2 {caterva2.__version__} successfully!")
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
