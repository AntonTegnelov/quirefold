#!/usr/bin/env python3
"""Transform pandoc's Typst table emission into our smart-table calls.

Pandoc 3.1.x emits:
    #align(center)[#table(
      columns: N,
      align: (col, row) => (...).at(col),
      inset: 6pt,
      [h1], [h2], ...,
      [c11], [c12], ...,
      ...
    )]

Pandoc 3.4+ wraps the same shape in a #figure with kind: table:
    #figure(
      align(center)[#table(
        ...
      )]
      , kind: table
    )

Both are recognized. We rewrite to:
    #smart-table(
      columns: N,
      ([h1], [h2], ...),
      ([c11], [c12], ...),
      ...
    )

`smart-table` is defined in the template and handles styling, header repeat
on break, and keep-together sizing logic.

This is a structural rewrite of Typst source. We parse the #table(...) call
with a small state machine that tracks bracket depth.
"""
import re
import sys


def find_table_calls(text: str):
    """Yield (start, end, inner) for each table emission from pandoc.

    `inner` is the content between the outermost `table(` and its matching `)`.
    `start` and `end` cover the entire emission — including the #figure
    wrapper when pandoc 3.4+ is being used.
    """
    pattern = re.compile(
        r'(#figure\(\s*\n?\s*)?'        # optional #figure( opener
        r'(#?align\(center\)\[)'         # align(center)[  (with or without leading #)
        r'#table\('
    )
    i = 0
    while True:
        m = pattern.search(text, i)
        if not m:
            return
        block_start = m.start()
        has_figure = m.group(1) is not None
        # Position after "table("
        paren_pos = m.end()
        depth = 1
        j = paren_pos
        while j < len(text) and depth > 0:
            c = text[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            j += 1
        # j now points one past the matching )
        inner = text[paren_pos:j-1]
        # After the closing ), expect ']' to close the align bracket
        k = j
        while k < len(text) and text[k] in ' \n\t':
            k += 1
        if k < len(text) and text[k] == ']':
            block_end = k + 1
        else:
            i = j
            continue
        # If wrapped in #figure(...), consume the figure trailer too:
        # whitespace, optional `, kind: ...`, closing `)`.
        if has_figure:
            fdepth = 1
            fj = block_end
            while fj < len(text) and fdepth > 0:
                c = text[fj]
                if c == '(':
                    fdepth += 1
                elif c == ')':
                    fdepth -= 1
                fj += 1
            block_end = fj
        yield (block_start, block_end, inner)
        i = block_end


def parse_cells(inner: str, n_cols: int) -> tuple:
    """Extract cells from the pandoc table body.

    Returns (columns, rows_of_cells).
    """
    cols_match = re.search(r'columns:\s*(\d+)', inner)
    if cols_match:
        columns = int(cols_match.group(1))
    else:
        # Pandoc 3.4+ emits `columns: (33.33%, 33.33%, 33.33%)` — count entries.
        tup_match = re.search(r'columns:\s*\(([^)]*)\)', inner)
        if tup_match:
            columns = len([s for s in tup_match.group(1).split(',') if s.strip()])
        else:
            columns = n_cols

    cells = []
    i = 0
    paren_depth = 0
    while i < len(inner):
        c = inner[i]
        if c == '(':
            paren_depth += 1
            i += 1
        elif c == ')':
            paren_depth -= 1
            i += 1
        elif c == '[' and paren_depth == 0:
            depth = 1
            j = i + 1
            while j < len(inner) and depth > 0:
                cc = inner[j]
                if cc == '[':
                    depth += 1
                elif cc == ']':
                    depth -= 1
                j += 1
            cell_content = inner[i+1:j-1]
            cells.append(cell_content)
            i = j
        else:
            i += 1

    # Pandoc 3.4+ also emits `table.header([h1], [h2], ...,)` and `table.hline()`
    # markers as content. The first row of cells is therefore inside
    # `table.header(...)` parens — but our paren_depth tracking already excluded
    # cells while paren_depth>0. We need to rescue those header cells.
    # Pull cells from `table.header(...)` explicitly:
    header_cells = []
    hm = re.search(r'table\.header\s*\(', inner)
    if hm:
        # Walk forward, balanced parens
        i2 = hm.end()
        depth = 1
        # Extract all top-level [..] inside the paren
        while i2 < len(inner) and depth > 0:
            c = inner[i2]
            if c == '(':
                depth += 1
                i2 += 1
            elif c == ')':
                depth -= 1
                i2 += 1
            elif c == '[' and depth == 1:
                d = 1
                j = i2 + 1
                while j < len(inner) and d > 0:
                    cc = inner[j]
                    if cc == '[':
                        d += 1
                    elif cc == ']':
                        d -= 1
                    j += 1
                header_cells.append(inner[i2+1:j-1])
                i2 = j
            else:
                i2 += 1

    if header_cells:
        all_cells = header_cells + cells
    else:
        all_cells = cells

    rows = []
    for start in range(0, len(all_cells), columns):
        row = all_cells[start:start + columns]
        if len(row) == columns:
            rows.append(row)
    return columns, rows


# Insert a zero-width space inside snake_case names, slashed paths, and dotted
# identifiers so typst can line-break them inside narrow body cells. Done here,
# at preprocess time, instead of via a `show regex(...)` rule in the template:
# the show rule fires per-cell across hundreds of replicated tables in the
# measurement passes and rebuilds the content tree on every match, which
# exhausts typst's allocator. Doing it once in Python is O(N) on the source.
ZWS = '​'
_BREAKABLE_RE = re.compile(r'([\w])([_/.])([\w])')
# Spans inside a body cell that must be left untouched: backtick-delimited raw
# content, double-quoted strings (e.g. image paths inside `#image("...")`), and
# math `$...$`. English smart quotes are curly (“”) and not affected.
_PROTECTED_RE = re.compile(r'`[^`]*`|"[^"]*"|\$[^$]*\$')


def _insert_zws(s: str) -> str:
    parts = []
    last = 0
    for m in _PROTECTED_RE.finditer(s):
        parts.append(_BREAKABLE_RE.sub(r'\1\2' + ZWS + r'\3', s[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_BREAKABLE_RE.sub(r'\1\2' + ZWS + r'\3', s[last:]))
    return ''.join(parts)


def emit_smart_table(columns: int, rows) -> str:
    """Generate a #smart-table(...) call."""
    if not rows:
        return ""
    lines = ["#smart-table(", f"  columns: {columns},"]
    for row_idx, row in enumerate(rows):
        cell_strs = []
        for cell in row:
            cell_stripped = cell.strip()
            if row_idx > 0:
                cell_stripped = _insert_zws(cell_stripped)
            if '\n' in cell_stripped:
                indented = '\n'.join('    ' + ln for ln in cell_stripped.splitlines())
                cell_strs.append(f"[\n{indented}\n  ]")
            else:
                cell_strs.append(f"[{cell_stripped}]")
        lines.append("  (" + ", ".join(cell_strs) + "),")
    lines.append(")")
    return '\n'.join(lines)


# Pandoc emits each markdown image as `#box(width: <Xpt>, image("..."));`
# inside a `#figure([...], caption: [...])` wrapper. The explicit width
# encodes the SVG/PNG's natural pixel size and pins the image to that
# width regardless of page size — so a 1058pt-wide flowchart silently
# overflows the 470pt page-inner-width. Strip the box wrapper so the
# template's `#show image:` scale-to-fit rule can do its job.
IMAGE_BOX_RE = re.compile(
    r'#box\(\s*width:\s*[\d.]+pt\s*,\s*(image\(\s*"[^"]+"\s*\))\s*\);'
)


def unwrap_images(text: str) -> str:
    return IMAGE_BOX_RE.sub(r'#\1', text)


def transform(text: str) -> str:
    text = unwrap_images(text)
    spans = list(find_table_calls(text))
    if not spans:
        return text
    out = []
    cursor = 0
    for start, end, inner in spans:
        preceding_label = None
        label_start = start
        i = start - 1
        while i > cursor and text[i] in ' \n\t':
            i -= 1
        region = text[cursor:start]
        m = re.search(
            r'(#strong\[[^\]\n]*[:：]\s*\]|#strong\[[^\]\n]*\]\s*[:：])\s*\Z',
            region,
        )
        if m:
            label_start = cursor + m.start()
            preceding_label = text[label_start:start].rstrip()

        out.append(text[cursor:label_start])

        columns, rows = parse_cells(inner, n_cols=3)
        if rows:
            if preceding_label:
                out.append("#keep-together[\n")
                out.append(preceding_label + "\n\n")
                out.append(emit_smart_table(columns, rows))
                out.append("\n]\n")
            else:
                out.append(emit_smart_table(columns, rows))
        else:
            out.append(text[label_start:end])
        cursor = end
    out.append(text[cursor:])
    return ''.join(out)


if __name__ == "__main__":
    src = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    sys.stdout.write(transform(src))
