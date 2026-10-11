"""HTML -> Markdown conversion and section extraction (docs/02_design.md §5.3).

Structure of dev.mysql.com pages (confirmed in M0):

* The content is in ``#docs-body``; breadcrumbs, version switcher and footer are outside it.
* Section headings carry their anchor: ``<h4 class="title"><a name="create-table-name">``.
  Sub-headings are not always wrapped in their own ``div.section`` (``create-table.html``
  has flat ``h4`` siblings), so a heading's section is cut in document order: from the
  heading to the next heading of the same or a higher level.
* Reference entries (system variables, options, ...) are ``<li class="listitem">`` items
  that start with ``<a name="sysvar_...">`` followed by a property table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import markdownify
import soupsieve
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_ADMONITIONS = ("note", "important", "warning", "caution", "tip")
# Anchor prefixes of named reference entries (see docs/02_design.md §3.6).
REFERENCE_ANCHOR_PREFIXES = (
    "sysvar_",
    "statvar_",
    "option_",
    "function_",
    "operator_",
    "priv_",
    "optflag_",
)
# Private-use characters that survive markdownify and never occur in the manual.
_MARK_OPEN, _MARK_CLOSE = "", ""
_MARKER_RE = re.compile(f"{_MARK_OPEN}(\\d+){_MARK_CLOSE}")
_EMPTY_TABLE_HEADER_RE = re.compile(r"^([ \t]*)\|\s+\|\s+\|\n[ \t]*\| --- \| --- \|", re.MULTILINE)
_SECTION_NUMBER_RE = re.compile(r"^(?:[A-Z]|\d+)(?:\.\d+)*\s+")


class UnreadablePageError(ValueError):
    pass


@dataclass(frozen=True)
class Heading:
    index: int
    level: int
    text: str  # as displayed, including the section number
    anchor: str | None

    @property
    def title(self) -> str:
        """Heading text without the leading section number."""
        return _SECTION_NUMBER_RE.sub("", self.text, count=1)


@dataclass(frozen=True)
class _MarkedMarkdown:
    text: str
    heading_offsets: dict[int, int]  # heading index -> offset of its line in ``text``


@dataclass
class ParsedPage:
    url: str
    manual: str  # e.g. "MySQL 8.4 Reference Manual"
    title: str  # e.g. "15.2.13 SELECT Statement"
    body: Tag
    headings: list[Heading] = field(default_factory=list)
    reference_anchors: list[str] = field(default_factory=list)
    # Converting a large page takes seconds; the result is shared by all reads of the page.
    _markdown: _MarkedMarkdown | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Section:
    requested: str
    title: str
    anchor: str | None
    content: str
    found: bool = True


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_page(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("#docs-body")
    if body is None or not body.get_text(strip=True):
        raise UnreadablePageError("the page has no documentation content (#docs-body)")

    manual, title = "", ""
    if soup.title and soup.title.string:
        # "MySQL :: MySQL 8.4 Reference Manual :: 15.2.13 SELECT Statement"
        parts = [p.strip() for p in _clean_text(soup.title.string).split("::")]
        if parts and parts[0] == "MySQL":
            parts = parts[1:]
        if parts:
            manual = parts[0]
            title = parts[-1] if len(parts) > 1 else ""

    for tag in body.select("script, style, a.indexterm"):
        tag.decompose()

    headings = []
    for i, h in enumerate(body.find_all(_HEADING_TAGS)):
        anchor_tag = h.select_one("a[name]") or h.select_one("[id]")
        anchor = str(anchor_tag.get("name") or anchor_tag.get("id")) if anchor_tag else None
        headings.append(
            Heading(index=i, level=int(h.name[1]), text=_clean_text(h.get_text(" ")), anchor=anchor)
        )
    if not title and headings:
        title = headings[0].text

    reference_anchors = [
        str(a["name"])
        for a in body.select("a[name]")
        if str(a["name"]).startswith(REFERENCE_ANCHOR_PREFIXES)
    ]
    return ParsedPage(
        url=url,
        manual=manual,
        title=title,
        body=body,
        headings=headings,
        reference_anchors=list(dict.fromkeys(reference_anchors)),
    )


# --------------------------------------------------------------------------- Markdown


def _code_language(el: Tag) -> str:
    for cls in el.get("class") or []:
        if cls.startswith("language-"):
            return cls[len("language-") :]
    return ""


class _Converter(markdownify.MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):  # type: ignore[override]
        # markdownify's default keeps the text but adds no language when the class is on <pre>.
        code = el.get_text()
        if not code.strip():
            return ""
        fence = "````" if "```" in code else "```"
        return f"\n\n{fence}{_code_language(el)}\n{code.strip(chr(10))}\n{fence}\n\n"


def _prepare(fragment: Tag, base_url: str) -> Tag:
    """Return a copy of ``fragment`` rewritten for Markdown output."""
    frag = BeautifulSoup(str(fragment), "lxml")
    root = frag.body.contents[0] if frag.body and frag.body.contents else frag
    assert isinstance(root, Tag)

    for a in root.find_all("a"):
        href = a.get("href")
        if href and not str(href).startswith(("mailto:", "javascript:")):
            a["href"] = urljoin(base_url, str(href))
            a.attrs.pop("title", None)
        elif not href and not a.get_text(strip=True):
            a.decompose()  # bare <a name="..."> anchors
    for img in root.find_all("img", src=True):
        img["src"] = urljoin(base_url, str(img["src"]))

    for kind in _ADMONITIONS:
        for box in root.select(f"div.{kind}"):
            title = box.select_one(".admon-title")
            label = _clean_text(title.get_text()) if title else kind.capitalize()
            if title:
                title.decompose()
            box.name = "blockquote"
            box.attrs = {}
            strong = frag.new_tag("strong")
            strong.string = f"{label}:"
            p = frag.new_tag("p")
            p.append(strong)
            box.insert(0, p)
    return root


def _to_markdown(fragment: Tag, base_url: str) -> str:
    md = _Converter(heading_style=markdownify.ATX, bullets="-", escape_misc=False).convert_soup(
        _prepare(fragment, base_url)
    )
    md = md.replace("\xa0", " ")
    # Property tables (Command-Line Format / Scope / Default Value ...) have no header row.
    md = _EMPTY_TABLE_HEADER_RE.sub(r"\1| Property | Value |\n\1| --- | --- |", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def _markdown_with_heading_offsets(page: ParsedPage) -> _MarkedMarkdown:
    if page._markdown is None:
        page._markdown = _convert_marked(page)
    return page._markdown


def _convert_marked(page: ParsedPage) -> _MarkedMarkdown:
    body = BeautifulSoup(str(page.body), "lxml").select_one("#docs-body")
    assert body is not None
    for i, h in enumerate(body.find_all(_HEADING_TAGS)):
        h.insert(0, NavigableString(f"{_MARK_OPEN}{i}{_MARK_CLOSE}"))
    marked = _to_markdown(body, page.url)

    offsets: dict[int, int] = {}
    out: list[str] = []
    pos = 0
    for line in marked.splitlines(keepends=True):
        match = _MARKER_RE.search(line)
        if match:
            offsets[int(match.group(1))] = pos
            line = re.sub(r"^(#+) +", r"\1 ", _MARKER_RE.sub("", line))
        out.append(line)
        pos += len(line)
    return _MarkedMarkdown("".join(out), offsets)


def page_to_markdown(page: ParsedPage) -> str:
    return _markdown_with_heading_offsets(page).text


def contents_summary(page: ParsedPage, max_reference_examples: int = 5) -> str:
    """A short table of contents to steer the agent toward ``read_sections``."""
    lines: list[str] = []
    if len(page.headings) > 1:
        top = min(h.level for h in page.headings)
        lines.append("Sections on this page (pass a title or #anchor to read_sections):")
        for h in page.headings:
            indent = "  " * (h.level - top)
            suffix = f" (#{h.anchor})" if h.anchor else ""
            lines.append(f"{indent}- {h.text}{suffix}")
    if page.reference_anchors:
        examples = ", ".join(page.reference_anchors[:max_reference_examples])
        more = "" if len(page.reference_anchors) <= max_reference_examples else ", ..."
        lines.append(
            f"Reference entries on this page: {len(page.reference_anchors)} "
            f"(anchors: {examples}{more}). Pass an anchor to read_sections to read one entry."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- sections


def _normalize(text: str) -> str:
    return _clean_text(text).casefold()


def _find_heading(page: ParsedPage, name: str) -> Heading | None:
    key = _normalize(name.lstrip("#"))
    for h in page.headings:
        if h.anchor and _anchor_key(h.anchor) == _anchor_key(key):
            return h
    for h in page.headings:
        if key in (_normalize(h.text), _normalize(h.title)):
            return h
    partial = [h for h in page.headings if key and key in _normalize(h.text)]
    return partial[0] if len(partial) == 1 else None


def _heading_section(page: ParsedPage, marked: _MarkedMarkdown, heading: Heading) -> str:
    start = marked.heading_offsets.get(heading.index)
    if start is None:
        return ""
    end = len(marked.text)
    for h in page.headings[heading.index + 1 :]:
        if h.level <= heading.level and h.index in marked.heading_offsets:
            end = marked.heading_offsets[h.index]
            break
    return marked.text[start:end].strip() + "\n"


def _anchor_key(anchor: str) -> str:
    return anchor.casefold().replace("-", "_")


def _find_anchor(page: ParsedPage, anchor: str) -> Tag | None:
    ident = soupsieve.escape(anchor)
    target = page.body.select_one(f"[name={ident}]") or page.body.select_one(f"[id={ident}]")
    if target is not None:
        return target
    # Agents often guess the separator or case: "function_json_extract" for the
    # manual's "function_json-extract".
    key = _anchor_key(anchor)
    for tag in page.body.select("[name]"):
        if _anchor_key(str(tag["name"])) == key:
            return tag
    return None


def _anchor_block(page: ParsedPage, target: Tag) -> Tag | None:
    item = target.find_parent("li")
    if item is not None:
        return item
    term = target.find_parent("dt")
    if term is not None:
        wrapper = BeautifulSoup("<div></div>", "lxml").div
        assert wrapper is not None
        wrapper.append(BeautifulSoup(str(term), "lxml").dt or "")
        definition = term.find_next_sibling("dd")
        if definition is not None:
            wrapper.append(BeautifulSoup(str(definition), "lxml").dd or "")
        return wrapper
    return target.find_parent(["p", "div", "table"])


def _item_to_markdown(block: Tag, base_url: str) -> str:
    if block.name == "li":
        wrapper = BeautifulSoup("<div></div>", "lxml").div
        assert wrapper is not None
        for child in list(BeautifulSoup(str(block), "lxml").li or []):
            wrapper.append(child)
        block = wrapper
    return _to_markdown(block, base_url)


def fragment_to_markdown(fragment: Tag, base_url: str) -> str:
    """Markdown of one element; a list item is converted without its bullet."""
    return _item_to_markdown(fragment, base_url)


def extract_sections(page: ParsedPage, names: list[str]) -> list[Section]:
    results: list[Section] = []
    for name in names:
        key = name.strip().lstrip("#")
        heading = _find_heading(page, key)
        if heading is not None:
            marked = _markdown_with_heading_offsets(page)
            content = _heading_section(page, marked, heading)
            results.append(Section(name, heading.text, heading.anchor, content))
            continue
        target = _find_anchor(page, key) if key else None
        block = _anchor_block(page, target) if target is not None else None
        if target is not None and block is not None:
            lead = block.find("p") or block
            title = _clean_text(lead.get_text())[:120] or key
            anchor = str(target.get("name") or target.get("id"))
            results.append(Section(name, title, anchor, _item_to_markdown(block, page.url)))
            continue
        results.append(Section(name, "", None, _not_found_message(page, key), found=False))
    return results


def _not_found_message(page: ParsedPage, key: str) -> str:
    lines = [f"Section {key!r} was not found on {page.url}."]
    candidates = [h for h in page.headings if key and _normalize(key) in _normalize(h.text)]
    if not candidates:
        lines.append("Available sections:")
        candidates = page.headings
    else:
        lines.append("Did you mean one of these?")
    lines += [f"- {h.text}" + (f" (#{h.anchor})" if h.anchor else "") for h in candidates[:50]]
    ref = [a for a in page.reference_anchors if _anchor_key(key) in _anchor_key(a)][:20]
    if ref:
        lines.append("Matching reference anchors: " + ", ".join(ref))
    return "\n".join(lines)
