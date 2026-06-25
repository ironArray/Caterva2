import nbformat

from caterva2.services.notebook import inject_pyodide_bootstrap_cell


def _bootstrap_cells(notebook):
    return [cell for cell in notebook.cells if cell.get("metadata", {}).get("caterva2_pyodide_bootstrap")]


def test_inject_pyodide_bootstrap_cell_adds_cell_once():
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("print('hello')")])
    content = nbformat.writes(notebook).encode("utf-8")

    patched = inject_pyodide_bootstrap_cell(content)
    patched_notebook = nbformat.reads(patched.decode("utf-8"), as_version=4)

    assert len(_bootstrap_cells(patched_notebook)) == 1
    assert patched_notebook.cells[0]["metadata"]["caterva2_pyodide_bootstrap"] is True


def test_inject_pyodide_bootstrap_cell_is_idempotent():
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell("hello")])
    content = nbformat.writes(notebook).encode("utf-8")

    patched_once = inject_pyodide_bootstrap_cell(content)
    patched_twice = inject_pyodide_bootstrap_cell(patched_once)
    patched_notebook = nbformat.reads(patched_twice.decode("utf-8"), as_version=4)

    assert len(_bootstrap_cells(patched_notebook)) == 1
