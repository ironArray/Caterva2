Install:

    pip install jupyterlite-core
    pip install jupyterlite-pyodide-kernel

Generate static files:

    cd caterva2/services/static
    rm .jupyterlite.doit.db jupyterlite -rf
    jupyter lite build --output-dir jupyterlite

Usage:

    import js
    js.fetch(...)

    %pip install -q caterva2
    import caterva2


http://localhost:8000/static/jupyterlite/notebooks/index.html?path=01.ndarray-basics.ipynb


/static/jupyterlite/
  notebooks/index.html?path=01.ndarray-basics.ipynb
  api/contents/all.json
  api/contents/blosc/all.json
  overrides.json





http://localhost:8000/static/jupyterlite/api/contents/all.json

http://127.0.0.1:8000/api/contents/all.json

    {
      "content": [
        {
          "content": null,
          "created": "2025-01-21T12:25:19.741116Z",
          "format": null,
          "hash": null,
          "hash_algorithm": null,
          "last_modified": "2025-01-21T12:23:38.424439Z",
          "mimetype": null,
          "name": "02.02-The-Basics-Of-NumPy-Arrays.ipynb",
          "path": "02.02-The-Basics-Of-NumPy-Arrays.ipynb",
          "size": 32798,
          "type": "notebook",
          "writable": true
        },
        {
          "content": null,
          "created": "2025-01-21T12:25:19.741116Z",
          "format": null,
          "hash": null,
          "hash_algorithm": null,
          "last_modified": "2025-01-21T12:25:19.741116Z",
          "mimetype": null,
          "name": "blosc",
          "path": "blosc",
          "size": null,
          "type": "directory",
          "writable": true
        }
      ],
      "created": "2025-01-21T12:25:19.741116Z",
      "format": "json",
      "hash": null,
      "hash_algorithm": null,
      "last_modified": "2025-01-21T12:25:19.741116Z",
      "mimetype": null,
      "name": "",
      "path": "",
      "size": null,
      "type": "directory",
      "writable": true
    }

http://127.0.0.1:8000/api/contents/blosc/all.json

    {
      "content": [
        {
          "content": null,
          "created": "2025-01-21T12:25:19.741116Z",
          "format": null,
          "hash": null,
          "hash_algorithm": null,
          "last_modified": "2025-01-21T11:58:04.096061Z",
          "mimetype": null,
          "name": "01.ndarray-basics.ipynb",
          "path": "blosc/01.ndarray-basics.ipynb",
          "size": 24683,
          "type": "notebook",
          "writable": true
        }
      ],
      "created": "2025-01-21T12:25:19.741116Z",
      "format": "json",
      "hash": null,
      "hash_algorithm": null,
      "last_modified": "2025-01-21T12:25:19.741116Z",
      "mimetype": null,
      "name": "blosc",
      "path": "blosc",
      "size": null,
      "type": "directory",
      "writable": true
    }


http://127.0.0.1:8000/files/01.ndarray-basics.ipynb
