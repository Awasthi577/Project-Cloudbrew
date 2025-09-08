black .
isort .
ruff check .
mypy LCF/
pytest tests/unit -q
bandit -r LCF/
pip-audit --progress
pre-commit run --all-files
