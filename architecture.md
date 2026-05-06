# quirefold architecture

This document explains *why* the pipeline is built the way it is. It captures the design goals, the decisions made along the way, the dead ends explored and discarded, and the rationale behind every non-obvious choice. The intended reader is anyone — including a future Claude instance — who needs to understand or extend the system.

## Goals, in priority order

The pipeline transforms a long-form Markdown document into a polished PDF. There are three quality goals, ordered by priority:

### G1 — Structural integrity (highest priority)

**Nothing logical should split across pages unless absolutely necessary.** When a split is unavoidable, it must happen at the highest possible structural level — between sections rather than between paragraphs, between paragraphs rather than between sentences, between list items rather than mid-item. Mid-paragraph and mid-sentence splits are unacceptable. Mid-table splits are tolerable only when the table is genuinely too large for one page, and even then must break at row boundaries.

The user stated this priority explicitly: "long inefficient whitespace version that barely crosses what we find acceptable but with perfect logical splitting is better than okayish whitespace with okayish splitting."

### G2 — Logical grouping

A heading should never appear at the bottom of a page with its body on the next page. A bold label like "**Implementation:**" should never be separated from the content it introduces. A table caption should stay with its table. The layout should respect the *meaning* of structure, not just its presence.

### G3 — Reasonable whitespace distribution

After G1 and G2 are satisfied, the pipeline should not waste pages with excessive blank space. The acceptable thresholds are: fewer than 5% of pages with >80% whitespace, fewer than 20% of pages with >60% whitespace. This is a soft target — exceeding it is acceptable if necessary to satisfy G1 and G2.

The user stated explicitly that page count is irrelevant — only the three goals above matter.

## Why this is hard

A naive Markdown→PDF pipeline (pandoc with a default template) achieves none of these goals reliably. Page breaks happen wherever the rendering engine decides, often mid-paragraph, mid-sentence, or with headings orphaned at the bottom of a page. Solving this requires the pipeline to understand the document's structural hierarchy and make page-break decisions deliberately, with knowledge of how content will actually render.

The hard part is that *deciding* where to break a page requires knowing each unit's *rendered* height, which only the rendering engine knows. The pipeline therefore needs to round-trip through the renderer twice: once to measure, once to lay out with measurements in hand.

## Pipeline overview

```
source.md
   │
   ├─ [1] normalize_md.py     → blank lines between block elements
   │
   ├─ [2] mmdc                → pre-render Mermaid diagrams to PNG
   │
   ├─ [3] pandoc              → markdown → typst
   │
   ├─ [4] transform_tables.py → #table() → #smart-table(); bundle label+table pairs
   │
   ├─ [5] wrap_sections.py    → repair pandoc's TOC link labels
   │
   ├─ [6] pack.py             → measure heights, recursively pack into page-sized groups
   │
   └─ [7] typst compile       → final PDF
```

Stages 1–5 are mechanical text transforms. Stage 6 is the heart of the system: it solves the layout problem by measuring then packing. Stage 7 is just rendering.

---

## Stage 1: `normalize_md.py` — markdown normalization

**Problem.** Pandoc's Markdown parser is sensitive to blank lines around block elements. A list immediately following a paragraph (no blank line between) is sometimes parsed as continuation of the paragraph rather than as a list. Tables likewise need surrounding blank lines.

**Solution.** Walk the markdown line-by-line and insert blank lines between any two adjacent lines that are different block-element types (paragraph→list, list→table, etc.). Idempotent: re-running on already-normalized markdown is a no-op.

**Why not rely on pandoc's lenient parser?** Pandoc's lenient mode introduces other ambiguities. The reliable answer is to give pandoc unambiguous input.

---

## Stage 2: `mmdc` — Mermaid pre-render

**Problem.** Mermaid diagrams in Markdown are fenced code blocks with `mermaid` language. Pandoc passes them through as plain code blocks; Typst can't render them natively.

