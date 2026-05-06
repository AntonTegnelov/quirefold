#!/usr/bin/env python3
"""Recursive structural packing for a Typst document.

Pipeline: tokenize → build tree → measure each node's height → recursive emit
that wraps containers/groups in `block(breakable: false)` so Typst pushes
them as units, descending into oversized containers and splitting only
where unavoidable.

Sticky leaves (headings + standalone bold-only paragraphs) get pulled
forward across group boundaries so they stay with the next content,
including across recursion boundaries (heading + oversized child).
"""
import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Set


# ============================================================
# Data model
# ============================================================

@dataclass
class Node:
    kind: str                         # 'container' or 'leaf'
    source: str = ''                  # raw Typst source for leaves
    level: int = 0                    # heading depth for containers
    heading_text: str = ''            # for debugging
    children: List['Node'] = field(default_factory=list)
    height_mm: float = 0.0            # whole-block measured height
    prefix_heights: List[float] = field(default_factory=list)
    id: int = -1
    sticky: bool = False              # heading or standalone bold label
    # For ordered-list-item leaves: 1-indexed position within the list. 0 means
    # this leaf is not an ordered-list item, or no number override needed.
    # When emitting a block whose first ordered-list leaf has enum_start > 1,
    # we prepend `#set enum(start: N)` so Typst's counter resumes correctly.
    enum_start: int = 0


# ============================================================
# Tokenizer
# ============================================================

HEADING_LINE = re.compile(r'^(=+)\s+')
LABEL_LINE = re.compile(r'^<([^>]+)>\s*\n')
HASH_IDENT = re.compile(r'^#(\w[\w-]*)')


def _find_balanced(text: str, start: int, open_ch: str, close_ch: str) -> int:
    assert text[start] == open_ch
    depth = 1
    i = start + 1
    n = len(text)
    while i < n and depth > 0:
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
        i += 1
    return i


def tokenize(source: str) -> list:
    """Parse Typst source into a flat list of token dicts.

    Each token is one of:
      {'kind': 'heading', 'level': N, 'text': '...', 'label': '...', 'source': '...'}
      {'kind': 'leaf', 'source': '...'}
    """
    tokens = []
    i = 0
    n = len(source)

    while i < n:
        # Skip blank lines between tokens
        while i < n and source[i] == '\n':
            i += 1
        if i >= n:
            break
        start = i

        # Fenced code block?
        if source[i:i+3] == '```':
            eol = source.find('\n', i)
            if eol == -1:
                eol = n
            end_fence = source.find('\n```', eol)
            if end_fence != -1:
                end = end_fence + 4  # past '\n```'
                eol_close = source.find('\n', end)
                end = eol_close + 1 if eol_close != -1 else n
                while end < n and source[end] == '\n':
                    end += 1
                tokens.append({'kind': 'leaf', 'source': source[start:end]})
                i = end
                continue

        # Heading?
        if source[i] == '=':
            m = HEADING_LINE.match(source[i:])
            if m:
                level = len(m.group(1))
                eol = source.find('\n', i)
                if eol == -1:
                    eol = n
                heading_text = source[i + m.end():eol].strip()
                k = eol + 1
                # Heading continuation lines: any non-blank line that doesn't
                # start a new heading or a label.
                while k < n:
                    next_eol = source.find('\n', k)
                    if next_eol == -1:
                        next_eol = n
                    next_line = source[k:next_eol]
                    if not next_line.strip():
                        break
                    if next_line.startswith('<') and next_line.rstrip().endswith('>'):
                        break
                    if HEADING_LINE.match(next_line):
                        break
                    heading_text += ' ' + next_line.strip()
                    k = next_eol + 1
                # Optional label
                label = ''
                lm = LABEL_LINE.match(source[k:])
                if lm:
                    label = lm.group(1)
                    k += lm.end()
                # IMPORTANT: rebuild the source as a single-line heading.
                # Pandoc wraps long headings to multiple lines, but Typst
                # reads only the FIRST line as the heading; subsequent lines
                # become body paragraphs. We unwrap so Typst sees one line.
                marker = '=' * level
                rebuilt = f'{marker} {heading_text}\n'
                if label:
                    rebuilt += f'<{label}>\n'
                tokens.append({
                    'kind': 'heading',
                    'level': level,
                    'text': heading_text,
                    'label': label,
                    'source': rebuilt,
                })
                i = k
                continue

        # Top-level #ident[...] or #ident(...) call?
        if source[i] == '#':
            m = HASH_IDENT.match(source[i:])
            if m:
                after_name = i + m.end()
                j = after_name
                while j < n and source[j] == ' ':
                    j += 1
                if j < n and source[j] == '[':
                    end = _find_balanced(source, j, '[', ']')
                elif j < n and source[j] == '(':
                    end = _find_balanced(source, j, '(', ')')
                    # Check for trailing [body] after (...) — e.g. #quote(block: true)[...]
                    k = end
                    while k < n and source[k] == ' ':
                        k += 1
                    if k < n and source[k] == '[':
                        end = _find_balanced(source, k, '[', ']')
                else:
                    # Standalone like #horizontalrule
                    end = source.find('\n', i)
                    end = end + 1 if end != -1 else n
                while end < n and source[end] == '\n':
                    end += 1
                tokens.append({'kind': 'leaf', 'source': source[start:end]})
                i = end
                continue

        # Otherwise: paragraph or list — collect until blank line, balanced
        j = i
        bd = pd = 0
        while j < n:
            c = source[j]
            if c == '[':
                bd += 1
            elif c == ']':
                bd -= 1
            elif c == '(':
                pd += 1
            elif c == ')':
                pd -= 1
            elif c == '\n' and bd == 0 and pd == 0:
                if j + 1 >= n:
                    break
                if source[j + 1] == '\n':
                    break
                nl = source[j + 1:j + 20]
                if HEADING_LINE.match(nl) or (nl and nl[0] == '#'):
                    break
            j += 1
        end = j
        while end < n and source[end] == '\n':
            end += 1
        tokens.append({'kind': 'leaf', 'source': source[start:end]})
        i = end

    return tokens


