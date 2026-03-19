import csv
from pathlib import Path

from extrai_nota_de_negociacao import NotaNegociacaoExtractor
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
from settings import load_settings


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
        return raw_value
    return PARSER_REGISTRY[parser_name](raw_value)


def score_layout(page, layout):
    score = 0

    for group in layout["groups"]:
        if group["type"] == "KEY_VALUE":
            for binding in group["bindings"]:
                if page.search_for(binding["label"]):
                    score += 1
        elif group["type"] == "TABLE":
            anchors = group["anchors"]
            if page.search_for(anchors["top"]):
                score += 3
            if page.search_for(anchors["bottom"]):
                score += 3
            for binding in group["bindings"]:
                if page.search_for(binding["label"]):
                    score += 1

    return score


def identificar_layout(page, corretora):
    candidate_layouts = [
        layout for layout in LAYOUT_CONFIG if layout["corretora_id"] == corretora.id
    ]
    if not candidate_layouts:
        raise ValueError(f"Nenhum layout configurado para a corretora '{corretora.id}'.")

    scored_layouts = [
        (score_layout(page, layout), layout["id"], layout) for layout in candidate_layouts
    ]
    scored_layouts.sort(key=lambda item: (item[0], item[1]), reverse=True)

    best_score, _, best_layout = scored_layouts[0]
    if best_score <= 0:
        raise ValueError(f"Nenhum layout reconhecido para a corretora '{corretora.id}'.")

    return best_layout


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
        parsed_values[field_id] = parse_value(field_by_id[field_id], raw_value)

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
        y_tolerance_ratio=options.get("y_tolerance_ratio", 0.5),
        row_y_tol=options.get("row_y_tol", 1.0),
    )

    parsed_rows = []
    for row in rows:
        parsed_row = {}
        for binding in group["bindings"]:
            field_id = binding["field_id"]
            raw_value = row.get(binding["label"], "")
            parsed_row[field_id] = parse_value(field_by_id[field_id], raw_value)
        parsed_rows.append(parsed_row)

    return parsed_rows


def stringify_value(value):
    if value is None:
        return ""
    return str(value)


def guess_document_type(filename):
    normalized = filename.lower()

    if "darf" in normalized:
        return "darf"
    if "títulos" in normalized or "titulos" in normalized:
        return "titulos"
    if "nota de corretagem" in normalized:
        return "nota_de_corretagem"
    if "nota de negociação" in normalized or "nota de negociacao" in normalized:
        return "nota_de_negociacao"
    if "opção" in normalized or "opcao" in normalized or "opções" in normalized or "opcoes" in normalized:
        return "derivativos_ou_opcoes"

    return "desconhecido"


def main():
    settings = load_settings()
    extractor = NotaNegociacaoExtractor()
    field_by_id = {field["id"]: field for field in FIELD_CONFIG}
    scalar_field_ids = [field["id"] for field in FIELD_CONFIG]

    project_root = Path(__file__).resolve().parent
    output_dir = project_root / "exports"
    output_dir.mkdir(exist_ok=True)

    receipts_csv_path = output_dir / "receipts.csv"
    movimentacoes_csv_path = output_dir / "movimentacoes.csv"

    receipt_rows = []
    movimentacao_rows = []

    for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
        receipt_id = pdf_path.stem
        receipt_row = {
            "receipt_id": receipt_id,
            "filename": pdf_path.name,
            "source_path": str(pdf_path),
            "document_guess": guess_document_type(pdf_path.name),
            "corretora_id": "",
            "layout_id": "",
            "status": "",
            "error": "",
        }
        for field_id in scalar_field_ids:
            receipt_row[field_id] = ""

        try:
            doc = extractor.prepara_pagina_unica(pdf_path)
            page = doc[0]

            if extractor.eh_pdf_de_imagem(page):
                receipt_row["status"] = "skipped_image"
                receipt_row["error"] = "PDF sem texto extraivel; provavelmente arquivo escaneado ou de imagem."
                receipt_rows.append(receipt_row)
                doc.close()
                continue

            corretora = extractor.identificar_corretora(page)
            layout = identificar_layout(page, corretora)
            receipt_row["corretora_id"] = corretora.id
            receipt_row["layout_id"] = layout["id"]

            group_errors = []
            for group in layout["groups"]:
                try:
                    if group["type"] == "KEY_VALUE":
                        parsed_values = extract_key_value_group(page, group, field_by_id)
                        for field_id, value in parsed_values.items():
                            receipt_row[field_id] = stringify_value(value)
                    elif group["type"] == "TABLE":
                        parsed_rows = extract_table_group(page, group, field_by_id)
                        for index, parsed_row in enumerate(parsed_rows, start=1):
                            movimentacao_row = {
                                "receipt_id": receipt_id,
                                "filename": pdf_path.name,
                                "corretora_id": corretora.id,
                                "layout_id": layout["id"],
                                "group_id": group["id"],
                                "row_number": index,
                            }
                            for field_id in scalar_field_ids:
                                movimentacao_row[field_id] = ""
                            for field_id, value in parsed_row.items():
                                movimentacao_row[field_id] = stringify_value(value)
                            movimentacao_rows.append(movimentacao_row)
                    else:
                        raise ValueError(f"Tipo de grupo nao suportado: {group['type']}")
                except Exception as exc:
                    group_errors.append(f"{group['id']}: {type(exc).__name__}: {exc}")

            receipt_row["status"] = "parsed" if not group_errors else "partial"
            receipt_row["error"] = " | ".join(group_errors)
            receipt_rows.append(receipt_row)
            doc.close()
        except Exception as exc:
            receipt_row["status"] = "failed"
            receipt_row["error"] = f"{type(exc).__name__}: {exc}"
            receipt_rows.append(receipt_row)

    receipt_headers = [
        "receipt_id",
        "filename",
        "source_path",
        "document_guess",
        "corretora_id",
        "layout_id",
        "status",
        "error",
        *scalar_field_ids,
    ]

    movimentacao_headers = [
        "receipt_id",
        "filename",
        "corretora_id",
        "layout_id",
        "group_id",
        "row_number",
        *scalar_field_ids,
    ]

    with receipts_csv_path.open("w", newline="", encoding="utf-8") as receipts_file:
        writer = csv.DictWriter(receipts_file, fieldnames=receipt_headers)
        writer.writeheader()
        writer.writerows(receipt_rows)

    with movimentacoes_csv_path.open("w", newline="", encoding="utf-8") as movimentacoes_file:
        writer = csv.DictWriter(movimentacoes_file, fieldnames=movimentacao_headers)
        writer.writeheader()
        writer.writerows(movimentacao_rows)

    parsed_count = sum(1 for row in receipt_rows if row["status"] == "parsed")
    partial_count = sum(1 for row in receipt_rows if row["status"] == "partial")
    skipped_count = sum(1 for row in receipt_rows if row["status"] == "skipped_image")
    failed_count = sum(1 for row in receipt_rows if row["status"] == "failed")

    print(f"Receipts CSV: {receipts_csv_path}")
    print(f"Movimentacoes CSV: {movimentacoes_csv_path}")
    print(
        "Resumo:"
        f" parsed={parsed_count}"
        f" partial={partial_count}"
        f" skipped_image={skipped_count}"
        f" failed={failed_count}"
        f" movimentacoes={len(movimentacao_rows)}"
    )


if __name__ == "__main__":
    main()