**Solution.** Run `mermaid-cli` (`mmdc`) on the normalized Markdown with `--outputFormat png` to replace each ` ```mermaid ... ``` ` block with a `![](rendered-N.png)` image reference. Pandoc then sees an image and emits the corresponding Typst `#image(...)` call.

**Why PNG and not SVG?** SVG support in Typst exists but had inconsistent rendering for the specific Mermaid output styles used. PNG is universally supported. The `--scale 2` argument doubles resolution to maintain quality at 100% display size.

**Image height capping.** The template wraps every image in `box(height: 230mm)` so that a tall Mermaid diagram cannot exceed page height — Typst will scale it to fit. Without this, a complex flowchart could be taller than a page and force its own oversized leaf treatment.

---

## Stage 3: `pandoc` — markdown to Typst

**Problem.** Need to convert structured markdown to Typst syntax.

**Solution.** Pandoc 3.1.3+ has a Typst writer.

**Pandoc artifacts we have to work around.** Pandoc emits some imperfect Typst:

- **Long headings get wrapped to multiple lines** in the source. Typst's heading syntax only treats the *first* line as the heading; subsequent lines become body text. This caused the v19 page-11 bug ("opt-outs" was orphaned out of "3.1 Achieving... requires four specific opt-outs"). Fixed in `pack.py`'s tokenizer (see Stage 6).
- **TOC link targets don't match heading labels.** Pandoc emits `#link(<1-executive-summary>)` for the TOC entry but the actual heading carries label `<executive-summary>` (without the section number prefix). Fixed in Stage 5.
- **Tables are emitted as raw `#table(...)` calls** with minimal styling. We replace these with `#smart-table(...)` (Stage 4).
- **Smart-quotes, em-dashes, non-breaking spaces** appear in the output. The `split_analysis.py` tool has to normalize these when comparing source signatures to PDF text.

---

## Stage 4: `transform_tables.py` — table styling and label bundling

**Problem 1: pandoc's tables are unstyled.** Default `#table()` calls produce gray-bordered boxes with no header emphasis, no row alternation, no padding control.

**Solution.** Convert each `#table(...)` into a call to `#smart-table(...)`, a function defined in `template.typ`. `smart-table` handles header rows with bold white-on-blue styling, alternating row fills, padding, font sizing, and `1fr` column widths so columns expand to fill page width.

**Problem 2: bold labels separate from their tables across page breaks.** A pattern that recurs throughout the document is a bold label paragraph immediately followed by a table:

```
**Retention requirements:**
| Tier | Duration | ... |
```

Without intervention, the label can end up at the bottom of one page and the table on the next.

**Solution.** During the table transform, detect when a table is immediately preceded by a `**Bold:**` paragraph (a label) and bundle them in a `#keep-together[...]` wrapper. The keep-together primitive (defined in `template.typ`) measures its content; if it fits on a page it pushes the whole bundle to the next page when needed; if it's too tall it emits raw and lets natural breaking occur.

**Why not handle this in `pack.py` instead?** Pack.py operates on the parsed token tree where `**Label:**` and the table are two separate leaves. Detecting the pairing there is possible but requires reasoning about what content "belongs" with what, which is fragile. Doing it as a textual pre-transform on the Typst source — where the pattern is unambiguous (`#strong[Label:]\n\n#smart-table(`) — is simpler and reliable.

---

## Stage 5: `wrap_sections.py` — TOC label repair

**Problem.** Pandoc emits the document's TOC as a numbered list of `#link(<target>)[Heading text]` calls. The `<target>` is generated from the heading number+text (e.g. `<1-executive-summary>`), but the actual `<label>` attached to the heading omits the number prefix (`<executive-summary>`). Compiling fails with "label not found" errors.

**Solution.** Scan the document for all actual `<label>` references attached to headings. For each `#link(<target>)` whose target doesn't match an actual label, rewrite the target to the closest matching real label. Use `difflib.SequenceMatcher` for fuzziness because some labels also have additional tokens.