# ============================================================
# Tree builder
# ============================================================

def build_tree(tokens: list) -> Node:
    root = Node(kind='container', level=0, heading_text='<root>')
    stack = [root]
    next_id = [0]

    def new_id() -> int:
        next_id[0] += 1
        return next_id[0]

    for tok in tokens:
        if tok['kind'] == 'heading':
            while stack and stack[-1].level >= tok['level'] and stack[-1].level != 0:
                stack.pop()
            container = Node(
                kind='container',
                level=tok['level'],
                heading_text=tok['text'],
                id=new_id(),
            )
            heading_leaf = Node(kind='leaf', source=tok['source'], id=new_id())
            container.children.append(heading_leaf)
            stack[-1].children.append(container)
            stack.append(container)
        else:
            leaf = Node(kind='leaf', source=tok['source'], id=new_id())
            stack[-1].children.append(leaf)
    return root


# ============================================================
# Stickiness classification
# ============================================================

_STRONG_ONLY = re.compile(
    r'^\s*#strong\[((?:[^\[\]]|\[[^\]]*\])*)\]\s*$',
    re.DOTALL,
)


def _is_bold_label(source: str) -> bool:
    """Return True if leaf source is a bare standalone #strong[...] paragraph.
    Such paragraphs are mini-labels (almost always followed by content)."""
    return _STRONG_ONLY.match(source.strip()) is not None


def classify_sticky(tree: Node) -> int:
    n = 0
    def walk(node):
        nonlocal n
        if node.kind == 'leaf':
            s = node.source.lstrip()
            if s.startswith('=') or _is_bold_label(node.source):
                node.sticky = True
                n += 1
        for c in node.children:
            walk(c)
    walk(tree)
    return n


# ============================================================
# Ordered-list position classification
# ============================================================

def _is_ordered_list_item(source: str) -> bool:
    """True if a leaf is a `+ ...` ordered-list item (Typst syntax)."""
    return bool(re.match(r'^\s*\+\s', source))


