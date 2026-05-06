#!/usr/bin/env python3
"""Split analysis for a rendered Typst-compiled PDF.

For each structural level (heading L1-L4, paragraphs, sentences, lists,
list items, tables), count how many instances are contained on a single
page (whole) vs spanning multiple pages (split).

Approach: walk the pre-pack Typst source to enumerate units, then for
each unit search the PDF's per-page extracted text for prefix/suffix
matches to determine span.

Usage:
    python3 split_analysis.py <pre-pack-source.typ> <rendered.pdf>

Pass the PRE-pack source (e.g. source_v19_unwrapped.typ), NOT the
post-pack one — the analyzer needs to see raw paragraphs and lists,
not #block(breakable: false)[...] wrappers.
"""

import argparse
import re
import sys
from typing import List, Tuple, Optional

import pack


def normalize_text(s: str) -> str:
    """Collapse whitespace, normalize quotes/dashes/punctuation spacing for
    matching source signatures against PDF-extracted text."""
    s = re.sub(r'\s+', ' ', s)
    # Curly quotes → straight
    s = s.replace('\u201c', '"').replace('\u201d', '"')
    s = s.replace('\u2018', "'").replace('\u2019', "'")
    s = s.replace('\u00ab', '"').replace('\u00bb', '"')
    # Em/en dash → hyphen
    s = s.replace('\u2014', '-').replace('\u2013', '-')
    # Non-breaking space
    s = s.replace('\u00a0', ' ')
    # PDF line-wrap hyphen artifact: "health- adjacent" → "health-adjacent"
    s = re.sub(r'(\w)- (\w)', r'\1-\2', s)
    s = re.sub(r'-+', '-', s)
    # PDF inserts whitespace adjacent to punctuation at line wraps
    s = re.sub(r'\s+([\)\],:;.])', r'\1', s)
    s = re.sub(r'([\(\[])\s+', r'\1', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def strip_typst_markup(s: str) -> str:
    """Strip Typst markup primitives so source text matches PDF text."""
    s = re.sub(r'#smart-table\([^)]*\)', '', s, flags=re.DOTALL)
    s = re.sub(r'#link\(<[^>]+>\)\[([^\]]*)\]', r'\1', s)
    s = re.sub(r'#strong\[((?:[^\[\]]|\[[^\]]*\])*)\]', r'\1', s)
    s = re.sub(r'#emph\[((?:[^\[\]]|\[[^\]]*\])*)\]', r'\1', s)
    s = re.sub(r'#raw\[((?:[^\[\]]|\[[^\]]*\])*)\]', r'\1', s)
    s = re.sub(r'`([^`]*)`', r'\1', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'#\w+', '', s)
    return s


def find_page_span(text: str, page_texts: List[str], after_page: int = 0,
                   pre_norm: bool = False) -> Optional[Tuple[int, int]]:
    """Locate `text` (or its prefix..suffix span) in the rendered PDF.

    Strategy: take a prefix from the start of `text` and a suffix from the
    end. Find prefix_page (first page at/after `after_page` containing the
    prefix). Find suffix_page (first page at/after prefix_page containing
    the suffix). Return (prefix_page, suffix_page). If equal, the unit is
    contained on a single page.
    """
    if not text:
        return None
    norm = text if pre_norm else normalize_text(text)
    if len(norm) < 20:
        for i in range(after_page, len(page_texts)):
            pt = page_texts[i] if pre_norm else normalize_text(page_texts[i])
            if norm in pt:
                return (i + 1, i + 1)
        return None

    if len(norm) <= 80:
        prefix = norm
        suffix = norm
    else:
        plen = max(40, len(norm) // 4)
        plen = min(plen, 80)
        prefix = norm[:plen]
        suffix = norm[-plen:]

    prefix_page = None
    for i in range(after_page, len(page_texts)):
        pt = page_texts[i] if pre_norm else normalize_text(page_texts[i])
        if prefix in pt:
            prefix_page = i + 1
            break
    if prefix_page is None:
        return None

    suffix_page = None
    for i in range(prefix_page - 1, len(page_texts)):
        pt = page_texts[i] if pre_norm else normalize_text(page_texts[i])
        if suffix in pt:
            suffix_page = i + 1
            break
    if suffix_page is None:
        return (prefix_page, prefix_page)
    return (prefix_page, suffix_page)


# ============================================================
# Per-level enumeration
# ============================================================

def enumerate_headings(source: str) -> List[Tuple[int, str]]:
    out = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'^(=+)\s+(.*)$', line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            k = i + 1
            while k < len(lines):
                nxt = lines[k]
                if not nxt.strip():
                    break
                if nxt.startswith('<') and nxt.strip().endswith('>'):
                    break
                if re.match(r'^=+\s', nxt):
                    break
                if nxt.startswith('#'):
                    break
                text += ' ' + nxt.strip()
                k += 1
            out.append((level, text))
            i = k
        else:
            i += 1
    return out


def enumerate_tables(source: str) -> List[str]:
    """Each `#smart-table(...)` call. Returns a signature from the first
    data cell (distinctive enough to find in the PDF)."""
    out = []
    for mt in re.finditer(r'#smart-table\(', source):
        start = mt.end()
        m = re.match(r'\s*columns:\s*\d+,\s*', source[start:])
        if not m:
            continue
        pos = start + m.end()
        def skip_tuple(pos):
            if pos >= len(source) or source[pos] != '(':
                return None
            depth = 1
            pos += 1
            while pos < len(source) and depth > 0:
                c = source[pos]
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                pos += 1
            while pos < len(source) and source[pos] in ', \n':
                pos += 1
            return pos
        after_header = skip_tuple(pos)
        if after_header is None:
            continue
        if after_header >= len(source) or source[after_header] != '(':
            continue
        row_start = after_header + 1
        depth = 1
        j = row_start
        while j < len(source) and depth > 0:
            if source[j] == '(':
                depth += 1
            elif source[j] == ')':
                depth -= 1
            j += 1
        row = source[row_start:j - 1]
        cell_m = re.search(r'\[([^\[\]]*(?:\[[^\]]*\][^\[\]]*)*)\]', row)
        if cell_m:
            cell = cell_m.group(1)
            sig = normalize_text(strip_typst_markup(cell))
            if len(sig) >= 15:
                out.append(sig[:80])
    return out


def enumerate_paragraphs_and_sentences_full(source: str) -> Tuple[List[str], List[str]]:
    """Returns FULL normalized text per paragraph and per sentence."""
    paragraphs = []
    sentences = []
    tokens = pack.tokenize(source)
    for tok in tokens:
        if tok['kind'] != 'leaf':
            continue
        src = tok['source'].strip()
        if not src:
            continue
        first = src[:30]
        if first.startswith((
            '-', '+', '`', '=', '<',
            '#smart-table', '#figure', '#horizontalrule',
            '#image', '#quote', '#raw', '#align',
            '#keep-together', '#block(', '```',
        )):
            continue
        if re.match(r'^\s*#strong\[[^\]]*\]\s*$', src):
            continue
        plain = normalize_text(strip_typst_markup(src))
        if not re.search(r'[A-Za-z]{3,}', plain):
            continue
        if len(plain) < 10:
            continue
        paragraphs.append(plain)
        sentence_re = re.compile(r'[^.!?]+[.!?](?=\s+[A-Z"]|\s*$)')
        for sm in sentence_re.finditer(plain):
            sent = sm.group(0).strip()
            if len(sent) >= 20:
                sentences.append(sent)
    return paragraphs, sentences


def enumerate_lists_and_items_full(source: str) -> Tuple[List[dict], List[str]]:
    """Returns full normalized text per list item and per list."""
    lists = []
    items = []
    tokens = pack.tokenize(source)
    current_list = []
    for tok in tokens:
        if tok['kind'] != 'leaf':
            if current_list:
                lists.append({'first': current_list[0], 'last': current_list[-1]})
                current_list = []
            continue
        src = tok['source'].strip()
        if not re.match(r'^\s*[-+]\s', src):
            if current_list:
                lists.append({'first': current_list[0], 'last': current_list[-1]})
                current_list = []
            continue
        item_blocks = re.split(r'^(?=[-+]\s)', src, flags=re.MULTILINE)
        for ib in item_blocks:
            ib = ib.strip()
            if not ib or not re.match(r'^[-+]\s', ib):
                continue
            ib_text = re.sub(r'^[-+]\s+', '', ib)
            plain = normalize_text(strip_typst_markup(ib_text))
            if len(plain) < 10:
                continue
            items.append(plain)
            current_list.append(plain)
    if current_list:
        lists.append({'first': current_list[0], 'last': current_list[-1]})
    return lists, items


# ============================================================
# Main analysis
# ============================================================

def pct(n, total):
    if total == 0:
        return '0.0%'
    return f'{n/total*100:.1f}%'


def analyze(source_path: str, pdf_path: str):
    with open(source_path) as f:
        source = f.read()

    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    page_texts = [normalize_text(p.extract_text() or '') for p in reader.pages]

    def loc(text, after=0):
        return find_page_span(text, page_texts, after_page=after, pre_norm=True)

    print('=' * 70)
    print('HEADINGS')
    print('=' * 70)
    headings = enumerate_headings(source)
    by_level = {}
    for lvl, text in headings:
        by_level.setdefault(lvl, []).append(text)
    heading_pages = []
    last_p = 0
    for lvl, text in headings:
        clean = normalize_text(strip_typst_markup(text))
        span = loc(clean, after=max(0, last_p - 1))
        if span:
            heading_pages.append(span[0])
            last_p = span[0]
        else:
            heading_pages.append(None)
    section_spans = []
    for i, (lvl, text) in enumerate(headings):
        start = heading_pages[i]
        if start is None:
            section_spans.append(None)
            continue
        end = len(page_texts)
        for j in range(i + 1, len(headings)):
            if headings[j][0] <= lvl and heading_pages[j] is not None:
                end = heading_pages[j] - 1
                break
        section_spans.append((start, max(start, end)))
    for lvl in sorted(by_level):
        cons = [section_spans[i] for i in range(len(headings)) if headings[i][0] == lvl]
        resolved = [s for s in cons if s is not None]
        whole = sum(1 for s in resolved if s[0] == s[1])
        split = sum(1 for s in resolved if s[0] != s[1])
        unmatched = len(cons) - len(resolved)
        line = (f'L{lvl} ({"=" * lvl}): {len(cons)} total, '
                f'{whole} whole ({pct(whole, len(cons))}), '
                f'{split} split ({pct(split, len(cons))})')
        if unmatched:
            line += f', {unmatched} unmatched'
        print(line)

    print()
    print('=' * 70)
    print('TABLES')
    print('=' * 70)
    table_sigs = enumerate_tables(source)
    t_whole = t_split = t_notfound = 0
    last_p = 0
    for sig in table_sigs:
        span = loc(sig, after=max(0, last_p - 1))
        if span is None:
            t_notfound += 1
            continue
        last_p = span[0]
        if span[0] == span[1]:
            t_whole += 1
        else:
            t_split += 1
    total = t_whole + t_split + t_notfound
    print(f'Tables: {total} total, {t_whole} whole ({pct(t_whole, total)}), '
          f'{t_split} split ({pct(t_split, total)}), {t_notfound} unmatched')

    print()
    print('=' * 70)
    print('PARAGRAPHS & SENTENCES')
    print('=' * 70)
    paragraphs, sentences = enumerate_paragraphs_and_sentences_full(source)
    for label, items in [('Paragraphs', paragraphs), ('Sentences', sentences)]:
        whole = split = unmatched = 0
        last_p = 0
        for full in items:
            span = loc(full, after=max(0, last_p - 1))
            if span is None:
                unmatched += 1
                continue
            last_p = span[0]
            if span[0] == span[1]:
                whole += 1
            else:
                split += 1
        total = whole + split + unmatched
        print(f'{label}: {total} total, {whole} whole ({pct(whole, total)}), '
              f'{split} split ({pct(split, total)}), {unmatched} unmatched')

    print()
    print('=' * 70)
    print('LISTS & LIST ITEMS')
    print('=' * 70)
    lists, items = enumerate_lists_and_items_full(source)
    l_whole = l_split = l_unmatched = 0
    last_p = 0
    for lst in lists:
        span1 = loc(lst['first'], after=max(0, last_p - 1))
        if span1 is None:
            l_unmatched += 1
            continue
        last_p = span1[0]
        span2 = loc(lst['last'], after=max(0, span1[0] - 1))
        if span2 is None:
            l_unmatched += 1
            continue
        if span1[0] == span2[1]:
            l_whole += 1
        else:
            l_split += 1
    total = l_whole + l_split + l_unmatched
    print(f'Lists: {total} total, {l_whole} whole ({pct(l_whole, total)}), '
          f'{l_split} split ({pct(l_split, total)}), {l_unmatched} unmatched')
    i_whole = i_split = i_unmatched = 0
    last_p = 0
    for full in items:
        span = loc(full, after=max(0, last_p - 1))
        if span is None:
            i_unmatched += 1
            continue
        last_p = span[0]
        if span[0] == span[1]:
            i_whole += 1
        else:
            i_split += 1
    total = i_whole + i_split + i_unmatched
    print(f'List items: {total} total, {i_whole} whole ({pct(i_whole, total)}), '
          f'{i_split} split ({pct(i_split, total)}), {i_unmatched} unmatched')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', help='Pre-pack Typst source (e.g. source_v19_unwrapped.typ)')
    parser.add_argument('pdf', help='Rendered PDF')
    args = parser.parse_args()
    analyze(args.source, args.pdf)


if __name__ == '__main__':
    main()
