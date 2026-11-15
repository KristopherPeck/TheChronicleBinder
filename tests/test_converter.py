import io
import tempfile
import unittest
import zipfile
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from librarian_assistant.converter import (
    Article,
    ConversionCancelled,
    ConversionError,
    ImageAsset,
    Node,
    _build_epub,
    _cover_title_lines,
    _download_images,
    _render_cover_jpeg,
    _render_story,
    parse_article,
)


SAMPLE_PAGE = b'''<!doctype html><html><head>
<meta property="og:title" content="Halo: Test Chronicle"/>
<meta property="og:image" content="https://example.invalid/meta-cover.png"/>
</head><body><article>
<div class="article-page_category__abc">Waypoint Chronicles</div>
<h2 class="article-page_title__abc">Halo: Test Chronicle</h2>
<div class="article-page_subtitle__abc">By Test Author</div>
<picture class="article-page_articleImage__abc"><img src="https://example.invalid/cover.png" alt="Test cover"/></picture>
<div class="wordpress-content_content__abc article-page_articleContent__abc">
<p><strong>HISTORIAN'S NOTE</strong></p><p><em>A test note.</em></p><div></div>
<p>First paragraph with <strong>bold</strong> text.<script>do_not_render()</script></p>
</div></article></body></html>'''

LEGACY_SAMPLE_PAGE = b'''<!doctype html><html><head>
<meta name="author" content="343 INDUSTRIES"/>
<meta property="og:title" content="Halo 4 | VERTICAL UMBRAGE"/>
<meta property="og:image" content="https://example.invalid/legacy-cover.jpg"/>
</head><body><article>
<div class="article-page_category__legacy">Waypoint Chronicles</div>
<h2 class="article-page_title__legacy">Halo 4 | VERTICAL UMBRAGE</h2>
<div class="article-page_subtitle__legacy">Everything Has Gone Wrong (...Well, Not Everything)</div>
<div class="wordpress-content_content__legacy"><p>Legacy story text.</p></div>
</article></body></html>'''


def _sample_image_bytes(color: tuple[int, int, int] = (72, 110, 132)) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (640, 360), color).save(data, format="JPEG")
    return data.getvalue()


