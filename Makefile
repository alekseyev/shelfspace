.PHONY: help install format run process-all

install: ## Install packages from requirements.txt
	test -d .venv || uv venv --python $(cat .python-version)
	. .venv/bin/activate
	uv sync
	playwright install

format: ## Format with ruff
	ruff format .
	ruff check --fix .

run: ## Run the application
	. .venv/bin/activate
	SET_DEBUG=1 python -m shelfspace.gui_main

process-trakt: ## Sync all the things
	. .venv/bin/activate
	python shelf.py process-movies
	python shelf.py process-shows
	python shelf.py process-upcoming
	python shelf.py process-watched
	python shelf.py update-trakt-lists

process-all: ## Sync all the things
	. .venv/bin/activate
	python shelf.py process-movies
	python shelf.py process-shows
	python shelf.py process-upcoming
	python shelf.py process-watched
	python shelf.py update-trakt-lists
	python shelf.py process-books
	python shelf.py process-games

help: ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
