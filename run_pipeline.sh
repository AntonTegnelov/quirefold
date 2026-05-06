#!/usr/bin/env bash
# quirefold pipeline runner.
#
# Usage: ./run_pipeline.sh <input.md> <output.pdf>
#
# Stages:
#   1. normalize_md.py  — insert blank lines between block elements
#   2. mmdc             — pre-render Mermaid diagrams to PNG
#   3. pandoc           — markdown → typst
#   4. transform_tables.py — convert pandoc #table to #smart-table; bundle label+table pairs
#   5. wrap_sections.py — TOC link label repair
#   6. pack.py          — measure heights, recursively pack into page-sized groups
#   7. typst compile    — produce final PDF
#
# Requires:
#   - python3 with: typst, pypdf, pdf2image
#   - mmdc (mermaid-cli) installed at /home/claude/.cache/puppeteer
#   - pandoc 3.1.3+
set -euo pipefail

INPUT="$1"
OUTPUT="$2"
WORK="${WORK:-$(mktemp -d)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Resolve INPUT/OUTPUT to absolute paths before we cd into $WORK, otherwise
# relative paths resolve against the tempdir and the final PDF gets copied
# into $WORK (which then disappears).
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
case "$OUTPUT" in
    /*) ;;
    *)  OUTPUT="$PWD/$OUTPUT" ;;
esac

mkdir -p "$(dirname "$OUTPUT")"

echo "→ Working in: $WORK"
cp "$INPUT" "$WORK/source.md"
cp "$SCRIPT_DIR/template.typ" "$WORK/"
cp "$SCRIPT_DIR/puppeteer.json" "$WORK/"
cp "$SCRIPT_DIR/mermaid-config.json" "$WORK/"

cd "$WORK"

echo "[1/7] Normalizing markdown..."
python3 "$SCRIPT_DIR/normalize_md.py" source.md > source_norm.md

echo "[2/7] Pre-rendering Mermaid diagrams..."
# SVG output with htmlLabels: false (set in mermaid-config.json): mermaid
# emits SVG <text> elements instead of <foreignObject> for node labels, which
# Typst's native SVG renderer handles cleanly. Vector → sharp at any size, no
# DPI concerns, ~5 KB per diagram.
PUPPETEER_CACHE_DIR=/home/claude/.cache/puppeteer mmdc \
    -i source_norm.md \
    -o source_norm_rendered.md \
    --outputFormat svg \
    -c mermaid-config.json \
    -p puppeteer.json 2>&1 | tail -2

echo "[3/7] pandoc markdown → typst..."
# --wrap=none: keep paragraphs as single long lines so pandoc never wraps such
# that a Typst-significant character ('/', '+', '-', '=') ends up at column 0,
# which would otherwise be parsed as a definition-list term, list item, or
# heading and break the typst compile.
pandoc source_norm_rendered.md -t typst --wrap=none -o source.typ

echo "[4/7] Transforming tables..."
python3 "$SCRIPT_DIR/transform_tables.py" source.typ > source_tables.typ

echo "[5/7] Repairing TOC labels..."
python3 "$SCRIPT_DIR/wrap_sections.py" source_tables.typ > source_unwrapped.typ

echo "[6/7] Measuring + packing..."
# --page-height 230: tracks the template's keep-together threshold
# (page-inner-height * 0.85 ≈ 215mm) loosely. We keep this near the full
# page rather than at 215 because dropping further explodes the count of
# "oversized" containers, which makes pass-2 prefix measurement OOM the
# typst compile. 230mm gives pack.py headroom while still leaving a
# safety gap below the rendered page bound (253mm).
python3 "$SCRIPT_DIR/pack.py" source_unwrapped.typ \
    --template template.typ \
    --output source_packed.typ \
    --page-height 235 \
    --work-dir "$WORK"

echo "[7/7] Compiling Typst..."
cat template.typ source_packed.typ > full_doc.typ
python3 -c "import typst; typst.compile('full_doc.typ', output='out.pdf')"

cp out.pdf "$OUTPUT"
echo "✓ Wrote $OUTPUT"

# Optional: split analysis
if [ "${RUN_ANALYSIS:-0}" = "1" ]; then
    echo
    echo "=== Split analysis ==="
    python3 "$SCRIPT_DIR/split_analysis.py" source_unwrapped.typ "$OUTPUT"
fi
