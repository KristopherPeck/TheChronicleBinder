# The Librarian Assistant

The Librarian Assistant turns a Halo Waypoint Chronicle into a self-contained EPUB. It keeps the story text, emphasis, scene breaks, links, and article artwork, then applies a book design inspired by the official Waypoint Chronicle PDFs.

It uses only Python's standard library. No browser automation, Node, Docker, Calibre, or external Python packages are required.

## Quick start (Windows)

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
2. Double-click `run_gui.bat`.
3. Paste a Halo Waypoint article URL, choose where to save it, and select **Create EPUB**.

The first launch does not install anything. The batch file runs the application directly from this folder.

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

For development, an editable install also exposes the `librarian-assistant` command:

```powershell
python -m pip install -e .
librarian-assistant URL
```

## What it creates

- EPUB 3 output with EPUB 2 navigation fallback for older readers
- A full-bleed artwork cover with the story title and Waypoint Chronicle treatment, plus a raster thumbnail fallback for older readers
- Title and credits pages
- A restrained cream-paper reading theme with blue Waypoint-style accents
- Embedded article images, links, italics, bold text, headings, lists, and scene breaks
- Source URL and conversion date in the book metadata
- Recoverable artwork downloads, bounded file sizes, and cancellation from the desktop interface

Reader apps can override fonts, colors, margins, and line spacing. The design intentionally uses common built-in serif and sans-serif fonts so it remains portable and readable.

## Scope and troubleshooting

The extractor is intentionally limited to public `halowaypoint.com/news/...` pages. This avoids accidentally packaging navigation, ads, or unrelated pages. It does not bypass logins, paywalls, or access controls.

Article pages are limited to 10 MB and each image to 40 MB. If optional artwork cannot be downloaded, the converter warns and continues; if the hero image is unavailable, it creates a text-based cover.

If Waypoint changes its page markup, the application will stop with a clear “article body not found” message instead of creating an empty book. Open an issue with the article URL and the page's saved HTML when that happens.

The downloaded story and artwork remain subject to their respective copyrights. This tool is intended for personal archival and reading; do not redistribute generated books without permission.