def classify_enum_positions(tree: Node) -> int:
    """Walk the tree's leaves in document order. Each contiguous run of
    ordered-list-item leaves forms one logical list. Annotate each item
    with its 1-indexed position in the list. Returns count annotated.

    Only the FIRST item in a list (position 1) gets enum_start = 1, but we
    always store the actual number; emit() decides when to use it.
    """
    leaves = []
    def collect(n):
        if n.kind == 'leaf':
            leaves.append(n)
        for c in n.children:
            collect(c)
    collect(tree)

    count = 0
    pos = 0
    for leaf in leaves:
        if _is_ordered_list_item(leaf.source):
            pos += 1
            leaf.enum_start = pos
            count += 1
        else:
            pos = 0
    return count


# ============================================================
# Render (round-trip)
# ============================================================

def render_node(node: Node) -> str:
    if node.kind == 'leaf':
        return node.source
    return ''.join(render_node(c) for c in node.children)


# ============================================================
# Measurement
# ============================================================

MEASUREMENT_PREAMBLE = r'''
#let _meas_state = state("meas", ())
#let _measure_item(id, body) = context {
  let h = measure(block(width: %dmm, body)).height
  _meas_state.update(lst => lst + ((id, h.mm()),))
  []
}
'''

MEASUREMENT_POSTAMBLE = r'''
#pagebreak()
#show heading: it => it.body
= Measurement dump
#context {
  let m = _meas_state.get()
  for (id, h) in m [
    MEAS #id = #h mm \
  ]
}
'''

LINK_RE = re.compile(r'#link\(<[^>]+>\)(\[[^\]]*\])')
# Pandoc emits images as `#image("path")` (optionally with extra args, but
# our pipeline never adds them). Match the simple form.
IMAGE_RE = re.compile(r'#image\("[^"]*"\)')
# Worst-case rendered height under the template's scale-to-fit image rule
# is image-max-height (230mm). Using that as a placeholder keeps prefix
# probes from re-decoding the SVG/raster across many measurement passes
# (which can OOM the typst compile). Over-estimating image height only
# costs the occasional early page break, which is harmless.
IMAGE_PLACEHOLDER = '#block(breakable: false, box(width: 100%, height: 230mm))'


def _strip_measurement_refs(source: str) -> str:
    """Sanitize a Typst snippet for use in an isolated measurement context:

    1. `#link(<label>)[text]` — the label isn't in scope when measuring an
       isolated subtree, so replace with just the link's body text.
    2. `#image("path")` — replaced with a fixed-size placeholder upper
       bound (image-max-height in the template). Avoids re-decoding the
       SVG/raster N times across prefix probes (which OOMs the compile).
    """
    s = LINK_RE.sub(lambda m: m.group(1), source)
    s = IMAGE_RE.sub(IMAGE_PLACEHOLDER, s)
    return s


PREFIX_BASE = 100_000_000
PREFIX_STRIDE = 10_000


def _encode_prefix_id(container_id: int, k: int) -> int:
    return PREFIX_BASE + container_id * PREFIX_STRIDE + k


def emit_measurement_source(tree: Node, template_source: str,
                            page_inner_width_mm: float = 166,
                            include_prefix_for: Set[int] = None) -> str:
    parts = [template_source]
    parts.append(MEASUREMENT_PREAMBLE % page_inner_width_mm)

    def walk(n, out):
        out.append(n)
        for c in n.children:
            walk(c, out)
    all_nodes = []
    walk(tree, all_nodes)

    if include_prefix_for is None:
        for node in all_nodes:
            if node.id == -1:
                continue
            body = node.source if node.kind == 'leaf' else render_node(node)
            body = _strip_measurement_refs(body)
            parts.append(f'\n#_measure_item({node.id}, [\n{body}\n])\n')
    else:
        for node in all_nodes:
            if node.kind != 'container' or node.id not in include_prefix_for:
                continue
            N = len(node.children)
            for k in range(1, N):
                prefix_body = ''.join(render_node(c) for c in node.children[:k])
                prefix_body = _strip_measurement_refs(prefix_body)
                pid = _encode_prefix_id(node.id, k)
                parts.append(f'\n#_measure_item({pid}, [\n{prefix_body}\n])\n')

    parts.append(MEASUREMENT_POSTAMBLE)
    return ''.join(parts)


