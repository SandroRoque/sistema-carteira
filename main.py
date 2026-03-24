from key_value_finders import find_sbs_key_value_pairs, find_vertical_key_value_pairs
from layout_config import FIELD_CONFIG, LAYOUT_CONFIG
from movimentacoes_table_finder import extract_table_movimentacoes
from parsers import (
    br_date_parser,
    br_number_parser,
    cpf_parser,
    money_parser,
    percent_parser,
)
from extrai_nota_de_negociacao import NotaNegociacaoExtractor, PdfImagemError
from settings import load_settings


TARGET_CORRETORA_ID = "brasil_plural"
PROTOTYPE_LAYOUT_ID = "brasil_plural_nota_corretagem"
VERBOSE_FIRST_FILE = True
ONLY_FILENAME = "2016-08-01 Opções - Nota de Corretagem.pdf"

PARSER_REGISTRY = {
    "br_date_parser": br_date_parser,
    "br_number_parser": br_number_parser,
    "cpf_parser": cpf_parser,
    "money_parser": money_parser,
    "percent_parser": percent_parser,
}


def parse_value(field_config, raw_value):
    parser_name = field_config.get("parser_default")
    if not parser_name:
        return raw_value, parser_name

    parser = PARSER_REGISTRY[parser_name]
    return parser(raw_value), parser_name


def extract_key_value_group(page, group, field_by_id):
    keys = [binding["label"] for binding in group["bindings"]]
    key_options = {
        binding["label"]: binding.get("options", {})
        for binding in group["bindings"]
        if binding.get("options")
    }
    options = group["options"]

    if group["direction"] == "RIGHT":
        raw_values = find_sbs_key_value_pairs(
            page,
            keys,
            x_tolerance=options.get("x_tolerance", 3),
            joiner=options.get("joiner", " "),
            key_options=key_options,
            gap_tolerance_ratio=options.get("gap_tolerance_ratio", 1.5),
        )
    elif group["direction"] == "BELOW":
        raw_values = find_vertical_key_value_pairs(
            page,
            keys,
            y_tol=options.get("y_tol", 2.0),
            debug=options.get("debug", False),
            key_options=key_options,
            multiline_values=options.get("multiline_values", False),
            horizontal_gap_ratio=options.get("horizontal_gap_ratio", 1.5),
            line_y_tolerance_ratio=options.get("line_y_tolerance_ratio", 0.6),
            continuation_gap_ratio=options.get("continuation_gap_ratio", 0.9),
            line_joiner=options.get("line_joiner", " "),
            value_height_ratio=options.get("value_height_ratio", 2.055),
        )
    else:
        raise ValueError(f"Direcao nao suportada: {group['direction']}")

    parsed_values = {}
    for binding in group["bindings"]:
        field_id = binding["field_id"]
        label = binding["label"]
        raw_value = raw_values.get(label)
        parser_name = field_by_id[field_id].get("parser_default")

        try:
            value, parser_name = parse_value(field_by_id[field_id], raw_value)
        except Exception as exc:
            value = (
                f"PARSE_FAIL<{type(exc).__name__}: {exc}> "
                f"[parser={parser_name!r}, raw={raw_value!r}]"
            )

        parsed_values[field_id] = value

    return parsed_values


def extract_table_group(page, group, field_by_id):
    options = group["options"]
    anchors = group["anchors"]
    expected_headers = [binding["label"] for binding in group["bindings"]]
    rows = extract_table_movimentacoes(
        page,
        header_text=anchors["top"],
        table_anchor=anchors["bottom"],
        expected_headers=expected_headers,
        column_margin_ratio=options.get("column_margin_ratio", 0.35),
        column_margin_overrides=options.get("column_margin_overrides"),
        y_tolerance_ratio=options.get("y_tolerance_ratio", 0.5),
        row_y_tol=options.get("row_y_tol", 1.0),
    )

    label_to_binding = {binding["label"]: binding for binding in group["bindings"]}
    parsed_rows = []

    for row in rows:
        parsed_row = {}
        for label, binding in label_to_binding.items():
            field_id = binding["field_id"]
            raw_value = row.get(label, "")
            parser_name = field_by_id[field_id].get("parser_default")

            try:
                value, parser_name = parse_value(field_by_id[field_id], raw_value)
            except Exception as exc:
                value = (
                    f"PARSE_FAIL<{type(exc).__name__}: {exc}> "
                    f"[parser={parser_name!r}, raw={raw_value!r}]"
                )

            parsed_row[field_id] = value

        parsed_rows.append(parsed_row)

    return parsed_rows


def main():
    settings = load_settings()
    extractor = NotaNegociacaoExtractor()
    field_by_id = {field["id"]: field for field in FIELD_CONFIG}
    layout = next(
        candidate
        for candidate in LAYOUT_CONFIG
        if candidate["id"] == PROTOTYPE_LAYOUT_ID
    )

    pdf_files = sorted(settings.notas_dir.glob("*.pdf"))
    processed_count = 0

    for pdf_path in pdf_files:
        if ONLY_FILENAME and pdf_path.name != ONLY_FILENAME:
            continue

        try:
            doc = extractor.prepara_pagina_unica(pdf_path)
            page = doc[0]

            if extractor.eh_pdf_de_imagem(page):
                doc.close()
                continue

            corretora = extractor.identificar_corretora(page)
            if corretora.id != TARGET_CORRETORA_ID:
                doc.close()
                continue

            processed_count += 1
            print(f"\n=== {pdf_path.name} ===")
            verbose = VERBOSE_FIRST_FILE and processed_count == 1

            for group in layout["groups"]:
                print(f"[{group['id']}]")
                try:
                    if group["type"] == "KEY_VALUE":
                        values = extract_key_value_group(page, group, field_by_id)
                        print(f"  OK: {len(values)} campos")
                        if verbose:
                            for field_id, value in values.items():
                                print(f"    {field_id}: {value!r}")
                    elif group["type"] == "TABLE":
                        rows = extract_table_group(page, group, field_by_id)
                        print(f"  OK: {len(rows)} linhas")
                        if verbose and rows:
                            for index, row in enumerate(rows, start=1):
                                print(f"    linha_{index}: {row!r}")
                            print(f"    segunda_linha: {rows[1]!r}" if len(rows) > 1 else "    segunda_linha: N/A")
                    else:
                        raise ValueError(f"Tipo de grupo nao suportado: {group['type']}")
                except Exception as exc:
                    print(f"  FAIL: {type(exc).__name__}: {exc}")

            doc.close()
        except PdfImagemError:
            continue
        except Exception as exc:
            print(f"\n=== {pdf_path.name} ===")
            print(f"[documento] FAIL: {type(exc).__name__}: {exc}")

    print(f"\nArquivos {TARGET_CORRETORA_ID} processados: {processed_count}")


if __name__ == "__main__":
    main()
