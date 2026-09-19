.PHONY: help install dev test clean

help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make dev        - Run local development environment"
	@echo "  make test       - Run tests"
	@echo "  make clean      - Clean up containers"

install:
	cd api && pip install -r requirements-dev.txt

dev:
	docker-compose up --build

test:
	cd api && python -m pytest -q

clean:
	docker-compose down -v
