"""Download a Halo Waypoint article and package it as an EPUB 3 book."""

from __future__ import annotations

import hashlib
import html
import io
import mimetypes
import os
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

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .catalog import PRIMARY_SERIES, ChronicleRelation, SeriesMembership, chronicle_for_url


USER_AGENT = "TheHaruspisAssistant/0.1 (+personal EPUB converter)"
ALLOWED_HOSTS = {"halowaypoint.com", "www.halowaypoint.com"}
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
PRECOMPOSED_COVER_SLUGS = {"halo-winter-contention"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
CONTENT_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "figure", "figcaption", "img", "hr", "br", "strong", "b", "em", "i", "a", "span", "sup", "sub"}
DISCARDED_TAGS = {"script", "style", "noscript", "template", "iframe", "object"}
SCENE_BREAK_XHTML = (
    '<div class="scene-break" role="separator" aria-label="Scene break">'
    '<span aria-hidden="true">&#10022;</span></div>'
)


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
    publication_date: str = ""
    additional_authors: tuple[str, ...] = ()
    series_memberships: tuple[SeriesMembership, ...] = ()
    relations: tuple[ChronicleRelation, ...] = ()

    @property
    def authors(self) -> tuple[str, ...]:
        """Return every credited writer in metadata order."""

        return tuple(name for name in (self.author, *self.additional_authors) if name)

    @property
    def author_line(self) -> str:
        """Return a human-readable joint byline."""

        return " and ".join(self.authors)


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