def compile_measurement(source: str, work_dir: str, tag: str = '') -> dict:
    """Compile a measurement source via the `typst` CLI subprocess and parse
    the emitted MEAS lines from the PDF.

    Subprocess (rather than the in-process `typst` Python module) ensures
    the memory of each compile is fully released on process exit. Pass-2
    prefix probes can produce very large measurement files; batching plus
    subprocess isolation prevents the cumulative OOM the in-process
    binding can hit on large documents.

    `tag` is an optional suffix for the temp file names so concurrent /
    successive batches don't clobber each other's artifacts (helps when
    something goes wrong and you want to inspect them).
    """
    import subprocess
    suffix = f'_{tag}' if tag else ''
    src_path = os.path.join(work_dir, f'_measurement{suffix}.typ')
    pdf_path = os.path.join(work_dir, f'_measurement{suffix}.pdf')
    with open(src_path, 'w') as f:
        f.write(source)
    subprocess.run(
        ['typst', 'compile', src_path, pdf_path],
        check=True,
    )
    from pypdf import PdfReader
    r = PdfReader(pdf_path)
    all_text = ''.join((p.extract_text() or '') + '\n' for p in r.pages)
    heights = {}
    for m in re.finditer(r'MEAS\s+(\d+)\s*=\s*([\d.]+)\s*mm', all_text):
        heights[int(m.group(1))] = float(m.group(2))
    return heights


def annotate_heights(tree: Node, heights: dict) -> int:
    count = 0
    def walk(n):
        nonlocal count
        if n.id in heights:
            n.height_mm = heights[n.id]
            count += 1
        for c in n.children:
            walk(c)
    walk(tree)
    return count


def annotate_prefix_heights(tree: Node, heights: dict) -> int:
    populated = 0
    def walk(n):
        nonlocal populated
        if n.kind == 'container' and n.children:
            N = len(n.children)
            ph = [0.0] * (N + 1)
            ph[N] = n.height_mm
            any_p = False
            for k in range(1, N):
                pid = _encode_prefix_id(n.id, k)
                if pid in heights:
                    ph[k] = heights[pid]
                    any_p = True
            if N >= 1 and ph[1] == 0 and n.children[0].height_mm > 0:
                ph[1] = n.children[0].height_mm
                any_p = True
            if any_p:
                n.prefix_heights = ph
                populated += 1
        for c in n.children:
            walk(c)
    walk(tree)
    return populated


# ============================================================
# Packing + emission
# ============================================================

def pack_children(node: Node, page_height_mm: float) -> List[List[Node]]:
    """Greedy partition of children into page-sized groups, with sticky
    post-pass: a group may not END with a sticky leaf — pull it forward."""
    use_prefix = bool(node.prefix_heights)
    children = node.children
    N = len(children)

    def subseq_h(start, end):
        if use_prefix:
            return node.prefix_heights[end] - node.prefix_heights[start]
        return sum(c.height_mm for c in children[start:end])

    groups = []
    group_start = 0
    i = 0
    while i < N:
        child = children[i]
        if child.height_mm > page_height_mm:
            if i > group_start:
                groups.append(children[group_start:i])
            groups.append([child])
            i += 1
            group_start = i
            continue
        if subseq_h(group_start, i + 1) <= page_height_mm:
            i += 1
        else:
            if i > group_start:
                groups.append(children[group_start:i])
                group_start = i
            else:
                groups.append([child])
                i += 1
                group_start = i
    if i > group_start:
        groups.append(children[group_start:i])

    # Sticky post-pass
    changed = True
    while changed:
        changed = False
        for gi in range(len(groups) - 1):
            g = groups[gi]
            if len(g) < 2:
                continue
            last = g[-1]
            if last.kind == 'leaf' and last.sticky:
                groups[gi] = g[:-1]
                groups[gi + 1] = [last] + groups[gi + 1]
                changed = True
                break
    return groups


