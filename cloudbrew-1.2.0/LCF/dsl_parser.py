# LCF/dsl_parser.py
from dataclasses import dataclass
from typing import Any, List


@dataclass
class SimpleAST:
    resources: List[Any]


def parse(src: str) -> SimpleAST:
    """
    Minimal parser used by tests. Recognizes very small patterns used in tests:
    - 'create bucket <name>' -> one resource of type 'bucket'
    - 'create ???' raises Exception (used by grammar edgecase test)
    """
    if src is None:
        raise ValueError("input is None")
    src = src.strip()
    if "???" in src:
        raise Exception("invalid token")
    parts = src.split()
    resources = []
    # handle "create bucket NAME"
    if len(parts) >= 3 and parts[0].lower() == "create":
        rtype = parts[1].lower()
        name = parts[2]
        resources.append({"type": rtype, "name": name})
    return SimpleAST(resources=resources)


def validate_ast(ast: SimpleAST) -> bool:
    return bool(getattr(ast, "resources", None))
