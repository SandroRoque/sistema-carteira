import fitz


def _group_words_into_lines(words, line_y_tol):
    lines = []

    for word in sorted(words, key=lambda w: (w[1], w[0])):
        word_center_y = (word[1] + word[3]) / 2
        placed = False

        for line in lines:
            if abs(word_center_y - line["center_y"]) <= line_y_tol:
                line["words"].append(word)
                line["y0"] = min(line["y0"], word[1])
                line["y1"] = max(line["y1"], word[3])
                centers = [((candidate[1] + candidate[3]) / 2) for candidate in line["words"]]
                line["center_y"] = sum(centers) / len(centers)
                placed = True
                break

        if not placed:
            lines.append(
                {
                    "words": [word],
                    "y0": word[1],
                    "y1": word[3],
                    "center_y": word_center_y,
                }
            )

    for line in lines:
        line["words"].sort(key=lambda w: w[0])

    lines.sort(key=lambda line: line["y0"])
    return lines


def _extract_multiline_vertical_value(
    page,
    rect,
    next_key_x0,
    next_row_y0,
    horizontal_gap_ratio,
    line_y_tolerance_ratio,
    continuation_gap_ratio,
    line_joiner,
):
    key_height = rect.y1 - rect.y0
    line_y_tol = key_height * line_y_tolerance_ratio
    horizontal_gap_tol = key_height * horizontal_gap_ratio
    words = page.get_text("words")

    candidate_words = [
        word
        for word in words
        if rect.x0 <= word[0] < next_key_x0
        and word[1] >= rect.y1
        and (next_row_y0 is None or word[1] < next_row_y0)
    ]

    lines = _group_words_into_lines(candidate_words, line_y_tol)
    if not lines:
        return ""

    first_line = lines[0]
    first_gap = max(0.0, first_line["y0"] - rect.y1)
    previous_line = first_line
    collected_lines = []

    for index, line in enumerate(lines):
        if index > 0:
            line_gap = max(0.0, line["y0"] - previous_line["y1"])
            if line_gap > first_gap * continuation_gap_ratio:
                break

        collected_words = []
        previous_word = None

        for word in line["words"]:
            if previous_word is None:
                collected_words.append(word[4])
                previous_word = word
                continue

            gap = word[0] - previous_word[2]
            if gap > horizontal_gap_tol:
                break

            collected_words.append(word[4])
            previous_word = word

        if collected_words:
            collected_lines.append(" ".join(collected_words).strip())
            previous_line = line

    return line_joiner.join(collected_lines).strip()


def find_sbs_key_value_pairs(
    page,
    keys,
    x_tolerance=3,
    joiner=" ",
    key_options=None,
    gap_tolerance_ratio=1.5,
):
    """
    Locate side-by-side key-value pairs on a PDF page, returning key→value.

    Args:
        page (fitz.Page)
        keys (list[str])
        x_tolerance (float): allowed deviation (in points) from the main key column
        joiner (str): what to use to join multi-word values

    Raises:
        ValueError if any key is missing or has no valid instance.
    """
    key_options = key_options or {}

    # 1) find all key instances
    instances = {k: page.search_for(k) for k in keys}
    missing = [k for k, rects in instances.items() if not rects]
    if missing:
        raise ValueError(f"Key(s) not found on page: {', '.join(missing)}")

    # 2) cluster x0 to find main key column
    all_x0 = [r.x0 for rects in instances.values() for r in rects]
    clusters = []
    for x in all_x0:
        for c in clusters:
            if abs(c[0] - x) <= x_tolerance:
                c.append(x)
                break
        else:
            clusters.append([x])
    main_cluster = max(clusters, key=len)
    correct_x0 = sum(main_cluster) / len(main_cluster)

    # 3) pick one rect per key near correct_x0
    key_rects = {}
    for key, rects in instances.items():
        valid = [r for r in rects if abs(r.x0 - correct_x0) <= x_tolerance]
        if not valid:
            raise ValueError(f"No valid instance for key '{key}' near x0≈{correct_x0:.1f}")
        ordered_valid = sorted(valid, key=lambda r: r.y0)
        occurrence_index = key_options.get(key, {}).get("occurrence_index", 0)
        if occurrence_index >= len(ordered_valid):
            raise ValueError(
                f"Occurrence index {occurrence_index} out of range for key '{key}'."
            )
        key_rects[key] = ordered_valid[occurrence_index]

    # compute global right boundary
    right_of = max(r.x1 for r in key_rects.values())

    # 4) extract words and assemble values by contiguous grouping
    words = page.get_text("words")  # [x0, y0, x1, y1, text, ...]
    result = {}
    for key, rect in key_rects.items():
        height = rect.y1 - rect.y0
        y_center = (rect.y0 + rect.y1) / 2
        y_tol = height * 0.5
        gap_tol = height * gap_tolerance_ratio

        # candidates on same row to the right of all keys
        row = [
            (w[0], w[2], w[4])
            for w in words
            if w[0] > right_of
            and abs(((w[1] + w[3]) / 2) - y_center) <= y_tol
        ]
        row.sort(key=lambda x: x[0])

        # take contiguous words until gap > gap_tol
        group = []
        if row:
            prev_x1 = row[0][1]
            group.append(row[0][2])
            for x0, x1, text in row[1:]:
                if x0 - prev_x1 <= gap_tol:
                    group.append(text)
                    prev_x1 = x1
                else:
                    break

        result[key] = joiner.join(group).strip()

    return result

