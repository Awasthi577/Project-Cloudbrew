# LCF/api_handler.py
def validate_input(payload: dict) -> bool:
    """
    Minimal validation used by tests:
    Return False for empty inputs, True for dicts that look like DSL/API payloads.
    Keep this lightweight — expand validation later.
    """
    if not isinstance(payload, dict):
        return False
    # consider valid if it has 'resources' or at least one key
    if "resources" in payload:
        return True
    return bool(payload)
