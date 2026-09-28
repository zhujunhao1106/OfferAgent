"""HTML tree + text extraction mirroring Go webcrawler/html.go (stdlib only)."""
from __future__ import annotations

from html.parser import HTMLParser

from .types import NoContentError

IGNORED_ELEMENTS = {"script", "style", "noscript", "svg", "nav", "header", "footer"}

BLOCK_ELEMENTS = {
    "address", "article", "aside", "blockquote", "br", "div", "h1", "h2", "h3",
    "h4", "h5", "h6", "li", "main", "p", "section", "table", "tr",
}

VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Text:
    __slots__ = ("data", "parent")

    def __init__(self, data: str, parent: "Element"):
        self.data = data
        self.parent = parent


class Element:
    __slots__ = ("tag", "attrs", "parent", "children")

    def __init__(self, tag: str, attrs: dict, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list = []


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("__root__", {}, None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag.lower(), dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in VOID_ELEMENTS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index + 1:]
                break

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(Text(data, self.stack[-1]))

    def handle_entityref(self, name):
        # convert_charrefs already handles entities; fallback for safety
        self.handle_data(f"&{name};")


def parse_html(content: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(content)
    builder.close()
    return builder.root


def extract_document(content_type: str, data: bytes) -> tuple[str, str]:
    lower_type = content_type.lower()
    if lower_type.startswith("text/plain"):
        text = normalize_extracted_text(data.decode("utf-8", "replace"))
        if len(text) < 40:
            raise NoContentError()
        return "", text
    root = parse_html(data.decode("utf-8", "replace"))
    title = [""]
    parts: list[str] = []
    _walk_html(root, False, title, parts)
    normalized = normalize_extracted_text("".join(parts))
    if len(normalized) < 40:
        raise NoContentError()
    return title[0].strip(), normalized


def _walk_html(node, ignored: bool, title: list, parts: list) -> None:
    if isinstance(node, Element):
        name = node.tag
        ignored = ignored or name in IGNORED_ELEMENTS
        if name == "title" and node.children and isinstance(node.children[0], Text):
            title[0] = normalize_inline_text(node.children[0].data)
        if not ignored and name in BLOCK_ELEMENTS:
            parts.append("\n")
    if isinstance(node, Text) and not ignored and _parent_element(node) != "title":
        value = normalize_inline_text(node.data)
        if value:
            parts.append(value)
            parts.append(" ")
    if isinstance(node, Element):
        for child in node.children:
            _walk_html(child, ignored, title, parts)
    if isinstance(node, Element) and not ignored and node.tag in BLOCK_ELEMENTS:
        parts.append("\n")


def _parent_element(node: Text) -> str:
    if isinstance(node.parent, Element):
        return node.parent.tag
    return ""


def normalize_inline_text(value: str) -> str:
    return " ".join(value.split())


def normalize_extracted_text(value: str) -> str:
    lines = value.replace("\r", "").split("\n")
    result: list[str] = []
    for line in lines:
        line = normalize_inline_text(line)
        if not line:
            if result and result[-1] != "":
                result.append("")
            continue
        result.append(line)
    return "\n".join(result).strip()


def walk_scripts(node, visit) -> None:
    if isinstance(node, Element) and node.tag == "script":
        visit(node)
    if isinstance(node, Element):
        for child in node.children:
            walk_scripts(child, visit)


def script_text(node: Element) -> str:
    return "".join(child.data for child in node.children if isinstance(child, Text))


def attribute(node: Element, key: str) -> str:
    for name, value in node.attrs.items():
        if name.lower() == key.lower():
            return value
    return ""


def walk_elements(node, visit) -> None:
    if isinstance(node, Element):
        visit(node)
        for child in node.children:
            walk_elements(child, visit)
