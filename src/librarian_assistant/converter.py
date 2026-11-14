"""Download a Halo Waypoint article and package it as an EPUB 3 book."""

from __future__ import annotations

import base64
import hashlib
import html
import mimetypes
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


USER_AGENT = "TheHaruspisAssistant/0.1 (+personal EPUB converter)"
ALLOWED_HOSTS = {"halowaypoint.com", "www.halowaypoint.com"}
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
CONTENT_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "figure", "figcaption", "img", "hr", "br", "strong", "b", "em", "i", "a", "span", "sup", "sub"}
DISCARDED_TAGS = {"script", "style", "noscript", "template", "iframe", "object"}


class ConversionError(RuntimeError):
    """A user-facing conversion failure."""


class ConversionCancelled(ConversionError):
    """The user cancelled an in-progress conversion."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        pieces: list[str] = []

        def visit(item: Node | str) -> None:
            if isinstance(item, str):
                pieces.append(item)
            elif item.tag == "br":
                pieces.append("\n")
            else:
                for child in item.children:
                    visit(child)

        visit(self)
        return re.sub(r"[ \t\r\f\v]+", " ", "".join(pieces)).strip()

    def descendants(self, tag: str | None = None) -> Iterable[Node]:
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if tag is None or child.tag == tag:
                yield child
            yield from child.descendants(tag)


class TreeParser(HTMLParser):
    """Small, forgiving HTML tree builder for server-rendered article HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(tag, {key.lower(): value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


@dataclass
class Article:
    title: str
    author: str
    category: str
    source_url: str
    hero_url: str | None
    hero_alt: str
    content: Node
    plot: str = ""


@dataclass(frozen=True)
class ImageAsset:
    source_url: str
    filename: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ConversionResult:
    output_path: Path
    title: str
    author: str
    image_count: int
    warnings: tuple[str, ...] = ()


def _find_by_class(root: Node, class_prefix: str) -> Node | None:
    for node in root.descendants():
        if any(name.startswith(class_prefix) for name in node.classes):
            return node
    return None


def _find_meta(root: Node, key: str, value: str) -> str:
    for node in root.descendants("meta"):
        if node.attrs.get(key) == value:
            return node.attrs.get("content", "").strip()
    return ""


def _image_source(node: Node | None) -> tuple[str, str]:
    """Return the best URL and alt text from an image or image wrapper."""

    if node is None:
        return "", ""
    image = node if node.tag == "img" else next(node.descendants("img"), None)
    if image is None:
        return "", ""
    source = image.attrs.get("src") or image.attrs.get("data-src", "")
    if not source:
        srcset = image.attrs.get("srcset") or image.attrs.get("data-srcset", "")
        if srcset:
            source = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
    return source, image.attrs.get("alt", "")


def _historian_note(content: Node) -> str:
    """Extract the opening Historian's Note for ebook description metadata."""

    children = [child for child in content.children if isinstance(child, Node)]
    for index, node in enumerate(children):
        heading = node.text().upper().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").rstrip(":")
        if heading not in {"HISTORIAN'S NOTE", "HISTORIANS NOTE"}:
            continue
        paragraphs: list[str] = []
        for candidate in children[index + 1:]:
            text = candidate.text().strip()
            if not text:
                if paragraphs:
                    break
                continue
            if candidate.tag in {"h1", "h2", "h3", "h4", "hr"}:
                break
            if text in {"*", "* * *", "\N{FOUR TEARDROP-SPOKED ASTERISK}"}:
                break
            paragraphs.append(text)
        return "\n\n".join(paragraphs)
    return ""


def _validate_source_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ConversionError("Please enter a public halowaypoint.com article URL.")
    if not parsed.path.startswith("/news/"):
        raise ConversionError("The URL must point to a Halo Waypoint news article.")
    return url.strip()


def _download(
    url: str,
    *,
    timeout: int = 45,
    max_bytes: int,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8"})
    try:
        with urlopen(request, timeout=timeout) as response:
            declared_size = response.headers.get("Content-Length")
            if declared_size and declared_size.isdigit() and int(declared_size) > max_bytes:
                raise ConversionError(f"Download is larger than the allowed {max_bytes // (1024 * 1024)} MB: {url}")
            chunks: list[bytes] = []
            total = 0
            while True:
                if cancelled and cancelled():
                    raise ConversionCancelled("Conversion cancelled.")
                chunk = response.read(min(64 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise ConversionError(f"Download is larger than the allowed {max_bytes // (1024 * 1024)} MB: {url}")
            return b"".join(chunks), response.headers.get_content_type().lower()
    except HTTPError as exc:
        raise ConversionError(f"Waypoint returned HTTP {exc.code} for {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ConversionError(f"Could not download {url}: {exc}") from exc


def parse_article(page: bytes, source_url: str) -> Article:
    try:
        source = page.decode("utf-8")
    except UnicodeDecodeError:
        source = page.decode("utf-8", errors="replace")
    parser = TreeParser()
    parser.feed(source)
    root = parser.root

    content = _find_by_class(root, "wordpress-content_content__")
    if content is None:
        raise ConversionError("The Halo Waypoint article body was not found. The site's page format may have changed.")

    title_node = _find_by_class(root, "article-page_title__")
    subtitle_node = _find_by_class(root, "article-page_subtitle__")
    category_node = _find_by_class(root, "article-page_category__")
    hero_node = _find_by_class(root, "article-page_articleImage__")

    title = title_node.text() if title_node else _find_meta(root, "property", "og:title")
    if not title:
        raise ConversionError("The article title was not found.")
    subtitle = subtitle_node.text() if subtitle_node else ""
    author = re.sub(r"^\s*By\s+", "", subtitle, flags=re.IGNORECASE).strip() or _find_meta(root, "name", "author") or "Halo Studios"
    category = category_node.text() if category_node else "Waypoint Chronicle"
    hero_source, hero_alt = _image_source(hero_node)
    hero_source = hero_source or _find_meta(root, "property", "og:image")
    hero_alt = hero_alt or _find_meta(root, "property", "og:image:alt") or "Cover artwork"
    hero_url = urljoin(source_url, hero_source) if hero_source else None
    return Article(title, author, category, source_url, hero_url, hero_alt, content, _historian_note(content))


def _safe_filename(title: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", title)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "Waypoint Chronicle") + ".epub"


def _guess_image_type(url: str, content_type: str, data: bytes) -> tuple[str, str]:
    signatures = (
        (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
        (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
        (b"GIF87a", ".gif", "image/gif"),
        (b"GIF89a", ".gif", "image/gif"),
        (b"RIFF", ".webp", "image/webp"),
        (b"<svg", ".svg", "image/svg+xml"),
        (b"<?xml", ".svg", "image/svg+xml"),
    )
    for signature, extension, media_type in signatures:
        if data.startswith(signature):
            if extension != ".webp" or data[8:12] == b"WEBP":
                return extension, media_type
    normalized = content_type.split(";", 1)[0].lower()
    extension = mimetypes.guess_extension(normalized) or Path(urlparse(url).path).suffix.lower()
    if extension == ".jpe":
        extension = ".jpg"
    if normalized.startswith("image/") and extension:
        return extension, normalized
    raise ConversionError(f"Downloaded artwork is not a recognized image: {url}")


def _collect_image_urls(article: Article) -> list[str]:
    urls: list[str] = []
    if article.hero_url:
        urls.append(article.hero_url)
    for node in article.content.descendants("img"):
        source = node.attrs.get("src") or node.attrs.get("data-src")
        if source:
            urls.append(urljoin(article.source_url, source))
    return list(dict.fromkeys(urls))


def _download_images(
    article: Article,
    progress: Callable[[str], None],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[ImageAsset], dict[str, str], list[str]]:
    assets: list[ImageAsset] = []
    replacements: dict[str, str] = {}
    warnings: list[str] = []
    for index, url in enumerate(_collect_image_urls(article), start=1):
        if cancelled and cancelled():
            raise ConversionCancelled("Conversion cancelled.")
        progress(f"Downloading artwork {index}...")
        try:
            data, content_type = _download(url, max_bytes=MAX_IMAGE_BYTES, cancelled=cancelled)
            extension, media_type = _guess_image_type(url, content_type, data)
        except ConversionCancelled:
            raise
        except ConversionError as exc:
            warning = f"Warning: skipped artwork {index}: {exc}"
            warnings.append(warning)
            progress(warning)
            continue
        digest = hashlib.sha256(data).hexdigest()[:10]
        filename = f"image-{index:02d}-{digest}{extension}"
        assets.append(ImageAsset(url, filename, media_type, data))
        replacements[url] = f"../Images/{filename}"
    return assets, replacements, warnings


def _xml(value: str) -> str:
    return html.escape(value, quote=True)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def _render_node(node: Node | str, article: Article, image_paths: dict[str, str]) -> str:
    if isinstance(node, str):
        return _xml(_normalized_text(node))
    if node.tag in DISCARDED_TAGS:
        return ""
    if node.tag not in CONTENT_TAGS:
        return "".join(_render_node(child, article, image_paths) for child in node.children)

    tag = {"b": "strong", "i": "em", "h1": "h2", "h5": "h4", "h6": "h4", "div": "div", "span": "span"}.get(node.tag, node.tag)
    body = "".join(_render_node(child, article, image_paths) for child in node.children)
    plain = node.text().strip()

    if node.tag == "img":
        source = node.attrs.get("src") or node.attrs.get("data-src", "")
        absolute = urljoin(article.source_url, source)
        local = image_paths.get(absolute)
        if not local:
            return ""
        return f'<img src="{_xml(local)}" alt="{_xml(node.attrs.get("alt", "Article artwork"))}"/>'
    if node.tag == "br":
        return "<br/>"
    if node.tag == "hr":
        return '<div class="scene-break" aria-label="Scene break">&#10022;</div>'
    if node.tag == "a":
        href = urljoin(article.source_url, node.attrs.get("href", ""))
        if not href.startswith(("http://", "https://", "mailto:")):
            return body
        return f'<a href="{_xml(href)}">{body}</a>'
    if node.tag in {"span", "div"} and not plain and "<img " not in body:
        if node.tag == "div" and node.parent is article.content:
            return '<div class="scene-break" aria-label="Scene break">&#10022;</div>'
        return body
    if tag == "div" and node.parent is article.content:
        if "<img " in body:
            return f'<figure class="article-image">{body}</figure>'
        return body

    css_class = ""
    normalized_plain = plain.upper().replace("’", "'").rstrip(":")
    if tag == "p" and normalized_plain in {"HISTORIAN'S NOTE", "HISTORIANS NOTE"}:
        css_class = ' class="note-heading"'
    elif tag == "p" and plain in {"*", "* * *", "✦", "•"}:
        return '<div class="scene-break" aria-label="Scene break">&#10022;</div>'
    elif tag in {"h2", "h3", "h4"}:
        css_class = ' class="section-heading"'
    elif tag == "blockquote":
        css_class = ' class="epigraph"'
    return f"<{tag}{css_class}>{body}</{tag}>"


def _render_story(article: Article, image_paths: dict[str, str]) -> str:
    rendered = "".join(_render_node(child, article, image_paths) for child in article.content.children)
    rendered = re.sub(r"(?:\s*<div class=\"scene-break\"[^>]*>&#10022;</div>\s*){2,}", '<div class="scene-break" aria-label="Scene break">&#10022;</div>', rendered)
    return rendered.strip()


WAYPOINT_CSS = """@charset "utf-8";
@page { margin: 5%; }
html { color: #202a2f; background: #f4f0e7; }
body { margin: 0 auto; padding: 0 5%; max-width: 38em; font-family: Georgia, "Times New Roman", serif; font-size: 1em; line-height: 1.58; }
p { margin: 0 0 0.85em; text-align: justify; orphans: 2; widows: 2; }
p + p { text-indent: 1.25em; }
.note-heading + p, .section-heading + p, .scene-break + p, blockquote + p { text-indent: 0; }
a { color: #176579; text-decoration: underline; }
.cover-body { margin: 0; padding: 0; max-width: none; background: #10191d; text-align: center; }
.cover { margin: 0; padding: 0; }
.cover img { display: block; width: 100%; height: auto; max-height: 100vh; object-fit: contain; }
.title-page { min-height: 80vh; padding-top: 18vh; text-align: center; }
.kicker { color: #337d8f; font: bold 0.72em/1.3 Arial, sans-serif; letter-spacing: 0.22em; text-transform: uppercase; }
h1 { margin: 0.35em 0 0.25em; color: #17252b; font: normal 2.5em/1.04 Georgia, serif; letter-spacing: 0.025em; text-transform: uppercase; }
.rule { width: 9em; margin: 1.4em auto; border-top: 2px solid #51a9bc; }
.byline { font-style: italic; }
.imprint { margin-top: 30vh; color: #526068; font: 0.72em/1.5 Arial, sans-serif; letter-spacing: 0.08em; text-transform: uppercase; }
.story-header { margin: 0 0 2.3em; padding: 2em 0 1em; border-bottom: 1px solid #65a9b7; text-align: center; }
.story-header .book-title { margin: 0; color: #31464e; font: 0.72em/1.2 Arial, sans-serif; letter-spacing: 0.18em; text-transform: uppercase; }
.note-heading, .section-heading { margin: 2.4em 0 0.8em; color: #263b43; font: bold 0.86em/1.35 Arial, sans-serif; letter-spacing: 0.12em; text-transform: uppercase; text-indent: 0; page-break-after: avoid; break-after: avoid; }
.note-heading { padding-bottom: 0.45em; border-bottom: 1px solid #86b9c3; }
.epigraph { margin: 1.6em 8%; color: #46545a; font-style: italic; }
.scene-break { margin: 1.65em auto; color: #3f8fa1; text-align: center; font-size: 0.8em; letter-spacing: 0.4em; text-indent: 0; }
.article-image { margin: 1.8em 0; text-align: center; page-break-inside: avoid; break-inside: avoid; }
figure img, .article-image img, body > img { max-width: 100%; height: auto; }
figcaption { margin-top: 0.4em; color: #59666c; font: italic 0.78em/1.35 Arial, sans-serif; text-align: center; }
.credits { font: 0.86em/1.55 Arial, sans-serif; }
.credits dt { margin-top: 1em; color: #2f7585; font-weight: bold; text-transform: uppercase; }
.credits dd { margin-left: 0; }
nav ol { padding-left: 1.4em; }
nav li { margin: 0.6em 0; }
"""

CLASSIC_CSS = WAYPOINT_CSS + """
html, .cover-body { color: #000000; background: #ffffff; }
body { color: #000000; background: #ffffff; }
a { color: inherit; text-decoration: underline; }
.kicker, h1, .imprint, .story-header .book-title,
.note-heading, .section-heading, .epigraph, .scene-break,
figcaption, .credits dt { color: inherit; }
.rule, .story-header, .note-heading { border-color: currentColor; }
"""

READING_STYLES = {"waypoint": WAYPOINT_CSS, "classic": CLASSIC_CSS}


def _stylesheet(reading_style: str) -> str:
    try:
        return READING_STYLES[reading_style]
    except KeyError as exc:
        raise ConversionError(f"Unknown reading style: {reading_style}") from exc


def _xhtml(title: str, body: str, body_class: str = "", body_style: str = "") -> str:
    class_attr = f' class="{body_class}"' if body_class else ""
    style_attr = f' style="{_xml(body_style)}"' if body_style else ""
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><meta charset="utf-8"/><title>{_xml(title)}</title><link rel="stylesheet" type="text/css" href="../Styles/book.css"/></head>
<body{class_attr}{style_attr}>{body}</body>
</html>'''


def _cover_title_lines(title: str, target_length: int = 18) -> list[str]:
    """Split a title into a few balanced lines for the fixed-layout cover."""

    display_title = re.sub(r"^Halo:\s*", "", title, flags=re.IGNORECASE).strip().upper()
    words: list[str] = []
    for word in display_title.split():
        if len(word) <= target_length:
            words.append(word)
        else:
            words.extend(word[index:index + target_length] for index in range(0, len(word), target_length))
    if not words:
        return ["WAYPOINT CHRONICLE"]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= target_length:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _cover_svg(article: Article, hero_asset: ImageAsset | None) -> str:
    cover_width = 1700
    cover_height = 2400
    title_x = cover_width // 2
    title_letter_spacing = 14
    rule_y = cover_height - 420
    subtitle_y = cover_height - 315
    lines = _cover_title_lines(article.title)
    longest_line = max(len(line) for line in lines)
    available_width = (cover_width - 320) - (max(longest_line - 1, 0) * title_letter_spacing)
    width_limited_size = int(available_width / max(longest_line * 0.62, 1))
    height_limited_size = int(560 / max(len(lines) * 1.18, 1))
    font_size = max(28, min(132, width_limited_size, height_limited_size))
    line_height = int(font_size * 1.18)
    title_start = (rule_y - 115) - ((len(lines) - 1) * line_height)
    title_spans = "".join(
        f'<tspan x="{title_x}" y="{title_start + index * line_height}">{_xml(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    if hero_asset:
        encoded_artwork = base64.b64encode(hero_asset.data).decode("ascii")
        artwork = (
            f'<rect width="{cover_width}" height="{cover_height}" fill="#10191d"/>'
            f'<image href="data:{_xml(hero_asset.media_type)};base64,{encoded_artwork}" '
            f'xlink:href="data:{_xml(hero_asset.media_type)};base64,{encoded_artwork}" '
            f'x="0" y="0" width="{cover_width}" height="{cover_height}" preserveAspectRatio="xMidYMid slice"/>'
        )
    else:
        artwork = f'<rect width="{cover_width}" height="{cover_height}" fill="#17262d"/><path d="M0 340 L{cover_width} 0 M0 760 L{cover_width} 420 M0 1180 L{cover_width} 840" stroke="#285865" stroke-width="3" opacity="0.55"/>'
    return f'''<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{cover_width}" height="{cover_height}" viewBox="0 0 {cover_width} {cover_height}" style="background:#10191d" role="img" aria-labelledby="cover-title cover-description">
<title id="cover-title">{_xml(article.title)}</title><desc id="cover-description">{_xml(article.hero_alt or "Halo Waypoint Chronicle cover")}</desc>
<defs><linearGradient id="shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000000" stop-opacity="0"/><stop offset="0.58" stop-color="#000000" stop-opacity="0"/><stop offset="0.72" stop-color="#000000" stop-opacity="0.42"/><stop offset="0.84" stop-color="#000000" stop-opacity="0.96"/><stop offset="1" stop-color="#000000" stop-opacity="1"/></linearGradient></defs>
{artwork}<rect x="0" y="0" width="{cover_width}" height="{cover_height}" fill="url(#shade)"/>
<g fill="#ffffff" font-family="Arial Black, Arial, Helvetica, sans-serif" text-anchor="middle"><text font-size="{font_size}" font-weight="800" letter-spacing="{title_letter_spacing}">{title_spans}</text>
<g fill="none" stroke="#73c8d7" stroke-width="3"><path d="M220 {rule_y} H{title_x - 70}"/><path d="M{title_x + 70} {rule_y} H{cover_width - 220}"/></g>
<g fill="none" stroke="#73c8d7" stroke-linecap="square"><path d="M{title_x - 38} {rule_y - 9} L{title_x} {rule_y + 25} L{title_x + 38} {rule_y - 9}" stroke-width="8"/><path d="M{title_x - 20} {rule_y - 9} L{title_x} {rule_y + 8} L{title_x + 20} {rule_y - 9}" stroke-width="4"/></g>
<text x="{title_x}" y="{subtitle_y}" font-family="Arial, Helvetica, sans-serif" font-size="43" font-weight="600" letter-spacing="16">A HALO WAYPOINT CHRONICLE</text></g>
</svg>'''


def _build_epub(
    article: Article,
    assets: list[ImageAsset],
    image_paths: dict[str, str],
    output: Path,
    *,
    overwrite: bool = False,
    reading_style: str = "waypoint",
) -> None:
    identifier = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, article.source_url)}"
    modified = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    hero_asset = next((asset for asset in assets if asset.source_url == article.hero_url), None)
    cover_svg = _cover_svg(article, hero_asset)
    cover_body = f'<section class="cover" style="margin:0;padding:0"><img src="../Images/cover.svg" alt="Cover of {_xml(article.title)}" style="display:block;width:100%;height:auto;margin:0;padding:0;border:0"/></section>'
    cover_page_style = "margin:0;padding:0;max-width:none;background:#10191d;text-align:center;overflow:hidden"
    title_body = f'''<section class="title-page" epub:type="titlepage">
<div class="kicker">{_xml(article.category)}</div><h1>{_xml(article.title)}</h1><div class="rule"></div>
<div class="byline">by {_xml(article.author)}</div>
<div class="imprint">A Halo Waypoint Chronicle<br/>Personal EPUB edition</div></section>'''
    story_body = f'''<header class="story-header"><div class="book-title">{_xml(article.title)}</div></header>
<main epub:type="bodymatter">{_render_story(article, image_paths)}</main>'''
    credits_body = f'''<section class="credits"><h2 class="section-heading">Edition information</h2><dl>
<dt>Story</dt><dd>{_xml(article.title)}</dd><dt>Author</dt><dd>{_xml(article.author)}</dd>
<dt>Original publication</dt><dd><a href="{_xml(article.source_url)}">Halo Waypoint</a></dd>
<dt>Converted</dt><dd>{_xml(datetime.now().astimezone().strftime("%B %d, %Y"))}</dd>
</dl><p>This personal reading edition was generated from the publicly available article. Halo and its artwork remain the property of their respective rights holders.</p></section>'''
    nav_body = f'''<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>
<li><a href="title.xhtml">{_xml(article.title)}</a></li><li><a href="story.xhtml">Story</a></li><li><a href="credits.xhtml">Edition information</a></li>
</ol></nav>'''

    stylesheet = _stylesheet(reading_style)
    image_manifest = []
    for index, asset in enumerate(assets, start=1):
        image_manifest.append(f'<item id="image{index}" href="Images/{_xml(asset.filename)}" media-type="{_xml(asset.media_type)}"/>' )
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="en">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{_xml(article.title)}</dc:title><dc:creator>{_xml(article.author)}</dc:creator>
<dc:language>en</dc:language><dc:publisher>Halo Studios</dc:publisher><dc:source>{_xml(article.source_url)}</dc:source>
<dc:description>{_xml(article.plot)}</dc:description>
<meta property="dcterms:modified">{modified}</meta>
<meta name="cover" content="cover-image"/>
</metadata><manifest>
<item id="css" href="Styles/book.css" media-type="text/css"/>
<item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>
<item id="title" href="Text/title.xhtml" media-type="application/xhtml+xml"/>
<item id="story" href="Text/story.xhtml" media-type="application/xhtml+xml"/>
<item id="credits" href="Text/credits.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="cover-image" href="Images/cover.svg" media-type="image/svg+xml" properties="cover-image"/>
{''.join(image_manifest)}</manifest>
<spine toc="ncx"><itemref idref="cover" linear="yes"/><itemref idref="title"/><itemref idref="story"/><itemref idref="credits"/><itemref idref="nav" linear="no"/></spine>
</package>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{identifier}"/><meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{_xml(article.title)}</text></docTitle><navMap>
<navPoint id="nav1" playOrder="1"><navLabel><text>{_xml(article.title)}</text></navLabel><content src="Text/title.xhtml"/></navPoint>
<navPoint id="nav2" playOrder="2"><navLabel><text>Story</text></navLabel><content src="Text/story.xhtml"/></navPoint>
<navPoint id="nav3" playOrder="3"><navLabel><text>Edition information</text></navLabel><content src="Text/credits.xhtml"/></navPoint>
</navMap></ncx>'''
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>'''

    temporary_path: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not overwrite:
            raise ConversionError(f"The destination already exists: {output}. Choose another path or enable overwrite (--force in the CLI).")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.stem}-",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(temporary_path, "w") as book:
            book.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            book.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Styles/book.css", stylesheet, compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Images/cover.svg", cover_svg, compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Text/cover.xhtml", _xhtml(article.title, cover_body, "cover-body", cover_page_style), compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Text/title.xhtml", _xhtml(article.title, title_body), compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Text/story.xhtml", _xhtml(article.title, story_body), compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Text/credits.xhtml", _xhtml("Edition information", credits_body), compress_type=zipfile.ZIP_DEFLATED)
            book.writestr("OEBPS/Text/nav.xhtml", _xhtml("Contents", nav_body), compress_type=zipfile.ZIP_DEFLATED)
            for asset in assets:
                book.writestr(f"OEBPS/Images/{asset.filename}", asset.data, compress_type=zipfile.ZIP_DEFLATED)
        if output.exists() and not overwrite:
            raise ConversionError(f"The destination already exists: {output}. Choose another path or enable overwrite (--force in the CLI).")
        temporary_path.replace(output)
        temporary_path = None
    except ConversionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ConversionError(f"Could not write the EPUB: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def convert_url(
    url: str,
    output: str | Path | None = None,
    *,
    title: str | None = None,
    author: str | None = None,
    reading_style: str = "waypoint",
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ConversionResult:
    """Convert one public Halo Waypoint news article to an EPUB file."""

    notify = progress or (lambda _message: None)
    if cancelled and cancelled():
        raise ConversionCancelled("Conversion cancelled.")
    source_url = _validate_source_url(url)
    notify("Downloading article...")
    page, content_type = _download(source_url, max_bytes=MAX_PAGE_BYTES, cancelled=cancelled)
    if content_type not in {"text/html", "application/xhtml+xml", "application/octet-stream"}:
        raise ConversionError(f"Expected a web page but received {content_type}.")
    notify("Reading story and metadata...")
    article = parse_article(page, source_url)
    if title and title.strip():
        article.title = title.strip()
    if author and author.strip():
        article.author = author.strip()
    destination = Path(output).expanduser() if output else Path.cwd() / _safe_filename(article.title)
    if destination.suffix.lower() != ".epub":
        destination = destination.with_suffix(".epub")
    if destination.exists() and not overwrite:
        raise ConversionError(f"The destination already exists: {destination}. Choose another path or enable overwrite (--force in the CLI).")
    assets, image_paths, warnings = _download_images(article, notify, cancelled)
    if cancelled and cancelled():
        raise ConversionCancelled("Conversion cancelled.")
    notify("Packaging EPUB...")
    _build_epub(article, assets, image_paths, destination, overwrite=overwrite, reading_style=reading_style)
    notify("Done")
    return ConversionResult(destination.resolve(), article.title, article.author, len(assets), tuple(warnings))

