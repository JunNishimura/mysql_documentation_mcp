"""Parser for the MySQL manual in GNU Info format (see docs/how-it-works.md).

``https://downloads.mysql.com/docs/mysql-{version}.info.zip`` holds the whole reference
manual in one file (~20 MB):

* Nodes are separated by ``\\x1f`` and start with
  ``File: manual.info.tmp,  Node: select,  Next: ...,  Prev: ...,  Up: parent``.
  The node name equals the HTML page name (``select`` -> ``select.html``; ``Top`` is the
  manual's top page).
* The tag table at the end lists ``Node: name\\x7f<byte offset>`` and
  ``Ref: anchor\\x7f<byte offset>``. Offsets are byte positions in the file, so every
  anchor (``sysvar_sql_mode``, ``create-table-options``) can be placed inside its node.
* The general index sits at the end of the last node after a ``\\0\\b[index\\0\\b]`` marker,
  with entries like ``* sql_mode system variable:   server-system-variables.``.

Info text is only used for search: placeholders are upper-cased (``SELECT_EXPR`` for
``select_expr``), so content shown to agents always comes from the HTML pages.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

_NODE_HEADER_RE = re.compile(rb"^File: [^,]*,\s+Node: ([^,\n]+)(?:,[^\n]*?Up: ([^,\n]+))?", re.M)
_TAG_RE = re.compile(rb"^(Node|Ref): (.*?)\x7f(\d+)$", re.M)
_INDEX_MARKER = b"\x00\x08[index\x00\x08]"
_INDEX_ENTRY_RE = re.compile(r"^\* (?P<term>.+?):\s+(?P<node>[A-Za-z0-9._-]+)\.\s*$", re.M)
_NOTE_RE = re.compile(r"\*[Nn]ote\s+([^:]+?)::", re.S)  # *note select::
_NOTE_LABEL_RE = re.compile(r"\*[Nn]ote\s+([^:]+?):\s*[A-Za-z0-9._-]+\.", re.S)
# "* Menu:", a blank line, then "* node::  Description" entries whose wrapped lines follow
# directly. Content after the menu (e.g. a syntax block) comes after a blank line.
_MENU_RE = re.compile(r"^\* Menu:\n\n?(?:\* [^\n]*\n(?:[ \t]+\S[^\n]*\n)*)*", re.M)
_UNDERLINE_RE = re.compile(r"^[*=\-.]{3,}$")
_SECTION_NUMBER_RE = re.compile(r"^((?:[A-Z]|\d+)(?:\.\d+)*)\s+")


# The 8.4 manual is about 20 MB uncompressed.
MAX_INFO_SIZE = 200 * 1024 * 1024


class InfoFormatError(ValueError):
    pass


@dataclass
class Chunk:
    anchor: str | None  # None for the text before the first anchor of the node
    heading: str
    text: str


@dataclass
class Node:
    name: str
    up: str | None
    title: str
    section_number: str
    order: int
    chunks: list[Chunk] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)

    @property
    def page(self) -> str:
        return "" if self.name == "Top" else f"{self.name}.html"


def read_info_archive(data: bytes, max_size: int = MAX_INFO_SIZE) -> bytes:
    """Return the ``.info`` file inside ``mysql-{version}.info.zip``.

    The uncompressed file is about 20 MB; anything larger than ``max_size`` is refused
    without being decompressed in full (protection against a "zip bomb").
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = [i for i in archive.infolist() if i.filename.endswith(".info")]
            if not infos:
                raise InfoFormatError("the archive contains no .info file")
            if infos[0].file_size > max_size:
                raise InfoFormatError(f"the .info file is larger than {max_size} bytes")
            with archive.open(infos[0]) as member:
                content = member.read(max_size + 1)
            if len(content) > max_size:
                raise InfoFormatError(f"the .info file is larger than {max_size} bytes")
            return content
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        raise InfoFormatError(f"not a usable zip archive: {e}") from e


def _clean(text: str) -> str:
    text = _MENU_RE.sub("", text)
    text = _NOTE_LABEL_RE.sub(r"\1", text)
    text = _NOTE_RE.sub(r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _heading_of(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if not line or _UNDERLINE_RE.match(line):
            continue
        line = line.lstrip("* ").strip()
        return line.strip("*'\"").strip()[:120]
    return ""


def parse_info(data: bytes) -> list[Node]:
    tag_start = data.rfind(b"\x1f\nTag Table:")
    if tag_start < 0:
        raise InfoFormatError("no tag table")
    tags = [
        (kind, name.decode("utf-8", "replace"), int(off))
        for kind, name, off in _TAG_RE.findall(data[tag_start:])
    ]
    node_offsets = sorted((off, name) for kind, name, off in tags if kind == b"Node")
    if not node_offsets:
        raise InfoFormatError("no nodes in the tag table")
    refs = sorted((off, name) for kind, name, off in tags if kind == b"Ref")

    nodes: list[Node] = []
    by_name: dict[str, Node] = {}
    ref_i = 0
    for i, (start, name) in enumerate(node_offsets):
        end = node_offsets[i + 1][0] if i + 1 < len(node_offsets) else tag_start
        raw = data[start:end]
        header = _NODE_HEADER_RE.search(raw)
        if header is None or header.group(1).decode("utf-8", "replace").strip() != name:
            raise InfoFormatError(f"tag table offset of node {name!r} does not match the file")
        up = header.group(2).decode("utf-8", "replace").strip() if header.group(2) else None
        body_start = start + header.end()
        index_at = raw.find(_INDEX_MARKER)
        body_end = start + index_at if index_at >= 0 else end

        # Split the node body at each anchor inside it.
        while ref_i < len(refs) and refs[ref_i][0] < body_start:
            ref_i += 1
        cuts: list[tuple[int, str | None]] = [(body_start, None)]
        while ref_i < len(refs) and refs[ref_i][0] < end:
            off, anchor = refs[ref_i]
            if off < body_end:
                cuts.append((off, anchor))
            ref_i += 1

        body = data[body_start:body_end].decode("utf-8", "replace")
        title = _heading_of(body)
        number = _SECTION_NUMBER_RE.match(title)
        node = Node(
            name=name,
            up=up if up and up != "(dir)" else None,
            title=title,
            section_number=number.group(1) if number else "",
            order=i,
        )
        for j, (cut, anchor) in enumerate(cuts):
            stop = cuts[j + 1][0] if j + 1 < len(cuts) else body_end
            text = _clean(data[cut:stop].decode("utf-8", "replace"))
            if anchor is not None:
                node.anchors.append(anchor)
            if not text and anchor is None:
                continue
            node.chunks.append(Chunk(anchor=anchor, heading=_heading_of(text), text=text))
        nodes.append(node)
        by_name[name] = node

    index_at = data.find(_INDEX_MARKER)
    if index_at >= 0:
        index_text = data[index_at:tag_start].decode("utf-8", "replace")
        for m in _INDEX_ENTRY_RE.finditer(index_text):
            target = by_name.get(m.group("node"))
            if target is not None:
                term = re.sub(r"\s*<\d+>$", "", m.group("term")).strip()
                if term and term not in target.index_terms:
                    target.index_terms.append(term)
    return nodes
