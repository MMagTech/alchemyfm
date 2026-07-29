"""UI accent ids shared by API validation and the web client.

A theme used to be a whole hand-built palette (22 of them, each redefining
every colour token). It is now just an *accent*: the neutral base comes from
light/dark mode, and everything accent-derived is color-mix()'d from this one
value in the stylesheet.

Light vs dark is not stored here — it follows the listener's operating system
unless they override it in their own browser.
"""

DEFAULT_THEME = "amber"

VALID_THEMES: frozenset[str] = frozenset(
    {
        "amber",
        "violet",
        "blue",
        "emerald",
        "rose",
        "cyan",
    }
)

# Installs from before the accent model stored one of the old 22 palettes.
# Map each to its nearest accent so upgrading keeps a familiar colour instead
# of snapping everyone to the default.
LEGACY_THEME_MAP: dict[str, str] = {
    "gold": "amber",
    "sunset": "amber",
    "citrus": "amber",
    "copper": "amber",
    "ember": "amber",
    "mocha": "amber",
    "cyan": "cyan",
    "ocean": "cyan",
    "arctic": "cyan",
    "midnight": "blue",
    "steel": "blue",
    "emerald": "emerald",
    "sage": "emerald",
    "forest": "emerald",
    "rose": "rose",
    "crimson": "rose",
    "wine": "rose",
    "berry": "rose",
    "violet": "violet",
    "lavender": "violet",
    "orchid": "violet",
    "graphite": "violet",
}


def normalize_theme(value: str | None) -> str:
    """Coerce any stored or supplied theme id to a currently valid accent."""
    if not value:
        return DEFAULT_THEME
    if value in VALID_THEMES:
        return value
    return LEGACY_THEME_MAP.get(value, DEFAULT_THEME)
