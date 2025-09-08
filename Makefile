.PHONY: fmt lint typecheck test security precommit prebuild


fmt:
black .
isort .


lint:
ruff check .


typecheck:
mypy LCF/


test:
pytest tests/unit -q


security:
bandit -r LCF/
pip-audit --progress


precommit:
pre-commit run --all-files


prebuild: fmt lint typecheck test security precommit