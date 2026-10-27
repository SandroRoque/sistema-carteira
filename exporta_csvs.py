import dataclasses
from pathlib import Path

import pandas as pd

from extrai_nota_de_negociacao import (
    PdfImagemError,
    eh_pdf_de_imagem,
    extrair,
    prepara_pagina_unica,
)
from models import Movimentacao, NotaCorretagem, TituloPrivado, TituloPublico
from settings import load_settings


def _nota_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(NotaCorretagem) if f.name != "movimentacoes"]


def _titulo_publico_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(TituloPublico)]


def _titulo_privado_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(TituloPrivado)]


def _mov_fields() -> list[str]:
    return [f.name for f in dataclasses.fields(Movimentacao)]


# Fields that are handled as pipeline metadata columns and must not appear again
# in the data section — even if a dataclass happens to have the same field name.
_PIPELINE_COLUMNS = {"receipt_id", "filename", "source_path", "corretora_id", "doc_type", "status", "error"}

# Build the union of all receipt-level field names, preserving insertion order
# and deduplicating fields that appear in more than one document type.
_ALL_RECEIPT_FIELDS: list[str] = list(
    dict.fromkeys(
        f for f in _nota_fields() + _titulo_publico_fields() + _titulo_privado_fields()
        if f not in _PIPELINE_COLUMNS
    )
)

RECEIPT_HEADERS = ["receipt_id", "filename", "source_path", "corretora_id", "doc_type", "status", "error",
                   *_ALL_RECEIPT_FIELDS]
MOV_HEADERS = ["receipt_id", *_mov_fields()]


def main():
    settings = load_settings()

    project_root = Path(__file__).resolve().parent
    output_dir = project_root / "exports"
    output_dir.mkdir(exist_ok=True)
    workbook_path = output_dir / "extracao_notas.xlsx"

    receipt_rows: list[dict] = []
    movimentacao_rows: list[dict] = []

    for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
        receipt_id = pdf_path.stem
        receipt_row: dict = {
            "receipt_id": receipt_id,
            "filename": pdf_path.name,
            "source_path": str(pdf_path),
        }

        try:
            doc = prepara_pagina_unica(pdf_path)
            page = doc[0]

            if eh_pdf_de_imagem(page):
                doc.close()
                receipt_row["status"] = "skipped_image"
                receipt_row["error"] = "PDF sem texto extraível; provavelmente arquivo escaneado."
                receipt_rows.append(receipt_row)
                continue

            doc.close()
            resultado = extrair(pdf_path)
            resultado_dict = dataclasses.asdict(resultado)

            if isinstance(resultado, NotaCorretagem):
                movimentacoes_data = resultado_dict.pop("movimentacoes")
                receipt_row.update(resultado_dict)
                for mov in movimentacoes_data:
                    mov_row = {"receipt_id": receipt_id}
                    mov_row.update(mov)
                    movimentacao_rows.append(mov_row)
            else:
                receipt_row.update(resultado_dict)

            # Set pipeline metadata last so they override any same-named dataclass fields
            # (e.g. TituloPublico.status would otherwise overwrite the pipeline's "parsed" status).
            receipt_row["corretora_id"] = resultado.corretora_id
            receipt_row["doc_type"] = type(resultado).__name__
            receipt_row["status"] = "parsed"

            receipt_rows.append(receipt_row)

        except PdfImagemError as exc:
            receipt_row["status"] = "skipped_image"
            receipt_row["error"] = str(exc)
            receipt_rows.append(receipt_row)
        except Exception as exc:
            receipt_row["status"] = "failed"
            receipt_row["error"] = f"{type(exc).__name__}: {exc}"
            receipt_rows.append(receipt_row)

    receipts_df = pd.DataFrame(receipt_rows, columns=RECEIPT_HEADERS)
    movimentacoes_df = pd.DataFrame(movimentacao_rows, columns=MOV_HEADERS)

    with pd.ExcelWriter(workbook_path) as writer:
        receipts_df.to_excel(writer, sheet_name="notas", index=False)
        movimentacoes_df.to_excel(writer, sheet_name="movimentacoes", index=False)

    parsed_count = sum(1 for r in receipt_rows if r["status"] == "parsed")
    skipped_count = sum(1 for r in receipt_rows if r["status"] == "skipped_image")
    failed_count = sum(1 for r in receipt_rows if r["status"] == "failed")

    print(f"Workbook XLSX: {workbook_path}")
    print(
        "Resumo:"
        f" parsed={parsed_count}"
        f" skipped_image={skipped_count}"
        f" failed={failed_count}"
        f" movimentacoes={len(movimentacao_rows)}"
    )


if __name__ == "__main__":
    main()

