# The Chronicle Binder

The Chronicle Binder is an unofficial, noncommercial desktop tool that turns a public Halo Waypoint Chronicle into a self-contained EPUB for personal archival and reading. It preserves story text, emphasis, scene breaks, links, and article artwork, then applies a book design inspired by the official Waypoint Chronicle PDFs.

It uses Python and Pillow for portable raster cover generation. No browser automation, Node, Docker, Calibre installation, account, or API key is required.

<p align="center">
  <img src="screenshots/gui_example.png" alt="The Chronicle Binder desktop interface" width="720"/>
</p>


## AI disclosure

This project was developed with assistance from OpenAI Codex, including help with code, documentation, and debugging. The application does not use generative AI at runtime: EPUBs are assembled deterministically from the selected Waypoint article, its artwork, and the converter's built-in layouts and metadata rules.

## Why this exists

Earlier Waypoint Chronicles often included downloadable PDFs, while newer releases have not always provided an equivalent offline file. This project provides a consistent way to build a personal, local reading archive from the public articles.

## Important fan-project notice

**Halo © Microsoft Corporation. The Chronicle Binder was created under Microsoft's "Game Content Usage Rules" using assets from Halo, and it is not endorsed by or affiliated with Microsoft.**

Read Microsoft's [Game Content Usage Rules](https://www.xbox.com/en-US/developers/rules). This project is free, noncommercial, and ad-free. Microsoft, Halo, Halo Waypoint, and Xbox are trademarks of the Microsoft group of companies. Associated artwork, stories, names, and other content belong to their respective owners.

The MIT License applies only to this project's original source code and documentation. It does not grant rights to Microsoft or third-party content, repository screenshots containing Halo imagery, downloaded Waypoint material, or generated EPUBs. See [NOTICE.md](NOTICE.md) for details.

Generated EPUBs are intended for personal archival and reading. Do not assume this tool grants permission to redistribute them; anyone sharing material is responsible for obtaining any required rights and following the applicable content-usage rules.

## Features

- Desktop interface with a maintained catalog of known Waypoint Chronicles and a custom URL option
- Single-book conversion or sequential **Download All** archiving with a two-second cooldown
- Automatic filenames with optional title and author overrides
- Artwork-based 1700×2400 raster covers designed for Calibre, Kavita, and other library applications
- Waypoint-inspired color and classic black-and-white reading styles
- Reflowable, column-safe typography with reader-controlled margins, fonts, colors, and spacing
- Embedded article images, links, emphasis, headings, lists, quotations, and scene breaks
- EPUB 3 navigation with an EPUB 2 NCX fallback and legacy cover metadata
- Cataloged writer, publication date, publisher, plot, series, relationship, and ebook-producer metadata
- W3C EPUB Accessibility 1.2 discovery metadata and semantic navigation landmarks
- Bounded downloads, recoverable artwork failures, cancellation, and overwrite protection

## Requirements

- Python 3.11 or newer
- Pillow 13.0 or newer within the 13.x release line
- Tkinter for the desktop interface; it is included with the standard Windows installer from [python.org](https://www.python.org/downloads/)

The included batch launchers target Windows. The Python package and command-line interface can also run anywhere their Python dependencies are available.

## Quick start

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). On Windows, enable **Add Python to PATH** during installation.
2. Open PowerShell in the project folder and install the dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Double-click `run_gui.bat`.
4. Choose a known Chronicle or select **Custom URL...** and enter a public Halo Waypoint news URL.
5. Choose an existing archive folder.
6. Optionally override the detected title or author and choose a reading style.
7. Select **Create EPUB**.

Files use the detected title automatically. Existing EPUBs are protected by default; enable **Replace existing EPUBs** only when intentionally refreshing an archive.

### Download all

Select **Download All** to process every written Chronicle in the built-in catalog into one folder. Downloads run sequentially with a two-second pause between articles. A failure on one article is reported at the end without stopping the remaining archive.

Audio-only releases such as `Halo: Still Midnight` are intentionally excluded because their Waypoint pages do not contain the written story.

## Command line

Use the included Windows launcher:

```powershell
.\run_cli.bat "https://www.halowaypoint.com/news/halo-elegy"
```

Choose a destination or override detected metadata:

```powershell
.\run_cli.bat URL --output "Halo - Elegy.epub" --title "Halo: Elegy" --author "Jeremy Patenaude"
```

Use the classic reading style:

```powershell
.\run_cli.bat URL --style classic
```

Add `--force` only when intentionally replacing an existing destination:

