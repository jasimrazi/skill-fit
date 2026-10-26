.PHONY: build test lint run

build:
	docker compose build

test:
	pytest tests/ -v

lint:
	ruff check app/

run:
	uvicorn app.main:app --reload
