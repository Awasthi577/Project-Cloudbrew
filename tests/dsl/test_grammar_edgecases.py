import pytest

from LCF import dsl_parser


def test_invalid_token_raises():
    bad = "create ???"
    with pytest.raises(Exception):
        dsl_parser.parse(bad)