```powershell
.\run_cli.bat URL --output "Halo - Elegy.epub" --force
```

An editable install also exposes the `chronicle-binder` command:

```powershell
python -m pip install -e .
chronicle-binder URL
```

## EPUB design and metadata

The converter uses the article's lead artwork to generate a single 1700×2400 JPEG containing the gradient, title, divider, chevron, and Chronicle label. The same raster is both the in-book cover and the explicitly declared EPUB cover image, preventing library servers from selecting landscape article art as a fallback.

`Halo: Winter Contention` is handled as a precomposed cover because its source artwork already includes the title treatment. The converter preserves that composition rather than adding duplicate text.

Fort Foundry lists the Halo Series among users of its commercial [Industry](https://fortfoundry.com/fonts/industry) family. If Industry or Industry Inc is installed locally, the converter rasterizes it into the generated cover. Font files are not bundled; Bahnschrift and common sans-serif fonts provide portable fallbacks.

<p align="center">
  <img src="screenshots/cover_example.jpeg" alt="Example Waypoint Chronicle cover generated for Halo: Elegy" width="420"/>
</p>

Generated metadata includes:

- The detected title, or an optional override
- Publication date and separate writer credits from the built-in catalog, sourced from [Halopedia's Waypoint Chronicle index](https://www.halopedia.org/Halo_Waypoint_Chronicle)
- Alex Wakeford and Jeff Easterling as default Chronicle writers, while separately credited stories retain their named writers
- Standards-based author roles and sort names, `en-US` language, `Text` type, and `Science fiction` subject
- `Halo Studios` as publisher
- `Halo Waypoint Chronicles` series membership and numbered Battle for the Academy subseries membership
- Related-work metadata connecting `Halo: Saturn Devouring His Son` and `Halo: Ascension on Atropos`
- The Chronicle Binder as ebook producer using the MARC `bkp` role
- The standalone opening synopsis as the plot when present, otherwise the Historian's Note
- The original Waypoint URL, conversion date, and rights notice

## Accessibility

The generated books include [W3C EPUB Accessibility 1.2](https://www.w3.org/TR/epub-a11y-12/) discovery metadata describing textual and visual access modes, sufficient access modes, display transformability, structural navigation, the table of contents, and the absence of flashing, motion, or sound hazards.

Source-provided image descriptions are preserved as alternative text. Supplemental artwork without a source description is marked decorative rather than assigned an invented description, and the accessibility summary discloses that limitation. Covers receive a generated description, scene breaks use separator semantics, decorative glyphs are hidden from assistive technology, and source quotations remain native blockquotes.

The project does not claim formal WCAG or EPUB Accessibility conformance without a separate evaluation, and it does not currently run EPUBCheck.

## Privacy and network behavior

The application has no accounts, analytics, telemetry, advertising, or generative-AI calls. It requests only the selected Waypoint article and raster artwork hosted on approved Halo and Waypoint domains. Generated EPUBs are written only to the chosen local destination.

The converter accepts HTTPS `halowaypoint.com/news/...` URLs, including locale-prefixed variants. Redirects and artwork downloads must remain on an approved HTTPS host. YouTube thumbnail assets are not fetched; the approved Waypoint hero artwork is reused in their place when available. On articles without a video thumbnail, hero artwork is placed once at the start of the story unless the same image already appears in the body. SVG and other active or unsupported image formats are rejected. Article pages are limited to 10 MB, individual images to 40 MB, articles to 100 images, and total artwork to 250 MB.

## Supported scope and limitations

- The curated catalog contains written Halo Waypoint Chronicles; Story Shards and audio-only releases are outside the supported catalog.
- Custom conversion is limited to public Halo Waypoint news articles with compatible page markup.
- The output format is EPUB; PDF export is not implemented.
- If Waypoint changes its markup, conversion stops with an article-body error instead of creating an empty book.
- Optional artwork failures are reported and skipped; an unavailable hero image produces a text-based cover.
- Site metadata and the catalog may require maintenance as new Chronicles are published.

## Development and contributing

Install the project in editable mode and run the unit tests from the repository root:

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. In particular, do not commit generated EPUBs, downloaded Waypoint pages, proprietary fonts, or other third-party assets.

Security vulnerabilities should be reported privately through GitHub as described in [SECURITY.md](SECURITY.md), not through a public issue.

## License

The original source code and project documentation are available under the [MIT License](LICENSE). That license does not cover Halo content or any other third-party material. Review [NOTICE.md](NOTICE.md) before redistributing the project or derivative builds.