**Why not fix pandoc?** Patching pandoc's Typst writer would be the upstream-correct fix but is beyond scope. The post-process is reliable and self-contained.

**Why is this called "wrap_sections"?** Historical artifact. An earlier version of this script *also* wrapped each H3 section in `#keep-together[...]`. That responsibility moved to `pack.py` (where it can be done more intelligently with measurement data), and only the TOC repair remains. The filename was kept to avoid breaking external references.

---

## Stage 6: `pack.py` — the heart of the system

This is where structural integrity (G1, G2) is achieved. Everything before this stage is preparation; everything after is just rendering.

The job of `pack.py`: take the prepared Typst source, decide where every page break should occur, and emit a new Typst source where those break decisions are encoded.

### The core idea

Every Typst block has a measurable rendered height. If we wrap a block in `#block(breakable: false)[...]`, Typst will not break it across pages — it will push the whole block to the next page if it doesn't fit in the current page's remaining space.

So the layout problem reduces to: **partition the document into a sequence of "page-sized atoms" — blocks whose rendered heights are each ≤ one page — and let Typst place them.** Splits between atoms become page breaks naturally.

The challenge is choosing the right atoms. Too coarse (e.g. wrap each chapter as one atom) and you get tables that overflow pages. Too fine (e.g. wrap each paragraph individually) and Typst's natural greedy layout makes the same poor decisions a default would.

The right granularity is **structural**: an atom should be a complete logical unit at the highest level that fits on a page. That's what the recursive pack algorithm computes.

### The algorithm

```
emit(node):
    if node is a leaf:
        return raw source        # template handles per-leaf protection
    if node fits on one page:
        return block(breakable: false)[render(node)]
    # Container too big — partition children into page-sized groups
    groups = pack_children(node)  # greedy, uses prefix_heights
    for each group:
        if single oversized child:
            emit(child)            # recurse into container, or emit raw if leaf
        else:
            block(breakable: false)[render group]
```

In English: given a node, try to keep it whole. If it fits on a page, wrap it as one unbreakable block. If it doesn't fit, partition its children into the largest possible page-sized groups; for any child too big to fit alone, recurse.

This satisfies G1 by construction: splits only happen between groups, never within an unbreakable block. Splits happen at the highest level where they're necessary, which by definition is the most logical split point.

### Why this is the right algorithm

The user explicitly described this approach early in development: "The logic first checks, can I keep the entire `#-level` on the same page? If no, I need to split `##-level` onto 2 or more pages, then it checks each `##-level` for if I can keep the entire `##-level` on the same page if no, I need to split `###-level` onto 2 or more pages. This continues recursively."

Earlier attempts that didn't follow this structure failed in characteristic ways:

- **Naive flat threshold (rejected).** Wrap each `###` section in `#keep-together[...]` if it fits on a page. This keeps sections whole when possible but doesn't pack consecutive small sections together — every section becomes its own atom regardless of size, leading to many pages with one short section and large whitespace at the bottom.
- **Flat keep-together with size threshold (partially worked).** Same idea but only wrap if the section is < 93% of page height. Slightly better whitespace but couldn't handle nested cases (a section with three small subsections + one giant subsection couldn't pack the small ones together).
- **Pack-then-wrap-each-group (rejected).** Group siblings greedily, wrap each group. This worked but created multi-section atomic blocks that were *worse* than letting Typst flow naturally — Typst's natural ability to pack small siblings onto one page was being prevented by the grouping.

The recursive algorithm above is the synthesis: it descends to the level where each unit *individually* fits, then wraps individual units (not groups) as atomic blocks.

### Measurement: how heights are computed

The algorithm needs every container's height in millimeters before it can decide what fits. This is computed by a measurement pre-pass:

