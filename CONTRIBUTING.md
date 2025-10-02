# Contributing to Ephemera

Thank you for your interest in contributing to Ephemera! This document provides guidelines and information for contributors.

## Development Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Git

### Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone git@github.com:your-username/ephemera.git
   cd ephemera
   ```

3. Set up the development environment:
   ```bash
   cp api/.env.example api/.env
   make dev
   ```

4. Run migrations:
   ```bash
   docker-compose run --rm api alembic upgrade head
   ```

5. Verify setup:
   ```bash
   curl http://localhost:8000/health
   ```

## Development Workflow

### Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following our code style
3. Test your changes locally
4. Commit with clear, descriptive messages

### Code Style

- **Python**: Follow PEP 8
- **Imports**: Use absolute imports
- **Type Hints**: Add type annotations to function signatures
- **Docstrings**: Use Google-style docstrings for functions/classes

### Running Tests

```bash
# Run tests (when implemented)
make test

# Run linting
docker-compose exec api black --check .
docker-compose exec api ruff check .
```

### Database Migrations

When modifying models:

```bash
# Create migration
docker-compose run --rm api alembic revision --autogenerate -m "description"

# Apply migration
docker-compose run --rm api alembic upgrade head
```

## Pull Request Process

1. Update documentation for any changed functionality
2. Ensure all tests pass
3. Update the README.md if needed
4. Submit PR with clear description of changes
5. Link any related issues

### PR Guidelines

- **Title**: Clear, concise description (e.g., "Add Kubernetes provisioning service")
- **Description**:
  - What changes were made
  - Why the changes were needed
  - How to test the changes
- **Size**: Keep PRs focused and reasonably sized
- **Commits**: Squash commits if needed before merging

## Issue Reporting

### Bug Reports

Include:
- Clear description of the issue
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Docker version, etc.)
- Relevant logs

### Feature Requests

Include:
- Clear use case description
- Proposed solution (if any)
- Alternative solutions considered
- Additional context

## Project Structure

```
ephemera/
├── api/              # FastAPI backend
│   ├── app/
│   │   ├── api/      # REST endpoints
│   │   ├── models/   # Database models
│   │   ├── crud/     # Database operations
│   │   └── services/ # Business logic
│   └── alembic/      # Database migrations
├── worker/           # Celery workers (planned)
├── infrastructure/   # Terraform/K8s configs (planned)
└── docs/            # Documentation
```

## Architecture Decisions

For significant changes, please:
1. Open an issue for discussion first
2. Document architectural decisions
3. Update relevant documentation

## Code Review

All submissions require review. We use GitHub pull requests for this purpose. Reviewers will check for:

- Code quality and style
- Test coverage
- Documentation updates
- Breaking changes

## Community

- Be respectful and inclusive
- Help newcomers get started
- Share knowledge and best practices
- Give constructive feedback

## Questions?

- Open an issue for questions
- Check existing documentation in `/docs`
- Review closed issues/PRs for context

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
