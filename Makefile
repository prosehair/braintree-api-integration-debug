LINTER=.venv/bin/ruff
LINTER_DEEP=.venv/bin/pylint


.EXPORT_ALL_VARIABLES:
PIPENV_VENV_IN_PROJECT=1
PIPENV_DEFAULT_PYTHON_VERSION=3.10.8

all: help


venv:  ## Create and initialize a local virtual env
	rm -rf venv .venv
	python -m venv .venv
	.venv/bin/pip install -U --disable-pip-version-check pip pipenv
	bash -c '.venv/bin/pip install -r <(.venv/bin/pipenv requirements)'
	bash -c ' .venv/bin/pip install -r <(.venv/bin/pipenv requirements --dev-only)'
	cp Pipfile.lock .Pipfile.lock.installed && cp Pipfile .Pipfile.installed

test:  ## Run tests
	DJANGO_SETTINGS_MODULE=prose.settings pipenv run pytest prose/test_braintree_lite.py