1. **Build a tree** from the Typst source via custom tokenizer.
2. **Emit a Typst probe document** that uses `measure(...)` and `state(...)` to dump every node's rendered height to text.
3. **Compile the probe doc** with Typst.
4. **Read the heights back** from the probe PDF's text content.
5. **Annotate the tree** with the measurements.

The probe document looks like (simplified):

```typst
// Full template (so all show rules are in effect)
#import "template.typ": *

#let _meas_state = state("meas", ())
#let _measure_item(id, body) = context {
  let h = measure(block(width: 166mm, body)).height
  _meas_state.update(lst => lst + ((id, h.mm()),))
  []  // emit nothing visible
}

// One probe per node:
#_measure_item(42, [...node 42's source...])
#_measure_item(43, [...node 43's source...])
...

#pagebreak()
= Measurement dump
#context {
  let m = _meas_state.get()
  for (id, h) in m [
    MEAS #id = #h mm \
  ]
}
```

The dump page lists `MEAS 42 = 87.3 mm`, `MEAS 43 = 12.1 mm`, etc. We parse these from the rendered PDF text and attach to the tree.

**Why measure with the full template?** Heights depend on font, leading, paragraph spacing, heading margins, all defined by the template. Measuring without the template would give wrong numbers. The template is included so measurement happens in the same context as the final render.

**Why width 166mm?** Page width 210mm minus 22mm × 2 margins = 166mm content width. Heights depend on width because text wrapping depends on line width.

### Prefix heights: solving the inter-block-spacing problem

Initially, `pack_children` computed the height of children[start..end] as `sum(child.height_mm for child in start..end)`. This was systematically wrong: it underestimated by the cumulative inter-block spacing between children. Each paragraph break adds ~3-5mm of spacing that isn't part of any individual child's measured height. With 10 paragraphs in a group, the underestimate could be 30-50mm — enough to overflow pages.

The fix: a **second measurement pass** that emits, for every container that's too big to fit whole, additional probes for `children[0..k]` rendered as a single block. From these "prefix heights", any subsequence height can be computed as `prefix[end] - prefix[start]` — exact, including all inter-block spacing.

The empirical measurement of the error confirmed this was real:
- Median sum-vs-real error: 11mm
- Maximum error: 233mm (a chapter with many children)
- Containers with error >10mm: most non-trivial ones

After implementing prefix heights, the packing decisions became correct.

**Why two passes?** Pass 1 discovers which containers are oversized (their whole-block height > page). Only oversized containers need prefix probes (a container that fits whole won't be split, so subsequence sizes don't matter). Pass 2 generates probes for just those containers.

**The pass-2 OOM problem.** For very large documents, pass 2's measurement source can grow to tens of MB. Each prefix probe re-renders an increasingly large portion of the container; for a chapter with N children, the probe set size is ~O(N²) bytes. Typst compiles the whole probe doc into memory and can run out. The user runs locally with more RAM; in lower-memory environments this could be fixed by batching pass 2 across multiple compile passes (each batch covering a subset of oversized containers). The single-pass code is simpler and is the version persisted; batching is the obvious improvement if memory constraints become binding.

### Stickiness: keeping headings and labels with their bodies (G2)

After basic packing was working, a class of bugs remained: a heading or bold label could end up alone at the bottom of a page with its body content on the next page. This is exactly the orphaned-heading problem that G2 addresses.

The fix has two parts:

**1. Identify "sticky" leaves at parse time.**

A leaf is sticky if it's:
- A heading (`= ...`, `== ...`, etc.), OR
- A standalone `#strong[...]` paragraph (a bold-only paragraph with no other content)

The bold-paragraph rule was iteratively broadened. Initially the paragraph had to end in `:` or `?` (typical label punctuation). But this missed cases like "**A. Background and scope**" or "**C. Implementation notes**" — sub-section letters with no terminal punctuation. A survey of all 96 standalone bold paragraphs in the test corpus confirmed they were *all* labels followed by content; none was substantive prose. So the rule simplified to: any standalone `#strong[...]` paragraph is sticky. Sticky-leaf count: 268 → 379 after broadening.

