from LCF import dsl_parser


def test_schema_valid_example():
    good = "create bucket test-bucket"
    ast = dsl_parser.parse(good)
    assert dsl_parser.validate_ast(ast) is True
