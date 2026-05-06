#!/usr/bin/env python3
"""Post-process pandoc's Typst output: TOC label repair only.

Pandoc 3.1.3 emits TOC links like `#link(<1-executive-summary>)[...]` but
attaches labels `<executive-summary>` (without the section number prefix).
We rewrite the link targets to match. Fuzzy-match needed because some TOC
targets omit further tokens that the actual heading label includes.
"""
import re
import sys


HEADING_RE = re.compile(r'^(=+)\s', re.MULTILINE)
LABEL_LINE_RE = re.compile(r'^<([^>]+)>\s*$', re.MULTILINE)
TOC_LINK_RE = re.compile(r'#link\(<([^>]+)>\)')


def build_label_rewrite_map(text: str) -> dict:
    from difflib import SequenceMatcher

    actual_labels = set()
    for m in LABEL_LINE_RE.finditer(text):
        actual_labels.add(m.group(1))
    labels_list = list(actual_labels)

    rewrites = {}
    for m in TOC_LINK_RE.finditer(text):
        target = m.group(1)
        if target in actual_labels:
            continue
        stripped = re.sub(r'^\d+(-\d+)*-', '', target)
        if stripped in actual_labels:
            rewrites[target] = stripped
            continue
        starts_with = [lbl for lbl in labels_list if lbl.startswith(stripped)]
        pool = starts_with if starts_with else labels_list
        if not pool:
            continue
        best = max(pool, key=lambda lbl: SequenceMatcher(None, stripped, lbl).ratio())
        if SequenceMatcher(None, stripped, best).ratio() > 0.5:
            rewrites[target] = best
    return rewrites


def fix_toc_links(text: str) -> str:
    rewrites = build_label_rewrite_map(text)
    if not rewrites:
        return text

    def sub(m):
        target = m.group(1)
        return f'#link(<{rewrites.get(target, target)}>)'

    return TOC_LINK_RE.sub(sub, text)


def process(text: str) -> str:
    return fix_toc_links(text)


if __name__ == "__main__":
    src = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    sys.stdout.write(process(src))
