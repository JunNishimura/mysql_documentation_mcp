"""Build small synthetic Info manuals for tests (no MySQL documentation text)."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence

_ANCHOR_RE = re.compile(r"@@([\w.-]+)@@")


def make_info(
    nodes: Sequence[tuple[str, str | None, str]], index: Sequence[tuple[str, str]] = ()
) -> bytes:
    """``nodes``: (name, up, body). ``@@anchor@@`` in a body marks an anchor position.

    ``index``: (term, node) entries appended after the index marker of the last node.
    """
    out = bytearray(b"This is manual.info, produced for tests.\n\n")
    tags: list[tuple[str, str, int]] = []
    for i, (name, up, body) in enumerate(nodes):
        tags.append(("Node", name, len(out)))
        header = f"\x1f\nFile: manual.info.tmp,  Node: {name}"
        if i + 1 < len(nodes):
            header += f",  Next: {nodes[i + 1][0]}"
        header += f",  Up: {up or '(dir)'}\n\n"
        out += header.encode()
        pos = 0
        for m in _ANCHOR_RE.finditer(body):
            out += body[pos : m.start()].encode()
            tags.append(("Ref", m.group(1), len(out)))
            pos = m.end()
        out += body[pos:].encode() + b"\n"
        if i + 1 == len(nodes) and index:
            out += b"\x00\x08[index\x00\x08]\n* Menu:\n\n"
            for term, node in index:
                out += f"* {term}:{' ' * 4}{node}.\n".encode()
    out += b"\x1f\nTag Table:\n"
    for kind, name, off in tags:
        out += f"{kind}: {name}\x7f{off}\n".encode()
    out += b"\x1f\nEnd Tag Table\n"
    return bytes(out)


def zip_info(data: bytes, name: str = "mysql-8.4.info") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, data)
    return buf.getvalue()


SAMPLE_NODES: list[tuple[str, str | None, str]] = [
    ("Top", None, "Widget Manual\n*************\n\n* Menu:\n\n* widgets::    Widgets\n"),
    (
        "widgets",
        "Top",
        "1 Widgets\n*********\n\nWidgets are fictional objects used in tests.\n",
    ),
    (
        "widget-statement",
        "widgets",
        "1.1 WIDGET Statement\n====================\n\n* Menu:\n\n"
        "* widget-into::    WIDGET ... INTO\n\n"
        "     WIDGET [FAST | SLOW] WIDGET_NAME\n\n"
        "The 'WIDGET' statement creates a widget; see *note widget-limits::.\n\n"
        "@@widget-statement-options@@*Widget Options*\n\n"
        "Options make the widget sprocket spin.\n\n"
        "   * @@sysvar_widget_limit@@'widget_limit'\n\n"
        "     Default Value\n\n     '42'\n\n"
        "     The maximum number of gizmos per widget.\n\n"
        "   * @@function_widget-count@@'WIDGET_COUNT(EXPR)'\n\n"
        "     Counts widgets in a frobnicated table.\n",
    ),
    (
        "widget-limits",
        "widgets",
        "1.2 Widget Limits\n=================\n\nLimits prevent sprocket overload.\n",
    ),
]
SAMPLE_INDEX = [
    ("widget_limit system variable", "widget-statement"),
    ("sprocket <1>", "widget-limits"),
]
