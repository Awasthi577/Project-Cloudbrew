from LCF import api_handler


def test_api_validate_input():
    bad = {}
    assert not api_handler.validate_input(bad)
