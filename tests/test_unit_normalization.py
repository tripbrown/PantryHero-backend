from services.openai_service import _normalize_unit_token, _split_quantity_unit


def test_quantity_numeric_only():
    qty, unit = _split_quantity_unit("1.25 lb")
    assert qty == "1.25"
    assert unit == "lb"


def test_unit_embedded_pattern():
    qty, unit = _split_quantity_unit("16OZ")
    assert qty == "16"
    assert unit == "oz"


def test_fridge_no_unit_keeps_none():
    qty, unit = _split_quantity_unit("1")
    assert qty == "1"
    assert unit is None


def test_normalize_units():
    assert _normalize_unit_token("lbs") == "lb"
    assert _normalize_unit_token("ct") == "count"
    assert _normalize_unit_token("each") == "ea"