def _inline_historian_note(text: str) -> str | None:
    """Recognize a Historian's Note heading and return any same-node text."""

    normalized = text.strip().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    match = re.fullmatch(r"HISTORIAN'?S NOTE\s*:?\s*(.*)", normalized, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def _historian_note(content: Node) -> str:
    """Extract the opening Historian's Note for ebook description metadata."""

    children = [child for child in content.children if isinstance(child, Node)]
    for index, node in enumerate(children):
        inline_note = _inline_historian_note(node.text())
        if inline_note is None:
            continue
        if inline_note:
            return inline_note
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


def _opening_synopsis(content: Node) -> str:
    """Return a short emphasized synopsis when Waypoint places one first."""

    children = [child for child in content.children if isinstance(child, Node)]
    for node in children:
        text = node.text().strip()
        if not text:
            continue
        if _inline_historian_note(text) is not None:
            return ""
        meaningful_children = [
            child
            for child in node.children
            if isinstance(child, Node) or (isinstance(child, str) and child.strip())
        ]
        fully_emphasized = (
            len(meaningful_children) == 1
            and isinstance(meaningful_children[0], Node)
            and meaningful_children[0].tag in {"em", "i"}
        )
        if node.tag not in {"p", "blockquote"} or not fully_emphasized or len(text) > 1000:
            return ""
        return text
    return ""


def _validate_source_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ConversionError("Please enter a public halowaypoint.com article URL.")
    news_path = re.fullmatch(
        r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?news/[^/]+/?",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if news_path is None:
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
    byline = re.fullmatch(r"\s*By\s+(.+?)\s*", subtitle, flags=re.IGNORECASE)
    author = (byline.group(1).strip() if byline else "") or _find_meta(root, "name", "author") or "Halo Studios"
    category = category_node.text() if category_node else "Waypoint Chronicle"
    hero_source, hero_alt = _image_source(hero_node)
    hero_source = hero_source or _find_meta(root, "property", "og:image")
    hero_alt = hero_alt or _find_meta(root, "property", "og:image:alt") or "Cover artwork"
    hero_url = urljoin(source_url, hero_source) if hero_source else None
    plot = _opening_synopsis(content) or _historian_note(content)
    return Article(title, author, category, source_url, hero_url, hero_alt, content, plot)


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


def _file_as(name: str) -> str:
    """Return a library-friendly sort name for a person or organization."""

    normalized = name.strip()
    if not normalized or "," in normalized:
        return normalized
    if normalized.casefold() in {"343 industries", "halo studios"}:
        return normalized
    parts = normalized.split()
    if len(parts) < 2:
        return normalized
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _creator_metadata(article: Article) -> str:
    entries: list[str] = []
    for index, name in enumerate(article.authors, start=1):
        creator_id = f"creator-{index}"
        entries.extend(
            (
                f'<dc:creator id="{creator_id}">{_xml(name)}</dc:creator>',
                f'<meta refines="#{creator_id}" property="role" scheme="marc:relators">aut</meta>',
                f'<meta refines="#{creator_id}" property="file-as">{_xml(_file_as(name))}</meta>',
            )
        )
    return "".join(entries)


def _collection_metadata(article: Article) -> str:
    entries: list[str] = []
    for index, membership in enumerate(article.series_memberships, start=1):
        collection_id = f"collection-{index}"
        entries.extend(
            (
                f'<meta id="{collection_id}" property="belongs-to-collection">{_xml(membership.name)}</meta>',
                f'<meta refines="#{collection_id}" property="collection-type">series</meta>',
                f'<meta refines="#{collection_id}" property="group-position">{membership.position}</meta>',
            )
        )
    return "".join(entries)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def _cover_alt_text(article: Article) -> str:
    """Describe the generated cover without repeating generic source text."""

    base = f"Cover of {article.title} by {article.author_line}"
    source_alt = _normalized_text(article.hero_alt).strip()
    if not source_alt or source_alt.casefold() in {"article artwork", "cover artwork"}:
        return base
    if article.title.casefold() in source_alt.casefold():
        return source_alt
    return f"{base}. {source_alt}"


def _image_accessibility_attributes(node: Node) -> str:
    """Preserve useful source alt text and mark undescribed artwork decorative."""

    source_alt = _normalized_text(node.attrs.get("alt", "")).strip()
    if source_alt:
        return f'alt="{_xml(source_alt)}"'
    return 'alt="" role="presentation"'


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
        return f'<img src="{_xml(local)}" {_image_accessibility_attributes(node)}/>'
    if node.tag == "br":
        return "<br/>"
    if node.tag == "hr":
        return SCENE_BREAK_XHTML
    if node.tag == "a":
        href = urljoin(article.source_url, node.attrs.get("href", ""))
        if not href.startswith(("http://", "https://", "mailto:")):
            return body
        return f'<a href="{_xml(href)}">{body}</a>'
    if node.tag in {"span", "div"} and not plain and "<img " not in body:
        if node.tag == "div" and node.parent is article.content:
            return SCENE_BREAK_XHTML
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
        return SCENE_BREAK_XHTML
    elif tag in {"h2", "h3", "h4"}:
        css_class = ' class="section-heading"'
    elif tag == "blockquote":
        css_class = ' class="epigraph"'
    return f"<{tag}{css_class}>{body}</{tag}>"


def _render_story(article: Article, image_paths: dict[str, str]) -> str:
    rendered = "".join(_render_node(child, article, image_paths) for child in article.content.children)
    rendered = re.sub(
        rf"(?:\s*{re.escape(SCENE_BREAK_XHTML)}\s*){{2,}}",
        SCENE_BREAK_XHTML,
        rendered,
    )
    return rendered.strip()


WAYPOINT_CSS = """@charset "utf-8";
html { color: #202a2f; background: #f4f0e7; -webkit-hyphens: auto; hyphens: auto; }
body { margin: 0; padding: 0; max-width: none; box-sizing: border-box; font-family: Georgia, "Times New Roman", serif; font-size: 1em; font-kerning: normal; font-variant-ligatures: common-ligatures; line-height: 1.58; }
p { margin: 0 0 0.85em; overflow-wrap: break-word; text-align: justify; orphans: 2; widows: 2; }
p + p { text-indent: 1.25em; }
.note-heading + p, .section-heading + p, .scene-break + p, blockquote + p { text-indent: 0; }
a { color: #176579; text-decoration: underline; }
.cover-body { width: 100%; height: 100vh; margin: 0; padding: 0; max-width: none; background: #000000; text-align: center; overflow: hidden; }
.cover-raster { width: 100%; height: 100vh; margin: 0; padding: 0; }
.cover-raster img { display: block; width: 100%; height: 100%; max-width: none; max-height: none; margin: 0; object-fit: contain; }
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
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en-US" xml:lang="en-US">
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


def _cover_font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    """Load an installed Industry face when available, with portable fallbacks."""

    font_names = (
        (
            "Industry-Black.otf",
            "Industry Black.otf",
            "IndustryInc-Base.otf",
            "IndustryInc-Base.ttf",
            "bahnschrift.ttf",
            "arialbd.ttf",
            "DejaVuSansCondensed-Bold.ttf",
        )
        if bold
        else (
            "Industry-Medium.otf",
            "Industry Medium.otf",
            "Industry.otf",
            "bahnschrift.ttf",
            "arial.ttf",
            "DejaVuSans.ttf",
        )
    )
    roots = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
    ]
    for name in font_names:
        for candidate in [*(root / name for root in roots), Path(name)]:
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _letterspaced_extents(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    spacing: int,
) -> tuple[float, float]:
    """Return visible horizontal bounds for manually letterspaced text."""

    cursor = 0.0
    left: float | None = None
    right: float | None = None
    for character in text:
        bounds = draw.textbbox((cursor, 0), character, font=font, anchor="la")
        if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
            left = float(bounds[0]) if left is None else min(left, float(bounds[0]))
            right = float(bounds[2]) if right is None else max(right, float(bounds[2]))
        cursor += draw.textlength(character, font=font) + spacing
    if left is None or right is None:
        return 0.0, max(0.0, cursor - spacing)
    return left, right


def _letterspaced_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, spacing: int) -> float:
    left, right = _letterspaced_extents(draw, text, font, spacing)
    return right - left


def _draw_centered_letterspaced(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.ImageFont,
    spacing: int,
    *,
    fill: str,
    canvas_width: int,
) -> None:
    left, right = _letterspaced_extents(draw, text, font, spacing)
    x = (canvas_width - (right - left)) / 2 - left
    for character in text:
        # Align every glyph to the font's ascender line. Aligning each character
        # by its visible top makes short punctuation such as hyphens and dashes
        # jump above the shared baseline.
        draw.text((x, y), character, font=font, fill=fill, anchor="la")
        x += draw.textlength(character, font=font) + spacing


def _render_cover_jpeg(article: Article, hero_asset: ImageAsset | None) -> bytes:
    """Render one portable raster cover for EPUB readers and library servers."""

    width, height = 1700, 2400
    canvas = Image.new("RGB", (width, height), "#17262d")
    if hero_asset:
        try:
            with Image.open(io.BytesIO(hero_asset.data)) as source:
                source = ImageOps.exif_transpose(source).convert("RGB")
                canvas = ImageOps.fit(
                    source,
                    (width, height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
        except (OSError, UnidentifiedImageError):
            pass

    article_slug = urlparse(article.source_url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    if not (hero_asset and article_slug in PRECOMPOSED_COVER_SLUGS):
        gradient = Image.new("L", (1, height))
        alpha: list[int] = []
        for y in range(height):
            position = y / height
            if position < 0.48:
                opacity = 0
            elif position < 0.64:
                opacity = round(115 * (position - 0.48) / 0.16)
            elif position < 0.72:
                opacity = round(115 + 115 * (position - 0.64) / 0.08)
            elif position < 0.78:
                opacity = round(230 + 25 * (position - 0.72) / 0.06)
            else:
                opacity = 255
            alpha.append(opacity)
        gradient.putdata(alpha)
        gradient = gradient.resize((width, height))
        shade = Image.new("RGBA", (width, height), "#000000")
        shade.putalpha(gradient)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), shade).convert("RGB")

        draw = ImageDraw.Draw(canvas)
        title_lines = _cover_title_lines(article.title)
        title_size = 132
        title_spacing = 7
        while title_size > 54:
            title_font = _cover_font(title_size, bold=True)
            if max(_letterspaced_width(draw, line, title_font, title_spacing) for line in title_lines) <= 1450:
                break
            title_size -= 4
        title_font = _cover_font(title_size, bold=True)
        line_height = round(title_size * 1.04)
        title_top = 1900 - line_height * len(title_lines)
        for index, line in enumerate(title_lines):
            _draw_centered_letterspaced(
                draw,
                line,
                title_top + index * line_height,
                title_font,
                title_spacing,
                fill="#ffffff",
                canvas_width=width,
            )

        divider_y = 1954
        center = width // 2
        draw.line((130, divider_y, center - 90, divider_y), fill="#b9d7dc", width=3)
        draw.line((center + 90, divider_y, width - 130, divider_y), fill="#b9d7dc", width=3)
        draw.line((center - 62, divider_y - 9, center, divider_y + 45, center + 62, divider_y - 9), fill="#f4f7f8", width=9, joint="curve")
        draw.line((center - 31, divider_y - 9, center, divider_y + 18, center + 31, divider_y - 9), fill="#55cfe3", width=9, joint="curve")

        label = "A HALO WAYPOINT CHRONICLE"
        label_font = _cover_font(43, bold=False)
        label_spacing = 17
        while _letterspaced_width(draw, label, label_font, label_spacing) > 1450 and label_spacing > 4:
            label_spacing -= 1
        _draw_centered_letterspaced(
            draw,
            label,
            2040,
            label_font,
            label_spacing,
            fill="#ffffff",
            canvas_width=width,
        )

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=94, optimize=True, progressive=True)
    return output.getvalue()


def _cover_body(article: Article) -> str:
    return f'''<section class="cover-raster" epub:type="cover">
<img role="doc-cover" src="../Images/cover.jpg" alt="{_xml(_cover_alt_text(article))}"/></section>'''


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
    cover_jpeg = _render_cover_jpeg(article, hero_asset)
    cover_body = _cover_body(article)
    author_line = article.author_line
    author_metadata = _creator_metadata(article)
    collection_metadata = _collection_metadata(article)
    relation_metadata = "".join(f"<dc:relation>{_xml(relation.url)}</dc:relation>" for relation in article.relations)
    publication_metadata = f"<dc:date>{_xml(article.publication_date)}</dc:date>" if article.publication_date else ""
    publication_credit = ""
    if article.publication_date:
        try:
            publication_display = datetime.strptime(article.publication_date, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            publication_display = article.publication_date
        publication_credit = f"<dt>Published</dt><dd>{_xml(publication_display)}</dd>"
    series_credit = "".join(
        f'<dt>{"Series" if index == 0 else "Additional series"}</dt><dd>{_xml(membership.name)} #{membership.position}</dd>'
        for index, membership in enumerate(article.series_memberships)
    )
    relation_credit = "".join(
        f'<dt>Related work</dt><dd>{_xml(relation.label)} <a href="{_xml(relation.url)}">{_xml(relation.title)}</a></dd>'
        for relation in article.relations
    )
    cover_page_style = "width:100%;height:100vh;margin:0;padding:0;max-width:none;background:#000000;text-align:center;overflow:hidden"
    title_body = f'''<section class="title-page" epub:type="titlepage" aria-labelledby="title-page-heading">
<div class="kicker">{_xml(article.category)}</div><h1 id="title-page-heading">{_xml(article.title)}</h1><div class="rule"></div>
<div class="byline">by {_xml(author_line)}</div>
<div class="imprint">A Halo Waypoint Chronicle<br/>Personal EPUB edition</div></section>'''
    story_body = f'''<header class="story-header"><h1 id="story-heading" class="book-title">{_xml(article.title)}</h1></header>
<main id="bodymatter" epub:type="bodymatter" aria-labelledby="story-heading">{_render_story(article, image_paths)}</main>'''
    credits_body = f'''<section class="credits" epub:type="colophon" role="doc-colophon" aria-labelledby="colophon-heading"><h1 id="colophon-heading" class="section-heading">Edition information</h1><dl>
<dt>Story</dt><dd>{_xml(article.title)}</dd><dt>{"Author" if len(article.authors) == 1 else "Authors"}</dt><dd>{_xml(author_line)}</dd>
{publication_credit}
{series_credit}
{relation_credit}
<dt>Original publication</dt><dd><a href="{_xml(article.source_url)}">Halo Waypoint</a></dd>
<dt>Ebook producer</dt><dd>The Haruspis Assistant</dd>
<dt>Converted</dt><dd>{_xml(datetime.now().astimezone().strftime("%B %d, %Y"))}</dd>
</dl><p>This personal reading edition was generated from the publicly available article. Halo and its artwork remain the property of their respective rights holders.</p></section>'''
    nav_body = f'''<nav epub:type="toc" id="toc" role="doc-toc" aria-labelledby="toc-heading"><h1 id="toc-heading">Contents</h1><ol>
<li><a href="title.xhtml">{_xml(article.title)}</a></li><li><a href="story.xhtml#bodymatter">Story</a></li><li><a href="credits.xhtml">Edition information</a></li>
</ol></nav>
<nav epub:type="landmarks" aria-labelledby="landmarks-heading"><h2 id="landmarks-heading">Guide</h2><ol>
<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>
<li><a epub:type="titlepage" href="title.xhtml">Title page</a></li>
<li><a epub:type="bodymatter" href="story.xhtml#bodymatter">Start of story</a></li>
<li><a epub:type="colophon" href="credits.xhtml">Edition information</a></li>
<li><a epub:type="toc" href="#toc">Table of contents</a></li>
</ol></nav>'''

    stylesheet = _stylesheet(reading_style)
    image_manifest = []
    for index, asset in enumerate(assets, start=1):
        image_manifest.append(f'<item id="image{index}" href="Images/{_xml(asset.filename)}" media-type="{_xml(asset.media_type)}"/>' )
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="en-US">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title id="title">{_xml(article.title)}</dc:title>
<meta refines="#title" property="title-type">main</meta>{author_metadata}
<dc:language>en-US</dc:language><dc:type>Text</dc:type><dc:subject>Science fiction</dc:subject>
<dc:publisher>Halo Studios</dc:publisher>{publication_metadata}<dc:source>{_xml(article.source_url)}</dc:source>
<dc:description>{_xml(article.plot)}</dc:description>{relation_metadata}{collection_metadata}
<dc:contributor id="producer">The Haruspis Assistant</dc:contributor>
<meta refines="#producer" property="role" scheme="marc:relators">bkp</meta>
<meta refines="#producer" property="file-as">The Haruspis Assistant</meta>
<meta name="cover" content="cover-image"/>
<meta property="dcterms:modified">{modified}</meta>
<meta property="schema:accessMode">textual</meta>
<meta property="schema:accessMode">visual</meta>
<meta property="schema:accessModeSufficient">textual</meta>
<meta property="schema:accessModeSufficient">textual,visual</meta>
<meta property="schema:accessibilityFeature">alternativeText</meta>
<meta property="schema:accessibilityFeature">ARIA</meta>
<meta property="schema:accessibilityFeature">displayTransformability</meta>
<meta property="schema:accessibilityFeature">readingOrder</meta>
<meta property="schema:accessibilityFeature">structuralNavigation</meta>
<meta property="schema:accessibilityFeature">tableOfContents</meta>
<meta property="schema:accessibilityHazard">none</meta>
<meta property="schema:accessibilitySummary">The story is fully readable as reflowable text. The cover and source-described article images include alternative text. Supplemental images without source descriptions are marked decorative and may not convey their visual details to nonvisual readers.</meta>
</metadata><manifest>
<item id="css" href="Styles/book.css" media-type="text/css"/>
<item id="cover-image" href="Images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
<item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>
<item id="title" href="Text/title.xhtml" media-type="application/xhtml+xml"/>
<item id="story" href="Text/story.xhtml" media-type="application/xhtml+xml"/>
<item id="credits" href="Text/credits.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{''.join(image_manifest)}</manifest>
<spine toc="ncx"><itemref idref="cover" linear="yes"/><itemref idref="title"/><itemref idref="story"/><itemref idref="credits"/><itemref idref="nav" linear="no"/></spine>
<guide><reference type="cover" title="Cover" href="Text/cover.xhtml"/><reference type="title-page" title="Title page" href="Text/title.xhtml"/><reference type="text" title="Story" href="Text/story.xhtml#bodymatter"/><reference type="colophon" title="Edition information" href="Text/credits.xhtml"/></guide>
</package>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{identifier}"/><meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{_xml(article.title)}</text></docTitle><navMap>
<navPoint id="nav1" playOrder="1"><navLabel><text>{_xml(article.title)}</text></navLabel><content src="Text/title.xhtml"/></navPoint>
<navPoint id="nav2" playOrder="2"><navLabel><text>Story</text></navLabel><content src="Text/story.xhtml#bodymatter"/></navPoint>
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
            book.writestr("OEBPS/Images/cover.jpg", cover_jpeg, compress_type=zipfile.ZIP_DEFLATED)
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
    output_directory: str | Path | None = None,
    title: str | None = None,
    author: str | None = None,
    reading_style: str = "waypoint",
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ConversionResult:
    """Convert one public Halo Waypoint news article to an EPUB file."""

    notify = progress or (lambda _message: None)
    if output is not None and output_directory is not None:
        raise ConversionError("Choose either an output file or an output directory, not both.")
    if cancelled and cancelled():
        raise ConversionCancelled("Conversion cancelled.")
    source_url = _validate_source_url(url)
    notify("Downloading article...")
    page, content_type = _download(source_url, max_bytes=MAX_PAGE_BYTES, cancelled=cancelled)
    if content_type not in {"text/html", "application/xhtml+xml", "application/octet-stream"}:
        raise ConversionError(f"Expected a web page but received {content_type}.")
    notify("Reading story and metadata...")
    article = parse_article(page, source_url)
    catalog_entry = chronicle_for_url(source_url)
    if catalog_entry is not None:
        article.author = catalog_entry.authors[0]
        article.additional_authors = catalog_entry.authors[1:]
        article.publication_date = catalog_entry.published
        article.series_memberships = (
            SeriesMembership(PRIMARY_SERIES, catalog_entry.series_position),
            *catalog_entry.additional_series,
        )
        article.relations = catalog_entry.relations
    if title and title.strip():
        article.title = title.strip()
    if author and author.strip():
        article.author = author.strip()
        article.additional_authors = ()
    if output_directory is not None:
        destination = Path(output_directory).expanduser() / _safe_filename(article.title)
    else:
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
    return ConversionResult(destination.resolve(), article.title, article.author_line, len(assets), tuple(warnings))