def _first_enum_start(nodes: List[Node]) -> int:
    """Walk depth-first into the first leaves of `nodes` and return the
    enum_start of the first ordered-list-item leaf encountered (1-indexed).
    Returns 0 if no ordered-list item found before any non-list content.

    This is used to decide whether to prepend `#set enum(start: N)` to a
    block. We only override when the FIRST ordered-list item in the block
    has position > 1 (i.e. items 1..N-1 are in a previous block).
    """
    def first_leaf_chain(n):
        # Yield leaves in document order until we hit something that resets
        # context (we stop at first non-list-item leaf).
        if n.kind == 'leaf':
            yield n
            return
        for c in n.children:
            yield from first_leaf_chain(c)

    for n in nodes:
        for leaf in first_leaf_chain(n):
            if leaf.enum_start > 0:
                return leaf.enum_start
            # Stop scanning if we hit any non-list-item leaf with content
            # (it would mean the list, if any, starts later inside a context
            # that already has its own counter scope).
            s = leaf.source.strip()
            if s and not _is_ordered_list_item(leaf.source):
                # Skip empty / pure-whitespace leaves; otherwise stop
                if s:
                    return 0
        # Continue to next node only if we exhausted leaves without finding
        # ordered-list item or non-list-content. But once we returned 0 above
        # we're done.
    return 0


def _wrap_block(body: str, nodes_for_enum_start: List[Node]) -> str:
    """Wrap `body` in `block(breakable: false)[...]`, prepending
    `#set enum(start: N)` if the block's first ordered-list-item leaf has
    position > 1 (i.e. this block is a continuation of a previous list)."""
    n = _first_enum_start(nodes_for_enum_start) if nodes_for_enum_start else 0
    if n > 1:
        return f'#block(breakable: false)[\n#set enum(start: {n})\n{body}\n]\n\n'
    return f'#block(breakable: false)[\n{body}\n]\n\n'


