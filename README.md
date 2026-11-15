# The Haruspis Assistant

The Haruspis Assistant turns a Halo Waypoint Chronicle into a self-contained EPUB. It keeps the story text, emphasis, scene breaks, links, and article artwork, then applies a book design inspired by the official Waypoint Chronicle PDFs.

It uses Python plus Pillow for portable raster cover generation. No browser automation, Node, Docker, or Calibre installation is required.

<p align="center">
  <img src="screenshots/gui_example.png" alt="The Haruspis Assistant desktop interface" width="720"/>
</p>

## AI disclosure

This project was developed with assistance from OpenAI Codex, including help with code, documentation, and debugging. The application does not use generative AI at runtime: EPUBs are assembled deterministically from the selected Halo Waypoint article, its artwork, and the converter's built-in layouts and metadata rules.

## Why Though?
I loved how the previous Waypoint Chronicles included a PDF for people to have a local copy. The most recent ones haven't which considering the situation around Halo Studios I am not surprised. So I put this together with Codex so I could still have my local copies.

## Quick start (Windows)

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
2. Open PowerShell in this folder and install the required Python package:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Double-click `run_gui.bat`.
4. Choose a known Chronicle from the list, or select **Custom URL...** and paste a newer article URL.
5. Choose an archive folder. The EPUB filename is generated from the detected title or the optional title override.
6. Select **Official-inspired color** or **Classic black and white**, then select **Create EPUB**.

Select **Download All** to archive every written Chronicle in the built-in catalog into the same folder. Downloads run one at a time with a two-second cooldown between articles. Existing EPUBs are protected by default; enable **Replace existing EPUBs** only when you intend to refresh them. A failure on one article is reported at the end without stopping the rest of the archive. Audio-only releases such as `Halo: Still Midnight` are intentionally excluded because their article pages do not contain the story text.

The batch files run the application directly from this folder and show an installation reminder if Pillow is unavailable.

## Command line

From PowerShell in this folder, use the included launcher:

```powershell
.\run_cli.bat "https://www.halowaypoint.com/news/halo-elegy"
```

Choose a destination or override detected metadata:

```powershell
.\run_cli.bat URL --output "Halo - Elegy.epub" --title "Halo: Elegy" --author "Jeremy Patenaude"
```

Existing files are protected by default. Add `--force` only when you intentionally want to replace the destination:

```powershell
.\run_cli.bat URL --output "Halo - Elegy.epub" --force
```

Use a traditional black-on-white reading layout instead of the default Waypoint-inspired color treatment:

```powershell
.\run_cli.bat URL --style classic
```

The available styles are:

- `waypoint` (default): cream paper, blue accents, and Waypoint-inspired section styling
- `classic`: black text and rules on a white background

For development, an editable install also exposes the `haruspis-assistant` command:

```powershell
python -m pip install -e .
haruspis-assistant URL
```

## What it creates