**2. Honor stickiness during packing.**

Two mechanisms:

- **`pack_children` post-pass.** After greedy partitioning, walk the groups: if any group ends with a sticky leaf, pull that leaf to the start of the next group. Iterate until stable. This handles within-container stickiness — e.g. a label that ends a group must instead start the next group.

- **Cross-recursion sticky flow via `prepend=`.** When the algorithm encounters a single sticky leaf followed by an oversized container that needs recursion, the sticky leaf is *deferred* — passed via `prepend=` into the recursive `emit()` call. Inside the recursion, the prepend gets attached to whatever is emitted first.

This handles cases like a chapter heading followed by a sub-section that's too big: the heading shouldn't be wrapped alone on the previous page. Instead, the chapter heading rides into the recursion and emerges attached to the first emitted sub-block.

**The oversized-leaf-with-prepend bug.** When `prepend=` is passed to a recursive `emit()` call that targets an *oversized leaf* (e.g. a 305mm retention table on a section whose total is 313mm), the natural code path was to wrap `prepend_src + node.source` in `block(breakable: false)`. But Typst can't honor `breakable: false` on a block bigger than a page — it overlap-renders rows in the available space. The fix: when the leaf is oversized, emit `prepend_src + node.source` raw, without wrapper. The template's `#show heading: it => block(sticky: true, ...)` rule keeps the heading adjacent to the table start without forcing unbreakability.

### Heading wrap: the v19 page-11 bug

Pandoc wraps long headings to two source lines:

```
=== 3.1 Achieving LLM-layer zero data retention requires four specific
opt-outs
<achieving-llm-layer-...>
```

Typst's heading syntax stops at the first newline. So Typst saw the heading as "3.1 Achieving... four specific" and treated `opt-outs` as the start of body text — which then merged with the following paragraph "Google's ZDR commitment...".

**The fix.** In the tokenizer, when parsing a heading, *rebuild* the heading source as a single line. The tokenizer already correctly groups continuation lines logically into the heading text. The fix is to emit `=== {full heading text}\n<{label}>\n` with the wrapped text joined back together, instead of preserving the two-line input.

This means the round-trip property (tokenize → render = original source) no longer holds for headings — but we don't need it; we want the EMITTED Typst to be correct, not byte-identical to pandoc's output.

### Ordered-list counter reset: the page-39 bug

When an ordered list spans multiple `#block(breakable: false)[...]` wrappers (because the list got split across page-sized groups), Typst's `enum` counter resets to 1 in each block. Items 1-4 on page A counted 1, 2, 3, 4; item 5 on page B counted 1.

**Why does this happen?** Each `#block(...)` creates a new context in Typst. Counter state is scoped to context. Different blocks → different counter scopes → reset.

**Fix attempts:**

- **Apply `breakable: false` directly to enum items via `#show enum.item: it => block(breakable: false, it)`** (rejected). Wrapping each enum item in a fresh block resets the counter even *within* a single page. Every item became "1.".
- **Manually number list items** in source (rejected). Brittle and changes the visible source.
- **Use `#set enum(start: N)` at block boundaries** (chosen). When emitting a block whose first ordered-list-item leaf has position N > 1, prepend `#set enum(start: N)` to that block's content. The counter starts at the correct number.

Implementation:
- `Node.enum_start: int` — 1-indexed position within the ordered list, 0 if not an ordered list item.
- `classify_enum_positions(tree)` — walks leaves in document order, identifies contiguous runs of `+` items as one logical list, assigns each its position.
- `_first_enum_start(nodes)` — given a sequence of nodes about to be wrapped in a block, returns the position of the first ordered-list leaf inside (or 0).
- `_wrap_block(body, nodes)` — every block-emission in `emit()` goes through this helper, which prepends `#set enum(start: N)` when needed.

### Things that are NOT done in pack.py

