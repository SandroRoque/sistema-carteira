import fitz
from collections import defaultdict
from statistics import mode
from typing import List, Dict, Optional


def extract_table_movimentacoes(
    page: fitz.Page,
    *,
    header_text: str = "Especificação do Título",  # top-left label of the table
    table_anchor: str = "Resumo dos Negócios",     # first label found *after* the table
    expected_headers: Optional[List[str]] = None,
    y_tolerance_ratio: float = 0.5,                # tolerance for locating header row
    row_y_tol: float = 1.0                         # tolerance for grouping lines into rows
) -> List[Dict[str, str]]:
    """
    Locate the ‘Movimentações’ table bounded by `header_text` and `table_anchor` and
    return it as a list of dicts (one dict per row).

    Each dict maps the detected column headers to the cell contents found
    in that row. Empty strings are used when no content is present under a
    column for a given row.

    Parameters
    ----------
    page : fitz.Page
        The PyMuPDF page object to parse.
    header_text, table_anchor : str
        Sentinels that delimit the vertical extent of the table.
    y_tolerance_ratio : float
        Fraction of the header’s height used as tolerance when grabbing header words.
    row_y_tol : float
        Absolute Y tolerance (points) used to group individual words into the same row.

    Returns
    -------
    list[dict[str, str]]
    """

    # --------------------------------------------------
    # 1) locate title (top boundary) and anchor (bottom)
    # --------------------------------------------------
    title_rects = page.search_for(header_text)
    if not title_rects:
        raise ValueError(f"Header '{header_text}' not found.")
    title_rect = title_rects[0]
    table_y_min = title_rect.y0
    header_height = title_rect.y1 - title_rect.y0

    anchor_rects = page.search_for(table_anchor)
    if not anchor_rects:
        raise ValueError(f"Anchor '{table_anchor}' not found.")
    table_y_max = anchor_rects[0].y0

    # --------------------------------------------------
    # 2) collect all words inside table vertical range
    # --------------------------------------------------
    words = page.get_text("words")  # [x0, y0, x1, y1, text, block_no, line_no, word_no]
    table_words = [
        w for w in words
        if table_y_min <= w[1] <= table_y_max - header_height
    ]

    # --------------------------------------------------
    # 3) detect header words, group them into columns
    # --------------------------------------------------
    header_y_tol = header_height * y_tolerance_ratio
    if expected_headers:
        header_rects = []
        for index, expected_header in enumerate(expected_headers):
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
        headers = [header for header, _ in header_rects]
        col_x_bounds = []

        for idx, (_, rect) in enumerate(header_rects):
            if idx == 0:
                x_left = 0
            else:
                prev_rect = header_rects[idx - 1][1]
                x_left = (prev_rect.x1 + rect.x0) / 2
            if idx < len(header_rects) - 1:
                next_rect = header_rects[idx + 1][1]
                x_right = (next_rect.x0 + rect.x1) / 2
            else:
                x_right = page.rect.x1
            col_x_bounds.append((x_left, x_right))
    else:
        header_words = [
            w for w in table_words
            if abs(w[1] - title_rect.y0) <= header_y_tol
        ]
        header_words.sort(key=lambda w: w[0])  # sort by x0

        # distances between consecutive header words
        header_x_gaps = [
            header_words[i + 1][0] - header_words[i][2]
            for i in range(len(header_words) - 1)
        ]
        if header_x_gaps:
            try:
                gap_threshold = mode(header_x_gaps) * 1.2
            except:
                # fallback if no unique mode (all gaps different)
                gap_threshold = min(header_x_gaps) * 1.2
        else:
            gap_threshold = header_height * 2.5

        grouped_headers: List[List] = []
        current_group: List = []
        for w in header_words:
            if not current_group:
                current_group.append(w)
            else:
                if w[0] - current_group[-1][2] <= gap_threshold:
                    current_group.append(w)
                else:
                    grouped_headers.append(current_group)
                    current_group = [w]
        if current_group:
            grouped_headers.append(current_group)

        # final header strings and x-boundaries
        headers = []
        col_x_bounds = []
        for idx, grp in enumerate(grouped_headers):
            text = " ".join(word[4] for word in grp).strip()
            headers.append(text)

            if idx == 0:
                x_left = 0
            else:
                prev_grp = grouped_headers[idx - 1]
                x_left = (prev_grp[-1][2] + grp[0][0]) / 2
            if idx < len(grouped_headers) - 1:
                x_right = (grouped_headers[idx + 1][0][0] + grp[-1][2]) / 2
            else:
                x_right = page.rect.x1
            col_x_bounds.append((x_left, x_right))

    # --------------------------------------------------
    # 4) group remaining words into data rows
    # --------------------------------------------------
    header_cutoff_y = title_rect.y1 - row_y_tol
    content_words = [
        w for w in table_words
        if w[1] >= header_cutoff_y
    ]
    # cluster words by y using tolerance
    rows_by_y: List[List] = []
    for w in sorted(content_words, key=lambda w: w[1]):
        matched = False
        for row in rows_by_y:
            if abs(row[0][1] - w[1]) <= row_y_tol:
                row.append(w)
                matched = True
                break
        if not matched:
            rows_by_y.append([w])

    # --------------------------------------------------
    # 5) assemble list of dicts
    # --------------------------------------------------
    extracted_table: List[Dict[str, str]] = []
    for row_words in rows_by_y:
        # group row words by column
        row_cells: Dict[str, List[str]] = defaultdict(list)
        for w in row_words:
            for col_idx, (x0, x1) in enumerate(col_x_bounds):
                if x0 <= w[0] < x1:
                    row_cells[headers[col_idx]].append(w[4])
                    break

        # map header -> concatenated cell text (or "")
        row_dict = {
            header: " ".join(row_cells.get(header, [])) or ""
            for header in headers
        }
        # ignore empty rows
        if any(value for value in row_dict.values()):
            extracted_table.append(row_dict)

    return extracted_table