def emit(node: Node, page_height_mm: float, prepend: List[Node] = None) -> str:
    prepend = prepend or []

    if node.kind == 'leaf':
        if prepend:
            prepend_src = ''.join(n.source for n in prepend)
            if node.height_mm <= page_height_mm:
                return _wrap_block(prepend_src + node.source, prepend + [node])
            else:
                return prepend_src + node.source
        return node.source

    if node.id == -1:
        return ''.join(emit(c, page_height_mm) for c in node.children)

    if node.height_mm <= page_height_mm:
        body = ''.join(n.source for n in prepend) + render_node(node)
        return _wrap_block(body, prepend + [node])

    groups = pack_children(node, page_height_mm)
    parts = []
    pending_sticky = list(prepend)

    for gi, group in enumerate(groups):
        if len(group) == 1:
            child = group[0]
            if child.height_mm > page_height_mm:
                parts.append(emit(child, page_height_mm, prepend=pending_sticky))
                pending_sticky = []
            else:
                is_only_sticky = (child.kind == 'leaf' and child.sticky)
                next_oversized = False
                if gi + 1 < len(groups):
                    ng = groups[gi + 1]
                    if len(ng) == 1 and ng[0].height_mm > page_height_mm:
                        next_oversized = True
                if is_only_sticky and next_oversized:
                    pending_sticky.append(child)
                else:
                    body = ''.join(n.source for n in pending_sticky) + render_node(child)
                    parts.append(_wrap_block(body, pending_sticky + [child]))
                    pending_sticky = []
        else:
            body = ''.join(n.source for n in pending_sticky)
            body += ''.join(render_node(c) for c in group)
            parts.append(_wrap_block(body, pending_sticky + list(group)))
            pending_sticky = []

    if pending_sticky:
        body = ''.join(n.source for n in pending_sticky)
        parts.append(_wrap_block(body, list(pending_sticky)))

    return ''.join(parts)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--template', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--page-height', type=float, default=253)
    parser.add_argument('--page-width', type=float, default=166)
    parser.add_argument('--work-dir', default='/home/claude')
    parser.add_argument('--verify-roundtrip', action='store_true')
    parser.add_argument('--pass2-batch', type=int, default=8,
                        help='Oversized containers per pass-2 measurement '
                             'batch. Lower = less memory per typst compile, '
                             'more compiles. Default 8.')
    args = parser.parse_args()

    with open(args.source) as f:
        source = f.read()
    with open(args.template) as f:
        template = f.read()

    print('Tokenizing...', file=sys.stderr)
    tokens = tokenize(source)
    n_h = sum(1 for t in tokens if t['kind'] == 'heading')
    n_l = sum(1 for t in tokens if t['kind'] == 'leaf')
    print(f'  {len(tokens)} tokens ({n_h} headings, {n_l} leaves)', file=sys.stderr)

    print('Building tree...', file=sys.stderr)
    tree = build_tree(tokens)
    def tc(n):
        return 1 + sum(tc(c) for c in n.children)
    print(f'  {tc(tree)} tree nodes', file=sys.stderr)

    n_sticky = classify_sticky(tree)
    print(f'  {n_sticky} sticky leaves', file=sys.stderr)

    n_enum = classify_enum_positions(tree)
    print(f'  {n_enum} ordered-list-item leaves', file=sys.stderr)

    if args.verify_roundtrip:
        rendered = render_node(tree)
        if rendered != source:
            print('ROUND-TRIP FAILED', file=sys.stderr)
            for i, (a, b) in enumerate(zip(rendered, source)):
                if a != b:
                    print(f'  diff at char {i}: expected {repr(source[i:i+80])} got {repr(rendered[i:i+80])}', file=sys.stderr)
                    break
            sys.exit(1)
        print('Round-trip OK', file=sys.stderr)
        return

    print('Measuring (pass 1: whole heights)...', file=sys.stderr)
    meas_source = emit_measurement_source(tree, template, args.page_width)
    heights1 = compile_measurement(meas_source, args.work_dir, tag='pass1')
    count = annotate_heights(tree, heights1)
    print(f'  pass 1: {count} / {tc(tree)} nodes measured', file=sys.stderr)

    oversized = set()
    def find_oversized(n):
        if n.kind == 'container' and n.id != -1 and len(n.children) > 1:
            if n.height_mm > args.page_height:
                oversized.add(n.id)
        for c in n.children:
            find_oversized(c)
    find_oversized(tree)

    heights = dict(heights1)
    heights = heights1
    if oversized:
        print(f'  {len(oversized)} containers need split', file=sys.stderr)
        print('Measuring (pass 2: prefix heights, batched)...', file=sys.stderr)
        # Batch oversized containers so each measurement file stays small
        # enough to compile within typst's memory bounds. The full pass-2
        # source for ~50 oversized containers can balloon to >7 MB and
        # OOM the typst process; ~8 per batch keeps each file tractable.
        oversized_list = sorted(oversized)
        batch_size = args.pass2_batch
        heights2_total = {}
        for bi in range(0, len(oversized_list), batch_size):
            batch = set(oversized_list[bi:bi + batch_size])
            meas2 = emit_measurement_source(tree, template, args.page_width,
                                            include_prefix_for=batch)
            heights2 = compile_measurement(meas2, args.work_dir,
                                           tag=f'pass2_b{bi // batch_size:02d}')
            heights2_total.update(heights2)
            print(f'  pass 2 batch {bi // batch_size + 1}: '
                  f'{len(batch)} containers, {len(heights2)} probes',
                  file=sys.stderr)
        heights = {**heights1, **heights2_total}
        n_pref = annotate_prefix_heights(tree, heights)
        print(f'  pass 2 total: {len(heights2_total)} probes, '
              f'{n_pref} containers', file=sys.stderr)

    print('Packing & emitting...', file=sys.stderr)
    output = emit(tree, args.page_height)
    with open(args.output, 'w') as f:
        f.write(output)
    print(f'Wrote {args.output} ({len(output)} chars)', file=sys.stderr)


if __name__ == '__main__':
    main()