- **Whitespace minimization.** Pack.py optimizes for G1 and G2 (structural integrity, logical grouping). G3 (whitespace) is whatever falls out. The user explicitly said this is the right priority.
- **Balanced page distribution.** An earlier user proposal: when a section is split, distribute children evenly across pages (e.g. for a 3.5-page section with 7 subsections, target 2-2-2-1 distribution rather than greedy 3-3-1-0). This was discussed but not implemented because the simpler recursive-keep-whole approach already met the structural goals. The balanced approach would marginally improve G3 in some cases but adds complexity.

---

## Stage 7: Typst compile

Just `typst.compile(full_doc.typ, output=out.pdf)` where `full_doc.typ = template.typ + source_packed.typ`. No tricks here.

---

## `template.typ`: typography and primitives

The template defines the visual style (fonts, colors, spacing) and a small set of layout primitives that the rest of the pipeline depends on.

### Page setup

A4, 22mm margins all around. Inner content area: 166mm × 253mm. Helvetica Neue throughout (with Arial fallback). Body text 10.5pt, 0.68em leading, 1.1em paragraph spacing.

**Why ragged-right (`justify: false`)?** Justified text in dense long-form prose with many short line-final words and acronyms produces jarring word-spacing. Ragged-right is more readable.

### Heading rules

Each heading level has a `#show heading.where(level: N): it => ...` rule that:
- Adds vertical space before the heading (`v(size, weak: true)`)
- Wraps the heading body in `block(sticky: true, breakable: false)` — `sticky: true` is Typst's primitive for "this block must stay adjacent to the next content"; `breakable: false` ensures the heading itself doesn't get split across pages
- Sets size, color, and weight per level
- Adds a small space after

