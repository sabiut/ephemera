.PHONY: help install dev test clean

help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make dev        - Run local development environment"
	@echo "  make test       - Run tests"
	@echo "  make clean      - Clean up containers"

install:
	cd api && pip install -r requirements.txt

dev:
	docker-compose up --build

test:
	pytest tests/

clean:
	docker-compose down -v
