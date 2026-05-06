// ============================================================
// Typst template for quirefold-rendered long-form documents
// Design goals (in priority order for dense long-form prose):
//   1. Readability of long prose — tight but not cramped leading
//   2. Headings bound to their following content
//   3. Tables never split mid-row; small tables kept whole
//   4. Short sections kept whole IFF they fit in remaining space
//     (this is the critical improvement over CSS-based approach)
//   5. Large images scale to fit a single page
// ============================================================

// ---------- Document-wide configuration ----------

// Optional: set the PDF metadata title here.
// #set document(title: "Your document title here")

#set page(
  paper: "a4",
  margin: (top: 22mm, bottom: 22mm, left: 22mm, right: 22mm),
  // Optional running header — uncomment and replace the placeholder.
  // header: context {
  //   if counter(page).get().first() > 1 [
  //     #set text(size: 8pt, fill: rgb("#aaaaaa"))
  //     Your running header text
  //   ]
  // },
  footer: context {
    if counter(page).get().first() > 1 [
      #set align(right)
      #set text(size: 9pt, fill: rgb("#888888"))
      #counter(page).display()
    ]
  },
)

#set text(
  font: ("Liberation Sans", "DejaVu Sans"),
  size: 10.5pt,
  lang: "en",
  hyphenate: false,  // long-form prose: don't hyphenate across line breaks
)

#set par(
  leading: 0.68em,       // space between lines within a paragraph
  spacing: 1.1em,        // space between paragraphs
  justify: false,        // left-align — better readability in dense long-form prose
  first-line-indent: 0pt,
  linebreaks: "optimized", // Knuth-Plass line breaking
)

// ---------- Lists & paragraphs ----------
// Prevent unordered-list items from splitting across pages.
#show list.item: it => block(breakable: false, it)
// NOTE: We do NOT apply breakable: false to enum.item because wrapping in
// a fresh block() resets Typst's ordered-list counter.
// Prevent paragraphs from being split mid-flow.
#show par: it => block(breakable: false, it)

// ---------- Colors ----------

#let heading-color = rgb("#1a2e4a")
#let subheading-color = rgb("#2a4a6a")
#let subsubheading-color = rgb("#3a5a7a")
#let rule-color = rgb("#c8d6e5")
#let code-bg = rgb("#f5f7fa")
#let code-border = rgb("#1a2e4a")
#let table-header-bg = rgb("#1a2e4a")
#let table-row-alt = rgb("#f4f7fb")
#let table-border = rgb("#dde4ec")

// ---------- Layout constants ----------

#let page-inner-height = 297mm - 44mm  // A4 minus top+bottom margins
#let keep-together-threshold = page-inner-height * 0.85

// ---------- Headings ----------
// IMPORTANT: Use explicit v() for below-heading spacing because block() 
// margins collapse with the following paragraph's above-spacing and get
// eaten. v() with weak:false guarantees the gap stays.

#show heading.where(level: 1): it => {
  context {
    let prev = query(selector(heading.where(level: 1)).before(it.location()))
    if prev.len() > 1 { pagebreak(weak: true) }
  }
  v(1.8em, weak: true)
  block(breakable: false, {
    set text(size: if counter(heading).get().first() == 1 { 24pt } else { 20pt },
             fill: heading-color, weight: "bold")
    it.body
  })
  context {
    if counter(heading).get().first() > 1 {
      v(0.3em)
      line(length: 100%, stroke: 2pt + heading-color)
    }
  }
  v(1em)
}

#show heading.where(level: 2): it => {
  v(1.8em, weak: true)
  block(sticky: true, breakable: false, {
    set text(size: 15pt, fill: heading-color, weight: "bold")
    it.body
    v(0.25em)
    line(length: 100%, stroke: 0.7pt + rule-color)
  })
  v(0.7em)
}

#show heading.where(level: 3): it => {
  v(1.4em, weak: true)
  block(sticky: true, breakable: false, {
    set text(size: 12pt, fill: subheading-color, weight: "bold")
    it.body
  })
  v(0.5em)
}

#show heading.where(level: 4): it => {
  v(1.1em, weak: true)
  block(sticky: true, breakable: false, {
    set text(size: 10.5pt, fill: subsubheading-color, weight: "bold", style: "italic")
    it.body
  })
  v(0.35em)
}

#show heading.where(level: 5): it => {
  v(0.9em, weak: true)
  block(sticky: true, breakable: false, {
    set text(size: 10.5pt, weight: "bold")
    it.body
  })
  v(0.25em)
}

// ---------- Links ----------

#show link: it => {
  set text(fill: rgb("#1a5fa8"))
  it
}

// ---------- Horizontal rules ----------

#show line: it => it  // pass through
// Markdown '---' renders as a line element; style it
#let hrule = {
  v(0.8em)
  line(length: 100%, stroke: 0.5pt + rule-color)
  v(0.6em)
}

// ---------- Inline code and code blocks ----------

#show raw.where(block: false): it => {
  box(
    fill: rgb("#eef1f5"),
    inset: (x: 3pt, y: 1pt),
    outset: (y: 1pt),
    radius: 2pt,
    text(font: "DejaVu Sans Mono", size: 8.8pt, it)
  )
}

#show raw.where(block: true): it => {
  block(
    fill: code-bg,
    stroke: (left: 2pt + code-border),
    inset: (x: 12pt, y: 9pt),
    radius: 2pt,
    width: 100%,
    breakable: true,  // allow large code blocks to split
    text(font: "DejaVu Sans Mono", size: 8pt, it)
  )
}

