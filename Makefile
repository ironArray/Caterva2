install:
	python -m venv venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt
	mkdir -p data

server:
	./venv/bin/uvicorn server:app --reload

foo:
	./venv/bin/python client.py --name=foo --sub=new

bar:
	./venv/bin/python client.py --name=bar --sub=new
