.PHONY: install bro pub sub tests-start tests-run test-stop

BIN = ./venv/bin
PID = tests/supervisord.pid


install:
	python -m venv venv
	./venv/bin/pip install -U pip
	./venv/bin/pip install -r requirements.txt
	./venv/bin/pip install -r requirements-test.txt
	mkdir -p data

bro:
	${BIN}/python src/bro.py #--loglevel=INFO

pub:
	${BIN}/python src/pub.py foo data #--loglevel=INFO

sub:
	${BIN}/python src/sub.py --loglevel=INFO


tests-start:
	if [ -f "${PID}" ]; then kill -TERM `cat ${PID}`; fi
	${BIN}/supervisord -c tests/supervisor.conf

tests-run: tests-start
	if [ -f "${PID}" ]; then kill -TERM `cat ${PID}`; fi
	${BIN}/supervisord -c tests/supervisor.conf
	sleep 2.0
	pytest tests/test.py -s
	if [ -f "${PID}" ]; then kill -TERM `cat ${PID}`; fi

tests-stop:
	if [ -f "${PID}" ]; then kill -TERM `cat ${PID}`; fi
