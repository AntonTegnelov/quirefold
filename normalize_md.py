#!/usr/bin/env python3
"""Normalize markdown that's missing blank lines between block elements.

Pandoc requires blank lines to separate paragraphs, headings, lists, tables,
code fences, blockquotes, and horizontal rules. This script inserts them where
needed without touching content.
"""
import re
import sys


def classify(line: str) -> str:
    """Classify a single line by its block type."""
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("#"):
        return "heading"
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return "hr"
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return "fence"
    if stripped.startswith("|"):
        return "table"
    if stripped.startswith(">"):
        return "blockquote"
    # List markers: -, *, + followed by space; or digit(s) followed by . and space
    if re.match(r"^[-*+]\s", stripped) or re.match(r"^\d+\.\s", stripped):
        return "list"
    return "prose"


def needs_blank_between(prev: str, curr: str) -> bool:
    """Return True if a blank line should be inserted between prev and curr."""
    if prev == "blank" or curr == "blank":
        return False
    # Heading always needs a blank before and after
    if curr == "heading" or prev == "heading":
        return True
    # HR needs isolation
    if curr == "hr" or prev == "hr":
        return True
    # Fence transitions (entry/exit handled at a higher level; this is belt+braces)
    if curr == "fence" or prev == "fence":
        return True
    # Block type changes
    if prev != curr:
        # Going into or out of list/table/blockquote needs a blank
        if "list" in (prev, curr):
            return True
        if "table" in (prev, curr):
            return True
        if "blockquote" in (prev, curr):
            return True
    # Two consecutive prose lines = separate paragraphs in this source
    if prev == "prose" and curr == "prose":
        return True
    return False


def normalize(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    prev_type = "blank"

    for line in lines:
        curr_type = classify(line)

        # Track fenced code block state — inside a fence, never insert anything
        if curr_type == "fence":
            if not in_fence:
                # Opening fence: ensure blank before it
                if out and out[-1].strip() != "":
                    out.append("")
                out.append(line)
                in_fence = True
                prev_type = "fence"
            else:
                # Closing fence
                out.append(line)
                in_fence = False
                prev_type = "fence_close"
                # Ensure blank after closing fence for following content
                # (handled by the next iteration's check)
            continue

        if in_fence:
            # Inside code block: pass through untouched
            out.append(line)
            continue

        # After closing a fence, force a blank before any non-blank content
        if prev_type == "fence_close" and curr_type != "blank":
            out.append("")
            prev_type = "blank"

        if needs_blank_between(prev_type, curr_type):
            if out and out[-1].strip() != "":
                out.append("")

        out.append(line)
        prev_type = curr_type

    result = "\n".join(out)
    if not result.endswith("\n"):
        result += "\n"
    return result


if __name__ == "__main__":
    src = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    sys.stdout.write(normalize(src))
