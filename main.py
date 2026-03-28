import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from extrai_nota_de_negociacao import NotaNegociacaoExtractor
from key_value_finders import find_sbs_key_value_pairs, find_vertical_key_value_pairs
from layout_config import CORRETORAS, FIELD_CONFIG, LAYOUT_CONFIG
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

KNOWN_GROUP_TYPES = {"KEY_VALUE", "TABLE"}
KNOWN_DIRECTIONS = {"RIGHT", "BELOW"}
KNOWN_BINDING_OPTION_KEYS = {"occurrence_index"}


@dataclass(frozen=True)
class GroupError:
    group_id: str
    error_type: str
    message: str

    def format(self) -> str:
        return f"{self.group_id}: {self.error_type}: {self.message}"


@dataclass(frozen=True)
class ParseIssue:
    field_id: str
    parser_name: str | None
    raw_value: object
    error_type: str
    message: str

    def format_for_prototype(self) -> str:
        return (
            f"PARSE_FAIL<{self.error_type}: {self.message}> "
            f"[parser={self.parser_name!r}, raw={self.raw_value!r}]"
        )


@dataclass
class ExtractionResult:
    receipt_row: dict[str, str]
    movimentacao_rows: list[dict[str, str]]
    status: str
    corretora_id: str = ""
    layout_id: str = ""
    group_errors: list[GroupError] = field(default_factory=list)
    parse_issues: list[ParseIssue] = field(default_factory=list)

    @property
    def error_message(self) -> str:
        messages = [error.format() for error in self.group_errors]
        return " | ".join(messages)


def stringify_value(value):
    if value is None:
        return ""
    return str(value)


def field_by_id():
    return {field["id"]: field for field in FIELD_CONFIG}


def find_duplicate_ids(ids):
    seen = set()
    duplicates = set()

    for item_id in ids:
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)

    return sorted(duplicates)


def scalar_field_ids():
    table_ids = set(table_field_ids())
    return [field["id"] for field in FIELD_CONFIG if field["id"] not in table_ids]


def table_field_ids():
    field_ids = []
    for field in FIELD_CONFIG:
        if any(
            group["type"] == "TABLE"
            and any(binding["field_id"] == field["id"] for binding in group["bindings"])
            for layout in LAYOUT_CONFIG
            for group in layout["groups"]
        ):
            field_ids.append(field["id"])
    return field_ids


def validate_runtime_config():
    corretora_ids = {corretora.id for corretora in CORRETORAS}
    field_ids = {field["id"] for field in FIELD_CONFIG}

    duplicate_field_ids = find_duplicate_ids(field["id"] for field in FIELD_CONFIG)
    if duplicate_field_ids:
        raise ValueError(f"FIELD_CONFIG possui ids duplicados: {duplicate_field_ids}")

    duplicate_layout_ids = find_duplicate_ids(layout["id"] for layout in LAYOUT_CONFIG)
    if duplicate_layout_ids:
        raise ValueError(f"LAYOUT_CONFIG possui ids duplicados: {duplicate_layout_ids}")

    for field in FIELD_CONFIG:
        parser_name = field.get("parser_default")
        if parser_name not in PARSER_REGISTRY and parser_name is not None:
            raise ValueError(f"Parser desconhecido no campo '{field['id']}': {parser_name!r}")

    for layout in LAYOUT_CONFIG:
        if layout["corretora_id"] not in corretora_ids:
            raise ValueError(
                f"Layout '{layout['id']}' referencia corretora desconhecida: "
                f"{layout['corretora_id']!r}"
            )

        for group in layout["groups"]:
            group_type = group.get("type")
            if group_type not in KNOWN_GROUP_TYPES:
                raise ValueError(
                    f"Layout '{layout['id']}', grupo '{group['id']}' com tipo invalido: "
                    f"{group_type!r}"
                )

            if "bindings" not in group or not group["bindings"]:
                raise ValueError(
                    f"Layout '{layout['id']}', grupo '{group['id']}' sem bindings."
                )

            if group_type == "KEY_VALUE":
                direction = group.get("direction")
                if direction not in KNOWN_DIRECTIONS:
                    raise ValueError(
                        f"Layout '{layout['id']}', grupo '{group['id']}' com direction "
                        f"invalida: {direction!r}"
                    )
            elif group_type == "TABLE":
                anchors = group.get("anchors", {})
                if "top" not in anchors or "bottom" not in anchors:
                    raise ValueError(
                        f"Layout '{layout['id']}', grupo '{group['id']}' precisa de "
                        "anchors.top e anchors.bottom."
                    )

            for binding in group["bindings"]:
                field_id = binding.get("field_id")
                if field_id not in field_ids:
                    raise ValueError(
                        f"Layout '{layout['id']}', grupo '{group['id']}' referencia campo "
                        f"desconhecido: {field_id!r}"
                    )

                label = binding.get("label")
                if not label:
                    raise ValueError(
                        f"Layout '{layout['id']}', grupo '{group['id']}' possui binding "
                        f"sem label para campo '{field_id}'."
                    )

                option_keys = set(binding.get("options", {}))
                unknown_option_keys = option_keys - KNOWN_BINDING_OPTION_KEYS
                if unknown_option_keys:
                    raise ValueError(
                        f"Layout '{layout['id']}', grupo '{group['id']}', binding "
                        f"'{field_id}' possui opcoes desconhecidas: "
                        f"{sorted(unknown_option_keys)}"
                    )


