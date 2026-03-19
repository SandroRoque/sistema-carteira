from layout_config import CORRETORAS, FIELD_CONFIG, LAYOUT_CONFIG


KNOWN_PARSERS = {
    None,
    "br_date_parser",
    "br_number_parser",
    "cpf_parser",
    "money_parser",
    "percent_parser",
}


def test_corretora_ids_are_unique():
    corretora_ids = [corretora.id for corretora in CORRETORAS]
    assert len(corretora_ids) == len(set(corretora_ids))


def test_field_ids_are_unique():
    field_ids = [field["id"] for field in FIELD_CONFIG]
    assert len(field_ids) == len(set(field_ids))


def test_layout_ids_are_unique():
    layout_ids = [layout["id"] for layout in LAYOUT_CONFIG]
    assert len(layout_ids) == len(set(layout_ids))


def test_every_layout_references_a_known_corretora():
    corretora_ids = {corretora.id for corretora in CORRETORAS}

    for layout in LAYOUT_CONFIG:
        assert layout["corretora_id"] in corretora_ids


def test_every_binding_references_a_known_field():
    field_ids = {field["id"] for field in FIELD_CONFIG}

    for layout in LAYOUT_CONFIG:
        for group in layout["groups"]:
            for binding in group["bindings"]:
                assert binding["field_id"] in field_ids


def test_every_field_parser_is_known():
    for field in FIELD_CONFIG:
        assert field["parser_default"] in KNOWN_PARSERS
