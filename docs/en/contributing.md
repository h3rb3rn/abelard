# Contributing Guide

## Development Workflow

### 1. Branch Strategy
```bash
git checkout -b feature/short-description   # New features
git checkout -b fix/short-description       # Bug fixes
```

### 2. Code Guidelines
- Follow clean code principles (keep functions concise, utilize early returns).
- Comprehensive type hinting across all Python modules (`def function(name: str) -> bool:`).
- English identifiers, function names, and docstrings.
- Avoid magic numbers — declare named constants.

### 3. Running Test Suite
```bash
# Unit tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=. --cov-report=term-missing
```

### 4. Updating Documentation
- New features → update corresponding markdown pages under `docs/en/` and `docs/de/`, and update `mkdocs.yml` navigation.

## Code Quality Gate Checklist

- [ ] Clean code principles followed?
- [ ] Type hints present on all functions?
- [ ] Unit & integration tests added/updated?
- [ ] Documentation updated in both English (`docs/en/`) and German (`docs/de/`)?
- [ ] Zero secrets in source files or test fixtures?
- [ ] Docker stack starts without errors?