def parse_value(field_config, raw_value):
    parser_name = field_config.get("parser_default")
    if not parser_name:
        return raw_value, parser_name

    parser = PARSER_REGISTRY[parser_name]
    return parser(raw_value), parser_name


def score_layout(page, layout):
    score = 0
    debug_matches = []

    for group in layout["groups"]:
        if group["type"] == "KEY_VALUE":
            for binding in group["bindings"]:
                if page.search_for(binding["label"]):
                    score += 1
                    debug_matches.append(f"label:{binding['label']}")
        elif group["type"] == "TABLE":
            anchors = group["anchors"]
            if page.search_for(anchors["top"]):
                score += 3
                debug_matches.append(f"table_top:{anchors['top']}")
            if page.search_for(anchors["bottom"]):
                score += 3
                debug_matches.append(f"table_bottom:{anchors['bottom']}")
            for binding in group["bindings"]:
                if page.search_for(binding["label"]):
                    score += 1
                    debug_matches.append(f"header:{binding['label']}")

    return score, debug_matches


def identify_layout(page, corretora, debug=False):
    candidate_layouts = [
        layout for layout in LAYOUT_CONFIG if layout["corretora_id"] == corretora.id
    ]
    if not candidate_layouts:
        raise ValueError(f"Nenhum layout configurado para a corretora '{corretora.id}'.")

    scored_layouts = []
    for layout in candidate_layouts:
        score, matches = score_layout(page, layout)
        scored_layouts.append((score, layout["id"], layout, matches))

    scored_layouts.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, _, best_layout, best_matches = scored_layouts[0]

    if debug:
        print("  layout_scores:")
        for score, layout_id, _, matches in scored_layouts:
            sample = ", ".join(matches[:5]) if matches else "sem matches"
            print(f"    {layout_id}: score={score} ({sample})")

    if best_score <= 0:
        raise ValueError(f"Nenhum layout reconhecido para a corretora '{corretora.id}'.")

    if debug:
        print(
            f"  layout_escolhido: {best_layout['id']} "
            f"(score={best_score}, matches={len(best_matches)})"
        )

    return best_layout


