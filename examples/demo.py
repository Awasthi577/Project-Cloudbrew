import os

from LCF.dsl_parser import parse_spec
from LCF.orchestration import create_from_spec


def main():
    spec_path = os.path.join("examples", "sample.yml")
    spec = parse_spec(spec_path)
    print("Spec loaded:", spec)
    res = create_from_spec(spec)
    print("Result:", res)


if __name__ == "__main__":
    main()
