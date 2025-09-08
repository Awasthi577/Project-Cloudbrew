from LCF import dsl_parser


def test_parse_minimal():
    src = "create bucket test-bucket"
    ast = dsl_parser.parse(src)
    assert ast is not None
    assert getattr(ast, "resources", None) is not None