class ArticleParsingTests(unittest.TestCase):
    def test_extracts_waypoint_metadata_and_body(self):
        article = parse_article(SAMPLE_PAGE, "https://www.halowaypoint.com/news/test")
        self.assertEqual(article.title, "Halo: Test Chronicle")
        self.assertEqual(article.author, "Test Author")
        self.assertEqual(article.category, "Waypoint Chronicles")
        self.assertEqual(article.hero_url, "https://example.invalid/cover.png")
        self.assertEqual(article.hero_alt, "Test cover")
        self.assertEqual(article.plot, "A test note.")
        self.assertIn("First paragraph", article.content.text())
        rendered = _render_story(article, {})
        self.assertIn('class="note-heading"', rendered)
        self.assertNotIn("do_not_render", rendered)

    def test_legacy_teaser_is_not_mistaken_for_the_author(self):
        article = parse_article(LEGACY_SAMPLE_PAGE, "https://www.halowaypoint.com/news/halo-4-vertical-umbrage")
        self.assertEqual(article.author, "343 INDUSTRIES")
        self.assertEqual(article.hero_url, "https://example.invalid/legacy-cover.jpg")

    def test_cover_title_lines_stay_within_target_width(self):
        lines = _cover_title_lines("Halo: An Exceptionallylongunbrokenword Chronicle")
        self.assertTrue(lines)
        self.assertTrue(all(len(line) <= 18 for line in lines))

    def test_builds_epub_with_required_first_entry_and_navigation(self):
        content = Node("div")
        paragraph = Node("p", parent=content)
        paragraph.children.append("A short story.")
        content.children.append(paragraph)
        article = Article(
            title="Halo: Test",
            author="Test Author",
            category="Waypoint Chronicles",
            source_url="https://www.halowaypoint.com/news/test",
            hero_url=None,
            hero_alt="",
            content=content,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.epub"
            _build_epub(article, [], {}, output)
            with zipfile.ZipFile(output) as book:
                first = book.infolist()[0]
                self.assertEqual(first.filename, "mimetype")
                self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
                self.assertIn("OEBPS/content.opf", book.namelist())
                self.assertIn("OEBPS/Text/nav.xhtml", book.namelist())
                self.assertIn("OEBPS/Images/cover.jpg", book.namelist())
                self.assertEqual(book.read("mimetype"), b"application/epub+zip")
                opf_text = book.read("OEBPS/content.opf").decode("utf-8")
                cover_page = book.read("OEBPS/Text/cover.xhtml").decode("utf-8")
                self.assertIn("<dc:publisher>Halo Studios</dc:publisher>", opf_text)
                self.assertIn('<meta name="cover" content="cover-image"/>', opf_text)
                self.assertIn('id="cover-image" href="Images/cover.jpg" media-type="image/jpeg" properties="cover-image"', opf_text)
                self.assertIn('<guide><reference type="cover" title="Cover" href="Text/cover.xhtml"/></guide>', opf_text)
                self.assertIn('style="width:100%;height:100vh;margin:0;padding:0;max-width:none;background:#000000;text-align:center;overflow:hidden"', cover_page)
                self.assertIn('class="cover-raster"', cover_page)
                self.assertIn('src="../Images/cover.jpg"', cover_page)
                names = set(book.namelist())
                xml_names = [
                    name for name in names
                    if name.endswith((".xml", ".opf", ".ncx", ".xhtml", ".svg"))
                ]
                parsed = {name: ET.fromstring(book.read(name)) for name in xml_names}
                opf = parsed["OEBPS/content.opf"]
                manifest_items = opf.findall("{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item")
                manifest_ids = {item.attrib["id"] for item in manifest_items}
                for item in manifest_items:
                    target = posixpath.normpath(posixpath.join("OEBPS", item.attrib["href"]))
                    self.assertIn(target, names)
                for itemref in opf.findall("{http://www.idpf.org/2007/opf}spine/{http://www.idpf.org/2007/opf}itemref"):
                    self.assertIn(itemref.attrib["idref"], manifest_ids)
                for name, root in parsed.items():
                    for element in root.iter():
                        for attribute in ("href", "{http://www.w3.org/1999/xlink}href", "src"):
                            reference = element.attrib.get(attribute)
                            if not reference or reference.startswith(("http://", "https://", "mailto:", "#")):
                                continue
                            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), reference))
                            self.assertIn(target, names, f"Broken reference in {name}: {reference}")

    def test_refuses_to_replace_an_existing_epub_without_permission(self):
        content = Node("div")
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", None, "", content)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.epub"
            output.write_bytes(b"keep me")
            with self.assertRaises(ConversionError):
                _build_epub(article, [], {}, output)
            self.assertEqual(output.read_bytes(), b"keep me")

    def test_missing_artwork_is_skipped_with_a_warning(self):
        content = Node("div")
        image = Node("img", {"src": "https://example.invalid/missing.png"}, parent=content)
        content.children.append(image)
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", None, "", content)
        messages: list[str] = []
        with patch("librarian_assistant.converter._download", side_effect=ConversionError("not found")):
            assets, replacements, warnings = _download_images(article, messages.append)
        self.assertEqual(assets, [])
        self.assertEqual(replacements, {})
        self.assertEqual(len(warnings), 1)
        self.assertTrue(any(message.startswith("Warning:") for message in messages))

    def test_download_can_be_cancelled_between_images(self):
        content = Node("div")
        image = Node("img", {"src": "https://example.invalid/image.png"}, parent=content)
        content.children.append(image)
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", None, "", content)
        with self.assertRaises(ConversionCancelled):
            _download_images(article, lambda _message: None, lambda: True)

    def test_designed_cover_renders_a_portrait_jpeg_with_a_black_lower_field(self):
        content = Node("div")
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", "https://example.invalid/hero.jpg", "Hero", content)
        hero = ImageAsset(article.hero_url, "hero.jpg", "image/jpeg", _sample_image_bytes())
        cover = _render_cover_jpeg(article, hero)
        self.assertTrue(cover.startswith(b"\xff\xd8\xff"))
        with Image.open(io.BytesIO(cover)) as image:
            self.assertEqual(image.size, (1700, 2400))
            self.assertLess(max(image.getpixel((850, 2300))), 15)

    def test_winter_contention_keeps_its_precomposed_cover_treatment(self):
        content = Node("div")
        article = Article(
            "Halo: Winter Contention",
            "343 INDUSTRIES",
            "Waypoint Chronicles",
            "https://www.halowaypoint.com/news/halo-winter-contention",
            "https://example.invalid/winter.jpg",
            "Cover for Halo: Winter Contention",
            content,
        )
        hero = ImageAsset(article.hero_url, "winter.jpg", "image/jpeg", _sample_image_bytes((180, 80, 60)))
        cover = _render_cover_jpeg(article, hero)
        with Image.open(io.BytesIO(cover)) as image:
            self.assertEqual(image.size, (1700, 2400))
            red, green, blue = image.getpixel((850, 2300))
            self.assertGreater(red, green)
            self.assertGreater(red, blue)

    def test_designed_cover_uses_renderable_cover_page_and_plot_metadata(self):
        content = Node("div")
        article = Article(
            "Halo: Test",
            "Author",
            "Waypoint Chronicles",
            "https://www.halowaypoint.com/news/test",
            "https://example.invalid/cover.png",
            "Cover",
            content,
            "The story's Historian's Note.",
        )
        hero = ImageAsset(article.hero_url, "hero.png", "image/png", _sample_image_bytes())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.epub"
            _build_epub(article, [hero], {article.hero_url: "../Images/hero.png"}, output, reading_style="classic")
            with zipfile.ZipFile(output) as book:
                opf = book.read("OEBPS/content.opf").decode("utf-8")
                css = book.read("OEBPS/Styles/book.css").decode("utf-8")
                self.assertIn('<meta name="cover" content="cover-image"/>', opf)
                self.assertIn('id="cover-image" href="Images/cover.jpg" media-type="image/jpeg" properties="cover-image"', opf)
                self.assertIn('<guide><reference type="cover" title="Cover" href="Text/cover.xhtml"/></guide>', opf)
                self.assertIn('id="image1" href="Images/hero.png" media-type="image/png"/>', opf)
                cover_page = book.read("OEBPS/Text/cover.xhtml").decode("utf-8")
                self.assertIn('class="cover-raster"', cover_page)
                self.assertIn('src="../Images/cover.jpg"', cover_page)
                with Image.open(io.BytesIO(book.read("OEBPS/Images/cover.jpg"))) as cover_image:
                    self.assertEqual(cover_image.size, (1700, 2400))
                self.assertIn("body { margin: 0; padding: 0; max-width: none; box-sizing: border-box;", css)
                self.assertNotIn("max-width: 38em", css)
                self.assertNotIn("@page", css)
                self.assertIn("hyphens: auto", css)
                self.assertIn("p { margin: 0 0 0.85em;", css)
                self.assertIn("overflow-wrap: break-word", css)
                self.assertIn("<dc:description>The story&#x27;s Historian&#x27;s Note.</dc:description>", opf)
                self.assertIn("background: #ffffff", css)
                self.assertIn("color: inherit", css)
                self.assertIn("border-color: currentColor", css)


if __name__ == "__main__":
    unittest.main()