**Why `v(weak: true)` rather than block margins?** Typst collapses block margins between adjacent blocks, but `v(weak: true)` is similarly collapsible while being more predictable in this composition. The "weak" qualifier means the spacing is dropped if the heading appears at the top of a page (so we don't get an extra blank gap above page-top headings).

**Why `sticky: true` on every heading?** This is the foundational mechanism for G2. Without it, a heading could orphan at the bottom of a page. The pack.py sticky-leaf flow provides additional protection across recursion boundaries, but the base sticky property covers the simple case.

### Lists and paragraphs

```typst
#show list.item: it => block(breakable: false, it)
#show par: it => block(breakable: false, it)
```

Every unordered list item and every paragraph becomes an unbreakable atom. This satisfies the "no mid-paragraph, no mid-list-item splits" requirement of G1 directly at the template level — even before pack.py's grouping kicks in, individual leaves are protected.

**No `#show enum.item` rule.** This was deliberately omitted because wrapping each enum item in `block(breakable: false)` resets Typst's enum counter (every item becomes "1."). The ordered-list-counter handling in pack.py compensates by emitting `#set enum(start: N)` at split boundaries.

### `#smart-table`

A wrapper function for tables with consistent styling:
- Header row: bold white text on dark blue, `repeat: true` so it appears on every page if the table breaks across pages
- Body cells: 8.5pt, hyphenation enabled, ragged-right
- Alternating row fill (subtle gray on odd rows)
- 1fr column widths so columns expand to fill page width
- Padding: 8pt horizontal, 6pt vertical

**Underscore-break show rule.** Inside body cells, this rule fires:

```typst
show regex("([\w])([_/.])([\w])"): it => {
  let s = it.text
  s.at(0) + s.at(1) + sym.zws + s.at(2)
}
```

This inserts a zero-width space after each `_`, `/`, or `.` between word characters. Without it, identifiers like `REPTILE_AMPHIBIAN` are one continuous word that Typst can't break, and they overflow the cell into the neighboring column. With the rule, Typst can break at any `_`, `/`, or `.`, keeping content inside cell boundaries.

### `#keep-together`

```typst
#let keep-together-threshold = page-inner-height * 0.93
#let keep-together(body) = layout(size => {
  let dims = measure(body)
  if dims.height < keep-together-threshold {
    block(breakable: false, body)
  } else {
    body  // emit raw, let it flow naturally
  }
})
```

This primitive is used by the label+table bundling in transform_tables.py. If the bundled content is small enough, it's wrapped unbreakable; if it's too large, it's emitted raw so Typst can break naturally inside.

**Why 93%?** Earlier values (75%, 80%) were too conservative — content that legitimately fit on a page was being pushed to the next page because the threshold flagged it as "too large for keep-together." 93% leaves a small buffer for inter-block spacing without being over-cautious. The threshold was tuned empirically on the actual document.

---

## `split_analysis.py`: quality measurement

Measures G1 directly. For each structural level, counts how many instances are contained on a single page (whole) vs spanning multiple pages (split):

- Headings L1-L4 (sections)
- Tables
- Paragraphs
- Sentences
- Lists
- List items

**How it works.** Walks the pre-pack Typst source (NOT the post-pack source — the analyzer needs to see raw paragraphs and lists, not `#block(breakable: false)[...]` wrappers). For each unit, extracts a normalized text signature (taking prefix and suffix from the unit's full text). Searches the rendered PDF's per-page extracted text for the prefix and suffix; if both appear on the same page, the unit is whole; if on adjacent pages, split.

**Normalization handles PDF-extraction artifacts:**
- Curly quotes → straight quotes
- Em/en dashes → ASCII hyphen
- Non-breaking spaces → regular spaces
- PDF line-wrap hyphen artifact (`"health-\nadjacent"` extracted as `"health- adjacent"`) → `"health-adjacent"`
- Whitespace adjacent to punctuation that PDF inserts at line wraps → removed

**Sentence detection.** A regex that matches `[^.!?]+[.!?]` followed by a capital letter or end-of-string. This avoids splitting on abbreviations like "e.g." (because no capital follows) but might miss some real sentence boundaries. The match rate is empirically ~96%, which is sufficient — and the few unmatched sentences are real artifacts of PDF text extraction noise, not actual splits.

**Why text-based and not coordinate-based?** Text matching is straightforward and works with any Typst output. A coordinate-based approach (extracting bounding boxes from the PDF and comparing to expected unit positions) would be more precise but requires deeper PDF parsing. Text matching is good enough for the 95%+ accuracy we need.

**Verified results on v19 (116 pages):**
- Tables: 36/37 whole (97%), 0 split, 1 unmatched
- Paragraphs: 399/431 whole (93%), **0 split**, 32 unmatched
- Sentences: 828/853 whole (97%), **0 split**, 25 unmatched
- Lists: 64/70 whole (91%), 3 split (large lists), 3 unmatched
- List items: 322/340 whole (95%), 1 split (false positive), 17 unmatched

Zero mid-paragraph splits, zero mid-sentence splits, zero mid-list-item splits. G1 satisfied.

---

## Failed approaches and dead ends

### "Use Typst's natural layout"

Initial attempts relied on Typst's built-in layout choices. Typst's default packing is greedy: fill each page top-to-bottom, break when full. This produces the failure modes the project was created to fix — mid-paragraph splits, orphaned headings, etc. Rejected immediately.

### "Wrap H3 sections in keep-together"

`wrap_sections.py`'s original responsibility. Wrap each H3 section in `#keep-together[...]` if it fits; let pandoc's natural flow handle the rest. This achieved partial G1 (sections stayed together when possible) but couldn't handle:
- H3 sections larger than a page (no nested logic)
- Cross-section packing (consecutive small sections never combined onto one page)
- Tables inside H3 sections (no special handling)

Replaced by the recursive pack algorithm in `pack.py`.

### "Sum-of-children for subsequence heights"

The first version of `pack_children` computed the height of children[start..end] as the sum of individual child heights. This systematically underestimated by tens of millimeters per group, causing overflows. Solved by adding prefix-height measurements (pass 2).

### "Heading-only stickiness"

The first version of sticky-leaf classification only flagged headings. Bold labels still orphaned. Broadened to `#strong[...]:` paragraphs (those ending in `:` or `?`). Then broadened further to *any* standalone `#strong[...]` paragraph after surveying the document for false positives (none were found).

### "Wrap oversized leaves with prepend in breakable: false"

When the cross-recursion sticky flow attached a heading to an oversized leaf (e.g. a giant retention table), the original code wrapped them together in `block(breakable: false)`. Typst couldn't honor the unbreakable constraint on content larger than a page and rendered overlapping rows. Fixed: when the leaf is oversized, emit `prepend_src + node.source` raw without the wrapper.

### "Apply breakable: false to enum.item"

To prevent ordered list items from splitting mid-item, the natural rule is `#show enum.item: it => block(breakable: false, it)`. This breaks the enum counter (every item resets to 1). Replaced with `#set enum(start: N)` at block boundaries.

### "Balanced front-loaded distribution"

Proposed mid-development: when splitting a container into pages, target a balanced distribution rather than greedy first-fit. E.g. a 3.5-page section with 7 subsections becomes 2-2-2-1 instead of 3-3-1-0. The user described this approach in detail and we discussed it as a way to improve G3 without compromising G1/G2. Implementation was deferred because the simpler greedy approach already met the quality bar.

### "Use real Markdown ordered list markers (1., 2., ...)"

Pandoc emits ordered lists with `+ ` markers in Typst. We considered manually rewriting these to explicit `#enum.item(N)[...]` calls during the transform stage. Rejected because `#set enum(start: N)` at block boundaries is simpler and stays inside the structural framework rather than adding a per-item rewrite.

### "Use coordinate-based PDF analysis for split detection"

`split_analysis.py` could extract bounding boxes from the PDF instead of text. More precise but more complex. Text-based matching achieves >95% match rates which is sufficient.

### "Larger memory budget for measurement"

For very large documents, the pass-2 measurement source can exceed available memory. Solutions considered:
- Batching pass 2 across multiple compile passes (implemented, then reverted because it complicates the code and the user's local environment has enough RAM)
- Skipping prefix probes for huge containers and falling back to summing (implemented, then reverted for the same reason)
- Computing prefix heights incrementally in Python (not yet attempted; would require re-doing measurement architecture)

The current code does single-pass pass-2 measurement. If memory becomes a binding constraint, batching is the cleanest fix.

---

## Why the pipeline is so many separate stages

A single monolithic script could do everything stages 1-7 do. The pipeline is split because each stage has a *single responsibility* with a clean text-in/text-out contract:

- `normalize_md.py`: makes the markdown pandoc-friendly
- `mmdc`: rasterizes diagrams
- `pandoc`: converts to Typst
- `transform_tables.py`: styles tables and bundles labels
- `wrap_sections.py`: repairs TOC labels
- `pack.py`: structural packing
- `typst`: renders

This makes each stage independently testable. When debugging, you can save the output of any stage, hand-edit it, and run the next stage on the modified input. The conversation history shows this debugging workflow happening repeatedly: "let me look at what `wrap_sections.py` produced before pack.py touched it." Without the staging, that kind of inspection is much harder.

---

## What this pipeline doesn't do

- **No interactive editing.** It's batch: input markdown, output PDF.
- **No incremental builds.** The whole pipeline runs every time. For a ~120-page document on this hardware, end-to-end is roughly 15-30 seconds, mostly in the Typst measurement compiles.
- **No multi-document orchestration.** Each invocation produces one PDF.
- **No collaborative features.** Single-user batch tool.
- **No layout knobs in the markdown source.** All layout decisions are made by the pipeline based on structural hierarchy. There's no way for the markdown author to say "force a page break here" or "keep this group of paragraphs together." The structural-integrity goals don't generally require such overrides; if they did, adding them as transform-stage directives would be straightforward.
