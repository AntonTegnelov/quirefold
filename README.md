# quirefold

> A Markdown→PDF pipeline focused on structural integrity.

A Markdown→PDF pipeline that produces structurally-clean long-form documents.
The key feature is the recursive packing algorithm in `pack.py` that
measures every node's rendered height and groups content into page-sized
units, ensuring no mid-paragraph, mid-sentence, or mid-list-item splits.

## Files

- **normalize_md.py** — inserts blank lines between markdown block elements
  so pandoc parses lists/tables/code-fences correctly
- **transform_tables.py** — converts pandoc's `#table()` calls into
  `#smart-table()` calls (defined in template.typ); bundles `**Label:**`
  + table pairs into `#keep-together[...]` so they don't separate at a
  page break
- **wrap_sections.py** — repairs TOC link labels emitted incorrectly by
  pandoc 3.1.3 (links target `<1-foo>` but heading labels are `<foo>`)
- **pack.py** — the heart of the pipeline. Tokenizes the post-pandoc
  Typst source into a hierarchical tree, measures each node's height
  via a probe Typst compile, then recursively emits content in
  page-sized groups using `block(breakable: false)` wrappers
- **template.typ** — Typst template defining typography, smart-table,
  heading rules with `sticky: true`, list-item and paragraph
  break-protection rules, layout constants
- **split_analysis.py** — quality measurement tool: counts whole vs split
  instances at each structural level (headings L1-L4, tables,
  paragraphs, sentences, lists, list items)
- **run_pipeline.sh** — single-command orchestrator
- **puppeteer.json** — Puppeteer launch flags for `mmdc` (`{"args": ["--no-sandbox"]}`)
- **mermaid-config.json** — Mermaid renderer config: `htmlLabels: false` so
  diagrams emit SVG `<text>` instead of `<foreignObject>` (Typst renders the
  former cleanly, the latter poorly)

## Conventions

- `input/` — your source markdown lives here (gitignored; bring your own).
- `outputs/` — rendered PDFs land here (gitignored; auto-created by the pipeline).

## Usage

```bash
./run_pipeline.sh input/source.md outputs/output.pdf

# With split analysis at the end:
RUN_ANALYSIS=1 ./run_pipeline.sh input/source.md outputs/output.pdf
```

## Algorithm: pack.py

The recursive pack algorithm (`emit` function):

```
emit(node, page_height):
    if node is a leaf:
        return raw source            # template handles per-leaf protection
    if node fits on one page:
        return block(breakable: false)[render(node)]
    # Container too big — partition children into page-sized groups
    groups = pack_children(node)     # greedy, uses prefix_heights
    for each group:
        if single oversized child:   # large container or large leaf
            emit(child)              # recurse
        else:
            block(breakable: false)[render group]
```

`pack_children` uses prefix-height measurements so subsequence sizes
account for inter-block spacing accurately. After greedy packing, a
sticky post-pass ensures no group ENDS with a sticky leaf (heading or
standalone bold label) — those get pulled to the start of the next group.

`emit` also handles cross-recursion sticky flow: when a single sticky
leaf precedes an oversized container being recursed into, the sticky
gets passed via `prepend=` and attaches to the recursion's first output.

## Heading wrap fix (v19 bug)

Pandoc wraps long headings to multiple source lines:
```
=== 3.1 Achieving LLM-layer zero data retention requires four specific
opt-outs
<label>
```
But Typst's heading syntax only treats the FIRST line as the heading;
subsequent lines become body text. The tokenizer in `pack.py` therefore
**rebuilds the heading source as a single line** before emitting back —
collapsing wrapped text so Typst parses it correctly.

## Known limitations

- Ordered list items can split mid-page because `#show enum.item: it =>
  block(breakable: false, it)` resets Typst's counter (every item becomes
  "1."). Unordered list items and paragraphs are protected.
- The keep-together threshold (template.typ) is 93% of page height.
  Sections between 93% and 100% would need to be kept whole but might
  overflow; in practice nothing in this document falls in that risky band.
- Mermaid diagrams are pre-rendered to SVG with `htmlLabels: false` (see
  `mermaid-config.json`) so labels become SVG `<text>` rather than
  `<foreignObject>`; the template's image show rule scales each image to
  fit the page (`max-width: 100%`, `max-height: 230mm`, aspect preserved).

## Output quality bar

- **Splits (priority)**: zero mid-paragraph, mid-sentence, mid-list-item
  splits. Some lists with many items can split between items.
- **Whitespace**: <5% of pages with >80% blank, <20% of pages with >60%
  blank. Measured by `split_analysis.py` companion (whitespace tool not
  included here, was inline in earlier turns).
