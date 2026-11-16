import io
import tempfile
import unittest
import zipfile
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from chronicle_binder.catalog import (
    DEFAULT_AUTHORS,
    PRIMARY_SERIES,
    KNOWN_CHRONICLES,
    ChronicleRelation,
    SeriesMembership,
    chronicle_for_url,
)
from chronicle_binder.converter import (
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
    _validate_source_url,
    convert_url,
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

DUAL_PLOT_SAMPLE_PAGE = b'''<!doctype html><html><head>
<meta name="author" content="343 INDUSTRIES"/>
<meta property="og:title" content="Halo: Venezian Sonata"/>
</head><body><article>
<div class="article-page_category__dual">Waypoint Chronicles</div>
<h2 class="article-page_title__dual">Halo: Venezian Sonata</h2>
<div class="wordpress-content_content__dual article-page_articleContent__dual">
<p><em>September 2559. Captain Veronica Dare is deployed to Venezia on a reconnaissance mission.</em></p>
<div></div><p>Available on Halo Waypoint and in other formats.</p><div></div>
<p><strong>HISTORIAN'S NOTE</strong></p>
<p><em>This story is set on September 22, 2559, before the assault on Zeta Halo.</em></p>
<div></div><p>Story text.</p>
</div></article></body></html>'''

INLINE_NOTE_SAMPLE_PAGE = '''<!doctype html><html><head>
<meta name="author" content="Halo Studios"/>
<meta property="og:title" content="Halo: Veritas Aeterna"/>
</head><body><article>
<div class="article-page_category__inline">Waypoint Chronicles</div>
<h2 class="article-page_title__inline">Halo: Veritas Aeterna</h2>
<div class="wordpress-content_content__inline article-page_articleContent__inline">
<p><strong>HISTORIAN’S NOTE</strong><br/><em>This story takes place on October 16, 2552, after the destruction of Alpha Halo.</em></p>
<div></div><p>Story text.</p>
</div></article></body></html>'''.encode("utf-8")

PROMO_THEN_NOTE_SAMPLE_PAGE = b'''<!doctype html><html><head>
<meta name="author" content="343 INDUSTRIES"/>
<meta property="og:title" content="Halo: Winter Contention"/>
</head><body><article>
<div class="article-page_category__promo">Waypoint Chronicles</div>
<h2 class="article-page_title__promo">Halo: Winter Contention</h2>
<div class="wordpress-content_content__promo article-page_articleContent__promo">
<p>Experience the return of Noble Team in <em>Halo: Winter Contention</em> <a href="book.pdf">in a free PDF</a>, or listen on YouTube.</p>
<div></div><p><strong>HISTORIAN'S NOTE</strong></p>
<p><em>Halo: Winter Contention is set in December 2551, before the fall of Reach.</em></p>
<div></div><p>Story text.</p>
</div></article></body></html>'''


def _sample_image_bytes(color: tuple[int, int, int] = (72, 110, 132)) -> bytes:
    data = io.BytesIO()
    Image.new("RGB", (640, 360), color).save(data, format="JPEG")
    return data.getvalue()


class ArticleParsingTests(unittest.TestCase):
    def test_known_chronicle_catalog_has_unique_titles_and_urls(self):
        titles = [entry.title for entry in KNOWN_CHRONICLES]
        urls = [entry.url for entry in KNOWN_CHRONICLES]
        self.assertEqual(len(KNOWN_CHRONICLES), 25)
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("https://www.halowaypoint.com/news/halo-veritas-aeterna", urls)
        self.assertNotIn("https://www.halowaypoint.com/news/halo-still-midnight", urls)
        self.assertTrue(all("halowaypoint.com/news/" in url for url in urls))
        self.assertTrue(all(entry.published.count("-") == 2 for entry in KNOWN_CHRONICLES))
        self.assertTrue(all(entry.authors for entry in KNOWN_CHRONICLES))
        self.assertEqual(len({entry.series_position for entry in KNOWN_CHRONICLES}), len(KNOWN_CHRONICLES))

    def test_catalog_uses_joint_default_authors_and_specific_story_credits(self):
        winter = chronicle_for_url("https://www.halowaypoint.com/en/news/halo-winter-contention")
        elegy = chronicle_for_url("https://www.halowaypoint.com/news/halo-elegy")
        foehammer = chronicle_for_url("https://www.halowaypoint.com/en-gb/news/halo-foehammers-last-flight")
        self.assertIsNotNone(winter)
        self.assertIsNotNone(elegy)
        self.assertIsNotNone(foehammer)
        self.assertEqual(winter.authors, DEFAULT_AUTHORS)
        self.assertEqual(elegy.authors, ("Jeremy Patenaude",))
        self.assertEqual(foehammer.authors, ("Kemi Ashing-Giwa",))
        self.assertEqual(elegy.published, "2026-11-02")

    def test_catalog_tracks_subseries_and_narrative_relationships(self):
        academy_one = chronicle_for_url("https://www.halowaypoint.com/news/halo-battle-for-the-academy-part-1")
        academy_two = chronicle_for_url("https://www.halowaypoint.com/news/halo-battle-for-the-academy-part-2")
        ascension = chronicle_for_url("https://www.halowaypoint.com/news/halo-ascension-on-atropos")
        saturn = chronicle_for_url("https://www.halowaypoint.com/news/halo-saturn-devouring-his-son")
        self.assertEqual(academy_one.series_position, 17)
        self.assertEqual(academy_one.additional_series, (SeriesMembership("Battle for the Academy", 1),))
        self.assertEqual(academy_two.additional_series, (SeriesMembership("Battle for the Academy", 2),))
        self.assertEqual(ascension.relations[0].label, "Sequel to")
        self.assertEqual(saturn.relations[0].label, "Followed by")

    def test_accepts_locale_prefixed_waypoint_news_urls(self):
        url = "https://www.halowaypoint.com/en/news/halo-veritas-aeterna"
        self.assertEqual(_validate_source_url(url), url)
        self.assertEqual(
            _validate_source_url("https://www.halowaypoint.com/en-us/news/halo-veritas-aeterna"),
            "https://www.halowaypoint.com/en-us/news/halo-veritas-aeterna",
        )

    def test_rejects_news_indexes_and_unrelated_localized_paths(self):
        for url in (
            "https://www.halowaypoint.com/en/news",
            "https://www.halowaypoint.com/en/games/halo-infinite",
            "https://www.halowaypoint.com/en/news/story/extra",
        ):
            with self.subTest(url=url), self.assertRaises(ConversionError):
                _validate_source_url(url)

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

    def test_opening_synopsis_takes_priority_over_historians_note(self):
        article = parse_article(
            DUAL_PLOT_SAMPLE_PAGE,
            "https://www.halowaypoint.com/news/halo-venezian-sonata",
        )
        self.assertEqual(
            article.plot,
            "September 2559. Captain Veronica Dare is deployed to Venezia on a reconnaissance mission.",
        )

    def test_extracts_historians_note_when_heading_and_text_share_a_paragraph(self):
        article = parse_article(
            INLINE_NOTE_SAMPLE_PAGE,
            "https://www.halowaypoint.com/news/halo-veritas-aeterna",
        )
        self.assertEqual(
            article.plot,
            "This story takes place on October 16, 2552, after the destruction of Alpha Halo.",
        )

    def test_promotional_intro_is_not_used_as_the_plot(self):
        article = parse_article(
            PROMO_THEN_NOTE_SAMPLE_PAGE,
            "https://www.halowaypoint.com/news/halo-winter-contention",
        )
        self.assertEqual(
            article.plot,
            "Halo: Winter Contention is set in December 2551, before the fall of Reach.",
        )

    def test_cover_title_lines_stay_within_target_width(self):
        lines = _cover_title_lines("Halo: An Exceptionallylongunbrokenword Chronicle")
        self.assertTrue(lines)
        self.assertTrue(all(len(line) <= 18 for line in lines))

    def test_cover_title_keeps_separator_dashes_and_compound_hyphens(self):
        self.assertEqual(
            _cover_title_lines("Halo: Battle for the Academy - Part 2"),
            ["BATTLE FOR THE", "ACADEMY - PART 2"],
        )
        self.assertEqual(
            _cover_title_lines("Halo: Battle for the Academy – Part 2"),
            ["BATTLE FOR THE", "ACADEMY – PART 2"],
        )
        self.assertIn("BLOOD-MOON", _cover_title_lines("Halo: Battle for the Blood-Moon"))

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
            publication_date="2026-11-15",
            additional_authors=("Second Author",),
            series_memberships=(
                SeriesMembership(PRIMARY_SERIES, 28),
                SeriesMembership("Test Subseries", 1),
            ),
            relations=(
                ChronicleRelation(
                    "Sequel to",
                    "Halo: Earlier Story",
                    "https://www.halowaypoint.com/news/halo-earlier-story",
                ),
            ),
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
                self.assertIn('<dc:title id="title">Halo: Test</dc:title>', opf_text)
                self.assertIn('<meta refines="#title" property="title-type">main</meta>', opf_text)
                self.assertIn('<dc:creator id="creator-1">Test Author</dc:creator>', opf_text)
                self.assertIn('<meta refines="#creator-1" property="role" scheme="marc:relators">aut</meta>', opf_text)
                self.assertIn('<meta refines="#creator-1" property="file-as">Author, Test</meta>', opf_text)
                self.assertIn('<dc:creator id="creator-2">Second Author</dc:creator>', opf_text)
                self.assertIn("<dc:date>2026-11-15</dc:date>", opf_text)
                self.assertIn("<dc:language>en-US</dc:language>", opf_text)
                self.assertIn("<dc:type>Text</dc:type>", opf_text)
                self.assertIn("<dc:subject>Science fiction</dc:subject>", opf_text)
                self.assertIn("<dc:rights>Halo © Microsoft Corporation.", opf_text)
                self.assertIn("not endorsed by or affiliated with Microsoft", opf_text)
                self.assertIn('<meta id="collection-1" property="belongs-to-collection">Halo Waypoint Chronicles</meta>', opf_text)
                self.assertIn('<meta refines="#collection-1" property="group-position">28</meta>', opf_text)
                self.assertIn('<meta id="collection-2" property="belongs-to-collection">Test Subseries</meta>', opf_text)
                self.assertIn("<dc:relation>https://www.halowaypoint.com/news/halo-earlier-story</dc:relation>", opf_text)
                self.assertIn('<dc:contributor id="producer">The Chronicle Binder</dc:contributor>', opf_text)
                self.assertIn('<meta refines="#producer" property="role" scheme="marc:relators">bkp</meta>', opf_text)
                self.assertIn('<meta property="schema:accessMode">textual</meta>', opf_text)
                self.assertIn('<meta property="schema:accessMode">visual</meta>', opf_text)
                self.assertIn('<meta property="schema:accessModeSufficient">textual</meta>', opf_text)
                self.assertIn('<meta property="schema:accessModeSufficient">textual,visual</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">alternativeText</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">ARIA</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">displayTransformability</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">readingOrder</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">structuralNavigation</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityFeature">tableOfContents</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilityHazard">none</meta>', opf_text)
                self.assertIn('<meta property="schema:accessibilitySummary">', opf_text)
                title_page = book.read("OEBPS/Text/title.xhtml").decode("utf-8")
                credits_page = book.read("OEBPS/Text/credits.xhtml").decode("utf-8")
                self.assertIn("by Test Author and Second Author", title_page)
                self.assertIn("November 15, 2026", credits_page)
                self.assertIn("Halo Waypoint Chronicles #28", credits_page)
                self.assertIn("Test Subseries #1", credits_page)
                self.assertIn("Sequel to", credits_page)
                self.assertIn("Ebook producer", credits_page)
                self.assertIn('href="https://www.xbox.com/en-US/developers/rules"', credits_page)
                self.assertIn("not endorsed by or affiliated with Microsoft", credits_page)
                self.assertIn('<meta name="cover" content="cover-image"/>', opf_text)
                self.assertIn('id="cover-image" href="Images/cover.jpg" media-type="image/jpeg" properties="cover-image"', opf_text)
                self.assertIn('<reference type="cover" title="Cover" href="Text/cover.xhtml"/>', opf_text)
                self.assertIn('<reference type="text" title="Story" href="Text/story.xhtml#bodymatter"/>', opf_text)
                self.assertIn('style="width:100%;height:100vh;margin:0;padding:0;max-width:none;background:#000000;text-align:center;overflow:hidden"', cover_page)
                self.assertIn('class="cover-raster"', cover_page)
                self.assertIn('role="doc-cover"', cover_page)
                self.assertIn('src="../Images/cover.jpg"', cover_page)
                self.assertIn('alt="Cover of Halo: Test by Test Author and Second Author"', cover_page)
                story_page = book.read("OEBPS/Text/story.xhtml").decode("utf-8")
                nav_page = book.read("OEBPS/Text/nav.xhtml").decode("utf-8")
                self.assertIn('epub:type="titlepage" aria-labelledby="title-page-heading"', title_page)
                self.assertIn('id="bodymatter" epub:type="bodymatter" aria-labelledby="story-heading"', story_page)
                self.assertIn('epub:type="colophon" role="doc-colophon"', credits_page)
                self.assertIn('epub:type="toc" id="toc" role="doc-toc"', nav_page)
                self.assertIn('epub:type="landmarks"', nav_page)
                self.assertIn('epub:type="bodymatter" href="story.xhtml#bodymatter"', nav_page)
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
                            reference_path = reference.split("#", 1)[0]
                            if not reference_path:
                                continue
                            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), reference_path))
                            self.assertIn(target, names, f"Broken reference in {name}: {reference}")

    def test_story_accessibility_preserves_alt_text_and_hides_undocumented_artwork(self):
        content = Node("div")
        described = Node(
            "img",
            {"src": "described.jpg", "alt": "A spacecraft above a planet."},
            parent=content,
        )
        undescribed = Node("img", {"src": "undocumented.jpg", "alt": ""}, parent=content)
        divider = Node("hr", parent=content)
        quotation = Node("blockquote", parent=content)
        quotation.children.append("A quoted transmission.")
        content.children.extend((described, undescribed, divider, quotation))
        article = Article(
            "Halo: Accessible Test",
            "Test Author",
            "Waypoint Chronicles",
            "https://www.halowaypoint.com/news/test",
            None,
            "",
            content,
        )
        rendered = _render_story(
            article,
            {
                "https://www.halowaypoint.com/news/described.jpg": "../Images/described.jpg",
                "https://www.halowaypoint.com/news/undocumented.jpg": "../Images/undocumented.jpg",
            },
        )
        self.assertIn('alt="A spacecraft above a planet."', rendered)
        self.assertIn('alt="" role="presentation"', rendered)
        self.assertIn('role="separator" aria-label="Scene break"', rendered)
        self.assertIn('aria-hidden="true"', rendered)
        self.assertIn('<blockquote class="epigraph">A quoted transmission.</blockquote>', rendered)

    def test_refuses_to_replace_an_existing_epub_without_permission(self):
        content = Node("div")
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", None, "", content)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.epub"
            output.write_bytes(b"keep me")
            with self.assertRaises(ConversionError):
                _build_epub(article, [], {}, output)
            self.assertEqual(output.read_bytes(), b"keep me")

    def test_output_directory_uses_the_overridden_title_as_the_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "Archive Title.epub"
            with (
                patch("chronicle_binder.converter._download", return_value=(SAMPLE_PAGE, "text/html")),
                patch("chronicle_binder.converter._download_images", return_value=([], {}, [])),
                patch("chronicle_binder.converter._build_epub") as build_epub,
            ):
                result = convert_url(
                    "https://www.halowaypoint.com/news/test",
                    output_directory=directory,
                    title="Archive Title",
                )
            self.assertEqual(result.output_path, expected.resolve())
            self.assertEqual(build_epub.call_args.args[3], expected)

    def test_known_url_applies_catalog_authors_and_publication_date(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("chronicle_binder.converter._download", return_value=(SAMPLE_PAGE, "text/html")),
                patch("chronicle_binder.converter._download_images", return_value=([], {}, [])),
                patch("chronicle_binder.converter._build_epub") as build_epub,
            ):
                result = convert_url(
                    "https://www.halowaypoint.com/en/news/halo-veritas-aeterna",
                    output_directory=directory,
                )
            article = build_epub.call_args.args[0]
            self.assertEqual(article.authors, DEFAULT_AUTHORS)
            self.assertEqual(article.publication_date, "2026-11-15")
            self.assertEqual(article.series_memberships, (SeriesMembership(PRIMARY_SERIES, 28),))
            self.assertEqual(result.author, "Alex Wakeford and Jeff Easterling")

    def test_manual_author_override_replaces_all_catalog_authors(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("chronicle_binder.converter._download", return_value=(SAMPLE_PAGE, "text/html")),
                patch("chronicle_binder.converter._download_images", return_value=([], {}, [])),
                patch("chronicle_binder.converter._build_epub") as build_epub,
            ):
                convert_url(
                    "https://www.halowaypoint.com/news/halo-veritas-aeterna",
                    output_directory=directory,
                    author="Override Author",
                )
            self.assertEqual(build_epub.call_args.args[0].authors, ("Override Author",))

    def test_output_file_and_directory_are_mutually_exclusive(self):
        with self.assertRaises(ConversionError):
            convert_url(
                "https://www.halowaypoint.com/news/test",
                "book.epub",
                output_directory="archive",
            )

    def test_missing_artwork_is_skipped_with_a_warning(self):
        content = Node("div")
        image = Node("img", {"src": "https://example.invalid/missing.png"}, parent=content)
        content.children.append(image)
        article = Article("Halo: Test", "Author", "Waypoint Chronicles", "https://www.halowaypoint.com/news/test", None, "", content)
        messages: list[str] = []
        with patch("chronicle_binder.converter._download", side_effect=ConversionError("not found")):
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
                self.assertIn('<reference type="cover" title="Cover" href="Text/cover.xhtml"/>', opf)
                self.assertIn('<reference type="colophon" title="Edition information" href="Text/credits.xhtml"/>', opf)
                self.assertIn('id="image1" href="Images/hero.png" media-type="image/png"/>', opf)
                cover_page = book.read("OEBPS/Text/cover.xhtml").decode("utf-8")
                self.assertIn('class="cover-raster"', cover_page)
                self.assertIn('role="doc-cover"', cover_page)
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
