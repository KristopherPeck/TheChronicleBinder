"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import ConversionError, convert_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert a Halo Waypoint Chronicle into a polished EPUB.")
    parser.add_argument("url", nargs="?", help="Public halowaypoint.com/news article URL")
    parser.add_argument("-o", "--output", type=Path, help="Destination .epub path")
    parser.add_argument("--title", help="Override the detected book title")
    parser.add_argument("--author", help="Override the detected author")
    parser.add_argument(
        "--style",
        choices=("waypoint", "classic"),
        default="waypoint",
        help="Reading style: official-inspired color or classic black and white",
    )
    parser.add_argument("--force", action="store_true", help="Replace the destination if it already exists")
    parser.add_argument("--gui", action="store_true", help="Open the desktop interface")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.gui or not args.url:
        from .gui import launch

        launch()
        return 0
    try:
        result = convert_url(
            args.url,
            args.output,
            title=args.title,
            author=args.author,
            reading_style=args.style,
            overwrite=args.force,
            progress=lambda message: print(message, file=sys.stderr),
        )
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(result.output_path)
    return 0

