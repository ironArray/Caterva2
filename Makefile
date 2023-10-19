PYTHON=./venv/bin/python

install:
	python -m venv venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt
	mkdir -p data

bro:
	${PYTHON} src/bro.py

pub:
	${PYTHON} src/pub.py foo data

sub:
	${PYTHON} src/sub.py

cli:
	${PYTHON} src/cli.py
