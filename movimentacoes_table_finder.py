import fitz
from collections import defaultdict
from statistics import mode
from typing import List, Dict, Optional


def _group_words_by_row(words, row_y_tol):
    rows_by_y: List[List] = []
    for word in sorted(words, key=lambda w: w[1]):
        matched = False
        for row in rows_by_y:
            if abs(row[0][1] - word[1]) <= row_y_tol:
                row.append(word)
                matched = True
                break
        if not matched:
            rows_by_y.append([word])

    for row in rows_by_y:
        row.sort(key=lambda w: w[0])

    return rows_by_y


def _find_header_rects(page, header_text, expected_headers, header_y_tol):
    title_rects = page.search_for(header_text)
    if not title_rects:
        raise ValueError(f"Header '{header_text}' not found.")
    title_rect = title_rects[0]

    if not expected_headers:
        raise ValueError("Expected headers are required for the current table extractor.")

    header_rects = []
    for expected_header in expected_headers:
        hits = page.search_for(expected_header)
        if not hits:
            raise ValueError(f"Expected header '{expected_header}' not found.")

        matching_hits = [
            rect
            for rect in hits
            if abs(rect.y0 - title_rect.y0) <= header_y_tol
        ]
        if not matching_hits:
            raise ValueError(
                f"Expected header '{expected_header}' not found on the header row."
            )

        if expected_header == header_text:
            header_rect = min(matching_hits, key=lambda rect: abs(rect.x0 - title_rect.x0))
        else:
            header_rect = sorted(matching_hits, key=lambda rect: (rect.x0, rect.y0))[0]

        header_rects.append((expected_header, header_rect))

    header_rects.sort(key=lambda item: item[1].x0)
    return title_rect, header_rects


def extract_table_movimentacoes(
    page: fitz.Page,
    *,
    header_text: str = "Especificação do Título",
    table_anchor: str = "Resumo dos Negócios",
    expected_headers: Optional[List[str]] = None,
    y_tolerance_ratio: float = 0.5,
    row_y_tol: float = 1.0,
    column_margin_ratio: float = 0.35,
    column_margin_overrides: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict[str, str]]:
    """
    Extract a movimentacoes table by anchoring columns directly on the detected headers.

    Instead of relying on text-gap splitting within each row, this version:
    1. finds the configured headers on the same header row
    2. expands each header horizontally by a configurable margin
    3. assigns words below the headers to the nearest matching header band
    4. groups words into rows by y proximity
    """

    column_margin_overrides = column_margin_overrides or {}

    title_rects = page.search_for(header_text)
    if not title_rects:
        raise ValueError(f"Header '{header_text}' not found.")
    title_rect = title_rects[0]
    header_height = title_rect.y1 - title_rect.y0
    header_y_tol = header_height * y_tolerance_ratio

    anchor_rects = page.search_for(table_anchor)
    if not anchor_rects:
        raise ValueError(f"Anchor '{table_anchor}' not found.")
    table_y_max = anchor_rects[0].y0

    title_rect, header_rects = _find_header_rects(
        page,
        header_text,
        expected_headers,
        header_y_tol,
    )

    header_specs = []
    for header, rect in header_rects:
        width = rect.x1 - rect.x0
        base_margin = width * column_margin_ratio
        overrides = column_margin_overrides.get(header, {})
        left_margin = overrides.get("left", base_margin)
        right_margin = overrides.get("right", base_margin)
        header_specs.append(
            {
                "header": header,
                "rect": rect,
                "center_x": (rect.x0 + rect.x1) / 2,
                "x0": rect.x0 - left_margin,
                "x1": rect.x1 + right_margin,
            }
        )

    words = page.get_text("words")
    max_header_y1 = max(spec["rect"].y1 for spec in header_specs)
    header_cutoff_y = max_header_y1 + row_y_tol
    content_words = [
        word
        for word in words
        if header_cutoff_y <= word[1] <= table_y_max - header_height
    ]

    rows_by_y = _group_words_by_row(content_words, row_y_tol)

    extracted_table: List[Dict[str, str]] = []
    for row_words in rows_by_y:
        row_cells: Dict[str, List[str]] = defaultdict(list)

        for word in row_words:
            word_center_x = (word[0] + word[2]) / 2
            matching_headers = [
                spec
                for spec in header_specs
                if spec["x0"] <= word_center_x <= spec["x1"]
            ]

            if not matching_headers:
                continue

            best_header = min(
                matching_headers,
                key=lambda spec: abs(word_center_x - spec["center_x"]),
            )
            row_cells[best_header["header"]].append(word[4])

        row_dict = {
            spec["header"]: " ".join(row_cells.get(spec["header"], [])) or ""
            for spec in header_specs
        }

        if any(value for value in row_dict.values()):
            extracted_table.append(row_dict)

    return extracted_table
