.PHONY: install assets lite-ext lite-build lite-dev lite-test run

VENV = ./venv
# Override to use a different toolchain, e.g. a conda env already on PATH:
#   make lite-build BIN=$(dirname $(which jupyter))
BIN ?= $(VENV)/bin

# Repo-local Jupyter search path for the caterva2-save labextension during
# development (see lite-ext/lite-build below). Keeping it outside the active
# environment's own share/jupyter/labextensions/ means it never collides with
# what `pip install` puts there, so no manual cleanup is ever needed between
# the two.
DEV_JUPYTER_PATH = $(CURDIR)/jupyterlite-exts/.dev-jupyter-path

install:
	python3 -m venv venv
	${BIN}/pip install -U pip
	${BIN}/pip install -e .
	${BIN}/pip install -e .[services,hdf5,plugins,blosc2-plugins,lite]
	${BIN}/pip install -e .[clients]
	${BIN}/pip install -e .[tests]
	${BIN}/pip install pre-commit

assets:
	rm caterva2/services/static/build/*
	npm run build
	git add caterva2/services/static/build/


# Build the caterva2-save JupyterLite extension (needs Node + jlpm/JupyterLab).
# Commit the resulting jupyterlite-exts/caterva2-save/labextension/ so the
# caterva2 wheel ships it and downstream installs need no Node toolchain.
# Registered for local `jupyter lite build` runs via DEV_JUPYTER_PATH (see
# lite-build) instead of the environment's own share/jupyter/labextensions/:
# an *editable* caterva2 install doesn't stage shared-data there at all, and a
# real wheel install does — either way, writing there ourselves risks
# colliding with what `pip install` manages.
lite-ext:
	cd jupyterlite-exts/caterva2-save && jlpm install && jlpm build:prod
	mkdir -p $(DEV_JUPYTER_PATH)/labextensions
	ln -sfn $(CURDIR)/jupyterlite-exts/caterva2-save/labextension $(DEV_JUPYTER_PATH)/labextensions/caterva2-save
	git add jupyterlite-exts/caterva2-save/labextension

lite-build:
	rm -rf .jupyterlite.doit.db caterva2/services/static/jupyterlite
	# --lite-dir points at the in-package jupyter-lite.json (contentsAllJsonFile),
	# so the build picks it up regardless of the working directory (the deploy
	# builds from elsewhere). See caterva2/services/lite-config/.
	# JUPYTER_PATH adds the dev labextension (see lite-ext) on top of whatever
	# is really installed; harmless/no-op if lite-ext was never run.
	JUPYTER_PATH=$(DEV_JUPYTER_PATH):$$JUPYTER_PATH \
		${BIN}/jupyter lite build --lite-dir caterva2/services/lite-config --output-dir caterva2/services/static/jupyterlite

# Installs our jupyterlite fork from a local copy, for development purposes
# Before doing this you must run "make build" in our jupyterlite fork
lite-dev:
	${BIN}/pip uninstall jupyterlite jupyterlite-core -y
	${BIN}/pip install ../jupyterlite/dist/jupyterlite_core-*.whl
	${BIN}/pip install ../jupyterlite/dist/jupyterlite-*.whl
	$(MAKE) lite-build

# Installs our jupyterlite fork from github, useful to test before deployment
lite-test:
	rm downloads -rf
	gh run -R ironArray/jupyterlite download -n "caterva2 dist" --dir downloads
	${BIN}/pip install --force-reinstall downloads/*.whl
	$(MAKE) lite-build


# To run the server, for convenience
run:
	${BIN}/python3 -m caterva2.services.server