// ---------- Tables ----------
// smart-table: the table-as-function we transform pandoc's raw #table() calls
// into. Takes columns + a sequence of row tuples (first row = header).
//
// Design:
//   • Styled header row (dark fill, white bold) that repeats on page breaks
//     via table.header(repeat: true).
//   • Alternating body row backgrounds for readability.
//   • Rows never split mid-cell (Typst tables break at row boundaries by
//     default, so this is automatic).
//   • keep-together logic applies at the OUTER wrap (label + table pair),
//     not at the table itself, because tables must remain breakable to
//     handle the genuinely-oversized case.
//
// Important: we do NOT wrap the table in `block(breakable: false)`. If the
// table is bigger than a page, an unbreakable wrapper would cause Typst to
// fail or overflow. The keep-together decision is made at the PAIR level
// (label + table) by the surrounding `#keep-together[...]` wrapper, which
// uses measure() to decide whether to force the pair onto one page or let
// the table break naturally.

#let smart-table(columns: 1, ..rows) = {
  let row-list = rows.pos()
  if row-list.len() == 0 { return }

  let header-row = row-list.at(0)
  let body-rows = row-list.slice(1)

  // Column widths: use equal fractions of available width.
  let col-widths = (1fr,) * columns

  // Build header cells with styled fill. Enable hyphenation + force linebreak
  // on long words so header text wraps rather than pushing the column wider.
  let header-cells = header-row.map(c => table.cell(
    fill: table-header-bg,
    breakable: false,
    {
      set text(fill: white, weight: "semibold", size: 8.5pt, hyphenate: true)
      set par(justify: false, leading: 0.5em, linebreaks: "simple")
      show: it => { set block(breakable: false); it }
      c
    }
  ))

  // Build body cells with alternating row shading
  let body-cells = body-rows.enumerate().map(((idx, row)) => {
    let row-fill = if calc.odd(idx) { table-row-alt } else { none }
    row.map(c => table.cell(
      fill: row-fill,
      {
        set text(size: 8.5pt, hyphenate: true)
        set par(justify: false, leading: 0.5em)
        // Zero-width spaces for snake_case / path / dotted-identifier breaks
        // are inserted at preprocess time in transform_tables.py; doing it
        // here as a `show regex(...)` rule blew up typst's allocator on
        // documents with many tables (the rule re-runs per cell across the
        // replicated measurement passes).
        c
      }
    ))
  }).flatten()

  block(
    above: 1em,
    below: 1em,
    breakable: true,
    table(
      columns: col-widths,
      stroke: 0.3pt + table-border,
      inset: (x: 5pt, y: 4pt),
      align: left + horizon,
      table.header(repeat: true, ..header-cells),
      ..body-cells,
    )
  )
}

// ---------- Blockquotes ----------

#show quote: it => {
  block(
    fill: rgb("#f8fafc"),
    stroke: (left: 2.5pt + rgb("#a0b8d0")),
    inset: (x: 11pt, y: 5pt),
    width: 100%,
    breakable: true,
    {
      set text(style: "italic", fill: rgb("#444"))
      it.body
    }
  )
}

// ---------- Images / figures ----------
// Scale-to-fit: bound by the inner page width and a height cap, preserving
// aspect ratio, never enlarged beyond natural size. Typst has no CSS max-*,
// so we measure the image's natural size, compute a uniform scale factor,
// and apply it via `scale(reflow: true)` which reserves the scaled space.
//
// We bound width against the constant `page-inner-width`, NOT against
// `layout(size => size.width)`. Pandoc wraps every image in
// `#box(width: <natural-pt>, image(...))`, so a layout-based width would
// see the box's own explicit width and never shrink — defeating the rule.

#let page-inner-width = 210mm - 2 * 22mm   // A4 minus left/right margins
#let image-max-height = 230mm

#show image: it => context {
  let nat = measure(it)
  let s = calc.min(
    page-inner-width / nat.width,
    image-max-height / nat.height,
    1.0,
  )
  align(center, block(breakable: false,
    scale(s * 100%, reflow: true, it),
  ))
}

// Suppress pandoc's auto-generated figure captions ("Figure 1: diagram")
#show figure: it => it.body
#show figure.caption: none

// ---------- Shims for pandoc-generated commands ----------
// Pandoc emits `#horizontalrule` for markdown `---` and `#blockquote[...]`
// for markdown `> ...`. Define them.

#let horizontalrule = {
  v(0.8em)
  line(length: 100%, stroke: 0.5pt + rule-color)
  v(0.6em)
}

#let blockquote(body) = block(
  fill: rgb("#f8fafc"),
  stroke: (left: 2.5pt + rgb("#a0b8d0")),
  inset: (x: 11pt, y: 5pt),
  width: 100%,
  breakable: true,
  {
    set text(style: "italic", fill: rgb("#444"))
    body
  }
)

// ---------- The keep-together primitive ----------
// Wraps content in a block that measures its own natural height; if under a
// threshold it's kept unbreakable, otherwise it flows naturally. This gives
// "keep sections whole where possible, split large ones at natural bounds".

#let keep-together(body) = layout(size => {
  let dims = measure(block(width: size.width, body))
  if dims.height < keep-together-threshold {
    block(breakable: false, body)
  } else {
    body
  }
})

// ---------- Strong/emphasis ----------

#show strong: it => {
  set text(weight: "bold", fill: heading-color)
  it
}
