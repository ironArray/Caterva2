install:
	python -m venv venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt
	mkdir -p data

start:
	./venv/bin/uvicorn server:app --reload