def find_vertical_key_value_pairs(
    page,
    keys,
    y_tol=2.0,
    debug=False,
    key_options=None,
    multiline_values=False,
    horizontal_gap_ratio=1.5,
    line_y_tolerance_ratio=0.6,
    continuation_gap_ratio=0.9,
    line_joiner=" ",
    value_height_ratio=2.055,
):
    """
    Locate vertical key-value pairs on a PDF page.

    Each key is assumed to appear in a left-hand column.
    Values for each key occupy the area immediately below the key,
    bounded on the sides by the next key (or page edge) and below
    by the next row of keys (or 1.5× the key height).

    Args:
        page (fitz.Page): PDF page to search.
        keys (list[str]): list of key strings to locate.
        y_tol (float): max vertical offset (points) to group keys into the same row.
        debug (bool): if True, print detailed debugging information.

    Returns:
        dict[str, str]: mapping each key to its extracted value text.

    Raises:
        ValueError: if any key is not found.
    """
    if debug:
        print(f"=== DEBUGGING find_vertical_key_value_pairs ===")
        print(f"Page dimensions: {page.rect.width} x {page.rect.height}")
        print(f"Looking for {len(keys)} keys with y_tol={y_tol}...")

    key_options = key_options or {}
    
    # 1) Locate one rectangle per key on the page
    # We take the first occurrence of each key found
    key_rects = {}
    missing_keys = []
    
    for key in keys:
        hits = page.search_for(key)
        if not hits:
            missing_keys.append(key)
            if debug:
                print(f"❌ Key '{key}' NOT FOUND")
        else:
            occurrence_index = key_options.get(key, {}).get("occurrence_index", 0)
            if occurrence_index >= len(hits):
                raise ValueError(
                    f"Occurrence index {occurrence_index} out of range for key '{key}'."
                )
            key_rects[key] = hits[occurrence_index]
            if debug:
                rect = key_rects[key]
                print(f"✅ Key '{key}' found at: x0={rect.x0:.1f}, y0={rect.y0:.1f}, x1={rect.x1:.1f}, y1={rect.y1:.1f}")
    
    if missing_keys:
        if debug:
            print(f"\n⚠️  {len(missing_keys)} keys were not found:")
            for key in missing_keys:
                print(f"   - {key}")
        raise ValueError(f"Key(s) not found on page: {', '.join(missing_keys)}")

    # 2) Group keys into horizontal rows based on their y0 position
    # Keys that are close vertically (within y_tol) are considered to be on the same row
    rows = []  # list of (row_y, [(key, rect), ...])
    for key, rect in key_rects.items():
        placed = False
        # Try to place this key in an existing row
        for i, (row_y, group) in enumerate(rows):
            if abs(rect.y0 - row_y) <= y_tol:
                # Key is close enough to this row's y position
                group.append((key, rect))
                placed = True
                break
        if not placed:
            # Create a new row for this key
            rows.append((rect.y0, [(key, rect)]))

    # 3) Sort rows from top to bottom based on their y0 coordinate
    rows.sort(key=lambda item: item[0])
    
    if debug:
        print(f"\n=== ROW GROUPING (y_tol={y_tol}) ===")
        for i, (row_y, group) in enumerate(rows):
            print(f"Row {i+1} (y≈{row_y:.1f}): {len(group)} keys")
            for key, rect in group:
                print(f"   - {key}")

    # 4) Build value extraction regions for each key
    # Each value region is defined as the rectangular area below each key
    page_right = page.rect.x1  # Right edge of the page
    value_regions = {}
    
    if debug:
        print(f"\n=== VALUE REGIONS ===")

    for idx, (row_y, group) in enumerate(rows):
        # Determine where the next row starts (for vertical boundary)
        next_row_y0 = rows[idx + 1][0] if idx + 1 < len(rows) else None
        
        # Sort keys in this row from left to right
        group.sort(key=lambda kr: kr[1].x0)
        
        if debug:
            print(f"\nRow {idx+1} regions:")
        
        for j, (key, rect) in enumerate(group):
            # Determine horizontal boundaries for the value region
            # Right boundary: either the next key in the same row, or the page edge
            next_key_x0 = group[j + 1][1].x0 if j + 1 < len(group) else page_right
            
            # Define the value extraction rectangle
            x0 = rect.x0          # Left: align with key's left edge
            x1 = next_key_x0      # Right: up to next key or page edge
            
            # Top: start below the key text, ensuring we don't include multi-line keys
            # We need to make sure the value's y0 is bigger than the key's y1
            # Add a small buffer to ensure we're truly below the key
            y0 = rect.y1 + 2.0    # Start 2 points below the key's bottom
            key_h = rect.y1 - rect.y0  # Height of the key text
            
            # Bottom boundary: either the next row of keys, or 1.5x key height below
            # Ensure next row boundary is also below the complete key
            default_y1 = y0 + value_height_ratio * key_h
            if next_row_y0 is not None:
                if next_row_y0 > y0:
                    y1 = min(next_row_y0, default_y1)
                else:
                    y1 = default_y1
            else:
                y1 = default_y1

            # Store the rectangular region for text extraction
            value_regions[key] = fitz.Rect(x0, y0, x1, y1)
            
            if debug:
                print(f"   {key}:")
                print(f"     Key rect: x0={rect.x0:.1f}, y0={rect.y0:.1f}, x1={rect.x1:.1f}, y1={rect.y1:.1f}")
                print(f"     Value region: x0={x0:.1f}, y0={y0:.1f}, x1={x1:.1f}, y1={y1:.1f}")
                if next_row_y0:
                    print(f"     Next row starts at y0={next_row_y0:.1f}")
                else:
                    print(f"     Last row, using key_h={key_h:.1f}")

    # 5) Extract text from each defined region
    result = {}
    if debug:
        print(f"\n=== TEXT EXTRACTION ===")
        
    for key, region in value_regions.items():
        if multiline_values:
            key_rect = key_rects[key]
            next_row_y0 = None
            next_key_x0 = page.rect.x1

            for row_index, (row_y, group) in enumerate(rows):
                group.sort(key=lambda kr: kr[1].x0)
                key_names = [candidate_key for candidate_key, _ in group]
                if key in key_names:
                    key_position = key_names.index(key)
                    if row_index + 1 < len(rows):
                        next_row_y0 = rows[row_index + 1][0]
                    if key_position + 1 < len(group):
                        next_key_x0 = group[key_position + 1][1].x0
                    break

            text = _extract_multiline_vertical_value(
                page,
                key_rect,
                next_key_x0,
                next_row_y0,
                horizontal_gap_ratio,
                line_y_tolerance_ratio,
                continuation_gap_ratio,
                line_joiner,
            )
        else:
            text = page.get_text("text", clip=region).strip()
            text = text.replace("\n", " ")

        result[key.strip()] = text

        if debug:
            print(f"{key}: '{text}'")
    
    if debug:
        print(f"\n=== EXTRACTION COMPLETE ===")

    return result