def parse_field_value(field_config, raw_value):
    parser_name = field_config.get("parser_default")
    try:
        value, parser_name = parse_value(field_config, raw_value)
        return value, None
    except Exception as exc:
        issue = ParseIssue(
            field_id=field_config["id"],
            parser_name=parser_name,
            raw_value=raw_value,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return raw_value, issue


def extract_key_value_group(page, group, field_configs):
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
    parse_issues = []
    for binding in group["bindings"]:
        field_id = binding["field_id"]
        label = binding["label"]
        raw_value = raw_values.get(label)
        value, issue = parse_field_value(field_configs[field_id], raw_value)
        parsed_values[field_id] = value
        if issue:
            parse_issues.append(issue)

    return parsed_values, parse_issues


def extract_table_group(page, group, field_configs):
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
    parse_issues = []

    for row in rows:
        parsed_row = {}
        for label, binding in label_to_binding.items():
            field_id = binding["field_id"]
            raw_value = row.get(label, "")
            value, issue = parse_field_value(field_configs[field_id], raw_value)
            parsed_row[field_id] = value
            if issue:
                parse_issues.append(issue)
        parsed_rows.append(parsed_row)

    return parsed_rows, parse_issues


def build_base_receipt_row(pdf_path):
    row = {
        "receipt_id": pdf_path.stem,
        "filename": pdf_path.name,
        "source_path": str(pdf_path),
        "corretora_id": "",
        "layout_id": "",
        "status": "",
        "error": "",
    }
    for field_id in scalar_field_ids():
        row[field_id] = ""
    return row


def extract_document(pdf_path, extractor, field_configs, debug_layout=False):
    receipt_row = build_base_receipt_row(pdf_path)
    movimentacao_rows = []
    parse_issues = []
    group_errors = []
    doc = None

    try:
        doc = extractor.prepara_pagina_unica(pdf_path)
        page = doc[0]

        if extractor.eh_pdf_de_imagem(page):
            return ExtractionResult(
                receipt_row=receipt_row,
                movimentacao_rows=[],
                status="skipped_image",
                group_errors=[
                    GroupError(
                        group_id="documento",
                        error_type="PdfImagemError",
                        message=(
                            "PDF sem texto extraivel; provavelmente arquivo escaneado "
                            "ou de imagem."
                        ),
                    )
                ],
            )

        corretora = extractor.identificar_corretora(page)
        layout = identify_layout(page, corretora, debug=debug_layout)
        receipt_row["corretora_id"] = corretora.id
        receipt_row["layout_id"] = layout["id"]

        for group in layout["groups"]:
            try:
                if group["type"] == "KEY_VALUE":
                    parsed_values, issues = extract_key_value_group(
                        page,
                        group,
                        field_configs,
                    )
                    for field_id, value in parsed_values.items():
                        receipt_row[field_id] = stringify_value(value)
                    parse_issues.extend(issues)
                elif group["type"] == "TABLE":
                    parsed_rows, issues = extract_table_group(page, group, field_configs)
                    parse_issues.extend(issues)

                    for parsed_row in parsed_rows:
                        movimentacao_row = {"receipt_id": receipt_row["receipt_id"]}
                        for field_id in table_field_ids():
                            movimentacao_row[field_id] = ""
                        for field_id, value in parsed_row.items():
                            movimentacao_row[field_id] = stringify_value(value)
                        movimentacao_rows.append(movimentacao_row)
                else:
                    raise ValueError(f"Tipo de grupo nao suportado: {group['type']}")
            except Exception as exc:
                group_errors.append(
                    GroupError(
                        group_id=group["id"],
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )

        status = "parsed" if not group_errors else "partial"
        return ExtractionResult(
            receipt_row=receipt_row,
            movimentacao_rows=movimentacao_rows,
            status=status,
            corretora_id=corretora.id,
            layout_id=layout["id"],
            group_errors=group_errors,
            parse_issues=parse_issues,
        )
    except Exception as exc:
        return ExtractionResult(
            receipt_row=receipt_row,
            movimentacao_rows=[],
            status="failed",
            group_errors=[
                GroupError(
                    group_id="documento",
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            ],
            parse_issues=parse_issues,
        )
    finally:
        if doc is not None:
            doc.close()


def apply_result_to_receipt_row(result):
    receipt_row = dict(result.receipt_row)
    receipt_row["corretora_id"] = result.corretora_id
    receipt_row["layout_id"] = result.layout_id
    receipt_row["status"] = result.status
    receipt_row["error"] = result.error_message
    return receipt_row


def run_export():
    validate_runtime_config()
    settings = load_settings()
    extractor = NotaNegociacaoExtractor()
    field_configs = field_by_id()

    project_root = Path(__file__).resolve().parent
    output_dir = project_root / "exports"
    output_dir.mkdir(exist_ok=True)
    workbook_path = output_dir / "extracao_notas.xlsx"

    receipt_rows = []
    movimentacao_rows = []

    for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
        result = extract_document(pdf_path, extractor, field_configs)
        receipt_rows.append(apply_result_to_receipt_row(result))
        movimentacao_rows.extend(result.movimentacao_rows)

    receipt_headers = [
        "receipt_id",
        "filename",
        "source_path",
        "corretora_id",
        "layout_id",
        "status",
        "error",
        *scalar_field_ids(),
    ]
    movimentacao_headers = ["receipt_id", *table_field_ids()]

    receipts_df = pd.DataFrame(receipt_rows, columns=receipt_headers)
    movimentacoes_df = pd.DataFrame(movimentacao_rows, columns=movimentacao_headers)

    with pd.ExcelWriter(workbook_path) as writer:
        receipts_df.to_excel(writer, sheet_name="notas", index=False)
        movimentacoes_df.to_excel(writer, sheet_name="movimentacoes", index=False)

    parsed_count = sum(1 for row in receipt_rows if row["status"] == "parsed")
    partial_count = sum(1 for row in receipt_rows if row["status"] == "partial")
    skipped_count = sum(1 for row in receipt_rows if row["status"] == "skipped_image")
    failed_count = sum(1 for row in receipt_rows if row["status"] == "failed")

    print(f"Workbook XLSX: {workbook_path}")
    print(
        "Resumo:"
        f" parsed={parsed_count}"
        f" partial={partial_count}"
        f" skipped_image={skipped_count}"
        f" failed={failed_count}"
        f" movimentacoes={len(movimentacao_rows)}"
    )


def run_prototype(
    *,
    filenames=None,
    corretora_id=None,
    layout_id=None,
    verbose_first_file=True,
):
    validate_runtime_config()
    settings = load_settings()
    extractor = NotaNegociacaoExtractor()
    field_configs = field_by_id()
    selected_filenames = set(filenames or settings.prototype_pdf_names)
    processed_count = 0

    for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
        if selected_filenames and pdf_path.name not in selected_filenames:
            continue

        result = extract_document(
            pdf_path,
            extractor,
            field_configs,
            debug_layout=True,
        )

        if corretora_id and result.corretora_id != corretora_id:
            continue
        if layout_id and result.layout_id != layout_id:
            continue

        processed_count += 1
        verbose = verbose_first_file and processed_count == 1
        print(f"\n=== {pdf_path.name} ===")
        print(f"status: {result.status}")
        print(f"corretora_id: {result.corretora_id or 'N/A'}")
        print(f"layout_id: {result.layout_id or 'N/A'}")

        if result.group_errors:
            print("group_errors:")
            for group_error in result.group_errors:
                print(f"  {group_error.format()}")

        if verbose:
            print("campos:")
            for field_id in scalar_field_ids():
                value = result.receipt_row.get(field_id, "")
                if value:
                    print(f"  {field_id}: {value!r}")

            if result.movimentacao_rows:
                print("movimentacoes:")
                for index, row in enumerate(result.movimentacao_rows, start=1):
                    print(f"  linha_{index}: {row!r}")

        if result.parse_issues:
            print("parse_issues:")
            for issue in result.parse_issues:
                print(f"  {issue.field_id}: {issue.format_for_prototype()}")

    print(f"\nArquivos processados no modo prototipo: {processed_count}")


def build_argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("export", "prototype"),
        default="prototype",
    )
    parser.add_argument("--filename", action="append", default=[])
    parser.add_argument("--corretora-id")
    parser.add_argument("--layout-id")
    parser.add_argument(
        "--quiet-prototype",
        action="store_true",
        help="Nao imprime os detalhes completos do primeiro arquivo no modo prototype.",
    )
    return parser


def main():
    parser = build_argument_parser()
    args = parser.parse_args()

    if args.mode == "export":
        run_export()
        return

    run_prototype(
        filenames=args.filename or None,
        corretora_id=args.corretora_id,
        layout_id=args.layout_id,
        verbose_first_file=not args.quiet_prototype,
    )


if __name__ == "__main__":
    main()
