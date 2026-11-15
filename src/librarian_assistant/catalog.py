"""Known Halo Waypoint Chronicle articles and their publication metadata."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


DEFAULT_AUTHORS = ("Alex Wakeford", "Jeff Easterling")
PRIMARY_SERIES = "Halo Waypoint Chronicles"


@dataclass(frozen=True)
class SeriesMembership:
    name: str
    position: int


@dataclass(frozen=True)
class ChronicleRelation:
    label: str
    title: str
    url: str


@dataclass(frozen=True)
class Chronicle:
    title: str
    url: str
    published: str
    series_position: int
    authors: tuple[str, ...] = DEFAULT_AUTHORS
    additional_series: tuple[SeriesMembership, ...] = ()
    relations: tuple[ChronicleRelation, ...] = ()


KNOWN_CHRONICLES = (
    Chronicle("Halo: Veritas Aeterna", "https://www.halowaypoint.com/news/halo-veritas-aeterna", "2026-11-15", 28),
    Chronicle(
        "Halo: Foehammer\u2019s Last Flight",
        "https://www.halowaypoint.com/news/halo-foehammers-last-flight",
        "2026-11-09",
        27,
        ("Kemi Ashing-Giwa",),
    ),
    Chronicle(
        "Halo: Elegy",
        "https://www.halowaypoint.com/news/halo-elegy",
        "2026-11-02",
        26,
        ("Jeremy Patenaude",),
    ),
    # Still Midnight is intentionally omitted because its article contains audiobook links, not the story text.
    Chronicle("Halo: Fifth Canticle", "https://www.halowaypoint.com/news/halo-fifth-canticle", "2025-10-27", 22),
    Chronicle(
        "Halo: Ascension on Atropos",
        "https://www.halowaypoint.com/news/halo-ascension-on-atropos",
        "2025-10-08",
        21,
        relations=(
            ChronicleRelation(
                "Sequel to",
                "Halo: Saturn Devouring His Son",
                "https://www.halowaypoint.com/news/halo-saturn-devouring-his-son",
            ),
        ),
    ),
    Chronicle("Halo: \u2018Ghosts & Glass\u2019 by B. Giraud", "https://www.halowaypoint.com/news/halo-ghosts-glass-by-b-giraud", "2025-09-14", 20),
    Chronicle("Halo: Moonrise Over Mombasa", "https://www.halowaypoint.com/news/halo-moonrise-over-mombasa", "2025-07-10", 19),
    Chronicle(
        "Halo: Battle for the Academy \u2013 Part 2",
        "https://www.halowaypoint.com/news/halo-battle-for-the-academy-part-2",
        "2025-06-06",
        18,
        additional_series=(SeriesMembership("Battle for the Academy", 2),),
    ),
    Chronicle(
        "Halo: Battle for the Academy \u2013 Part 1",
        "https://www.halowaypoint.com/news/halo-battle-for-the-academy-part-1",
        "2025-05-14",
        17,
        additional_series=(SeriesMembership("Battle for the Academy", 1),),
    ),
    Chronicle("Halo: Tulpamancy", "https://www.halowaypoint.com/news/halo-tulpamancy", "2025-04-10", 16),
    Chronicle("Halo: From the Soil to the Stars", "https://www.halowaypoint.com/news/halo-from-the-soil-to-the-stars", "2025-03-12", 15),
    Chronicle("Halo: Whispers From the Pyre", "https://www.halowaypoint.com/news/halo-whispers-from-the-pyre", "2024-12-18", 14),
    Chronicle("Halo: Age of Retribution", "https://www.halowaypoint.com/news/halo-age-of-retribution", "2024-11-08", 13),
    Chronicle("Halo: Venezian Sonata", "https://www.halowaypoint.com/news/halo-venezian-sonata", "2024-09-23", 12),
    Chronicle("Halo: The Machine Breaks", "https://www.halowaypoint.com/news/halo-the-machine-breaks", "2024-09-05", 11),
    Chronicle("Halo: Anvil Accord", "https://www.halowaypoint.com/news/halo-anvil-accord", "2024-07-22", 10),
    Chronicle("Halo: The Third Life", "https://www.halowaypoint.com/news/halo-the-third-life", "2024-05-08", 9),
    Chronicle("Halo: Fireside", "https://www.halowaypoint.com/news/halo-fireside", "2024-02-21", 8),
    Chronicle("Halo: Hippocratica", "https://www.halowaypoint.com/news/halo-hippocratica", "2024-02-05", 7),
    Chronicle("Halo: Trial of Reckoning", "https://www.halowaypoint.com/news/halo-trial-of-reckoning", "2023-12-06", 6),
    Chronicle("Halo: Battle for the Blood-Moon", "https://www.halowaypoint.com/news/halo-battle-for-the-blood-moon", "2023-11-27", 5),
    Chronicle(
        "Halo: Saturn Devouring His Son",
        "https://www.halowaypoint.com/news/halo-saturn-devouring-his-son",
        "2023-10-31",
        4,
        relations=(
            ChronicleRelation(
                "Followed by",
                "Halo: Ascension on Atropos",
                "https://www.halowaypoint.com/news/halo-ascension-on-atropos",
            ),
        ),
    ),
    Chronicle("Halo: Sunrise on Sanghelios", "https://www.halowaypoint.com/news/halo-sunrise-on-sanghelios", "2023-10-14", 3),
    Chronicle("Halo: Winter Contention", "https://www.halowaypoint.com/news/halo-winter-contention", "2023-02-08", 2),
    Chronicle("Halo 4 | VERTICAL UMBRAGE", "https://www.halowaypoint.com/news/halo-4-vertical-umbrage", "2022-11-07", 1),
)


_CHRONICLES_BY_SLUG = {
    urlsplit(chronicle.url).path.rstrip("/").rsplit("/", 1)[-1].lower(): chronicle
    for chronicle in KNOWN_CHRONICLES
}


def chronicle_for_url(url: str) -> Chronicle | None:
    """Return catalog metadata for a canonical or locale-prefixed Waypoint URL."""

    parsed = urlsplit(url)
    if parsed.hostname is None or parsed.hostname.lower().removeprefix("www.") != "halowaypoint.com":
        return None
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1].lower()
    return _CHRONICLES_BY_SLUG.get(slug)