- EPUB 3 output with EPUB 2 navigation fallback for older readers
- Semantic EPUB landmarks for the cover, title page, story, table of contents, and edition colophon
- [W3C EPUB Accessibility 1.2](https://www.w3.org/TR/epub-a11y-12/) discovery metadata describing access modes, reflowability, navigation, image alternatives, and content hazards
- A 1700×2400 artwork cover with the story title and Waypoint Chronicle treatment, packaged as both a raster cover image and the book's first page for Calibre, Kavita, and other library applications
- Title and credits pages
- A default Waypoint-inspired reading theme, with an optional classic black-and-white style
- Reflowable, column-safe book typography with reader-controlled margins, paragraph indents, and hyphenation
- Embedded article images, links, italics, bold text, headings, lists, and scene breaks
- Halo Studios publisher metadata, cataloged publication date and writer credits, the standalone synopsis (or Historian's Note as a fallback) as the plot/description, plus the source URL and conversion date
- Recoverable artwork downloads, bounded file sizes, and cancellation from the desktop interface
- A maintained selector of known [Waypoint Chronicles](https://www.halowaypoint.com/news?category=waypoint-chronicles), custom URL entry, and sequential bulk archiving into one folder

Reader apps can override fonts, colors, margins, and line spacing. The design intentionally uses common built-in serif and sans-serif fonts so it remains portable and readable.

## Covers and metadata

The converter uses the article's lead artwork to generate a single 1700×2400 JPEG containing the gradient, title, divider, chevron, and Chronicle label. That raster file is used by the in-book cover page and is declared through both EPUB 3 and legacy cover metadata, so library servers do not have to render an XHTML or SVG cover to discover the portrait artwork.

`Halo: Winter Contention` is handled as a precomposed cover because its source artwork already includes the title, divider, and Waypoint Chronicle label. The converter preserves that original treatment instead of adding a duplicate title layer.

Fort Foundry lists the Halo Series among users of its commercial [Industry](https://fortfoundry.com/fonts/industry) family. If Industry or Industry Inc is installed on the computer creating the EPUB, the converter rasterizes it into the cover automatically. The font files are not bundled; Bahnschrift and common sans-serif fonts provide the fallback treatment.

<p align="center">
  <img src="screenshots/cover_example.jpeg" alt="Example Waypoint Chronicle cover generated for Halo: Elegy" width="420"/>
</p>

For library applications such as Calibre and Kavita, `cover.jpg` is explicitly identified as the cover image while the matching cover page remains first in the reading order. This prevents servers from choosing the original landscape artwork as a fallback. Generated EPUB metadata includes:

- Title detected from the article, with an optional override
- Publication date and separate writer credits from the built-in catalog, sourced from [Halopedia's Waypoint Chronicle index](https://www.halopedia.org/Halo_Waypoint_Chronicle)
- Alex Wakeford and Jeff Easterling as the default Chronicle writers; separately credited stories retain their specific writer (for example, Jeremy Patenaude for `Halo: Elegy` and Kemi Ashing-Giwa for `Halo: Foehammer's Last Flight`)
- Standards-based author roles and sort names, a refined main title, `en-US` language, `Text` type, and `Science fiction` subject
- `Halo Waypoint Chronicles` collection membership with the official release position; Battle for the Academy also carries its numbered subseries membership
- Related-work metadata connecting `Halo: Saturn Devouring His Son` and its sequel `Halo: Ascension on Atropos`
- The Haruspis Assistant identified as the ebook producer using the MARC `bkp` role
- Accessibility discovery metadata for textual and visual content, sufficient access modes, navigation features, display transformability, and the absence of flashing, motion, or sound hazards
- An optional author override for one-off corrections
- Publisher set to `Halo Studios`
- The standalone opening synopsis as the plot/description when present, otherwise the Historian's Note
- Original Halo Waypoint article URL
- EPUB conversion date

Source-provided image descriptions are preserved as alternative text. Supplemental artwork without a source description is marked decorative rather than assigned an invented description, and the generated accessibility summary discloses that limitation. Scene breaks carry separator semantics, while source quotations remain native blockquotes. The converter does not claim formal WCAG or EPUB Accessibility conformance without a separate evaluation.

## Scope and troubleshooting

The extractor is intentionally limited to public Halo Waypoint news articles. It accepts both `halowaypoint.com/news/...` and locale-prefixed forms such as `halowaypoint.com/en/news/...`. This avoids accidentally packaging navigation, ads, or unrelated pages. It does not bypass logins, paywalls, or access controls.

Article pages are limited to 10 MB and each image to 40 MB. If optional artwork cannot be downloaded, the converter warns and continues; if the hero image is unavailable, it creates a text-based cover.

If Waypoint changes its page markup, the application will stop with a clear “article body not found” message instead of creating an empty book. Open an issue with the article URL and the page's saved HTML when that happens.

The downloaded story and artwork remain subject to their respective copyrights. This tool is intended for personal archival and reading; do not redistribute generated books without permission.
