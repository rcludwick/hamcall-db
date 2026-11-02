"""Street-address guard: keep mailing addresses out of published fields.

The redistribution contract says no street address ever reaches the published
artifact (CLAUDE.md, mem-c6e0). Importers never copy the street column into a
`Record`, but some filers type their street address into another field: FCC rows
with ``city = "5 Example Rd"``, an ISED club whose club name is the trustee's
address. Those values ride into a published column unless something removes them.

Two layers do that, both pure functions in this module:

1. `echoes_street` (importer layer, precise). An importer that has the row's street
   address at parse time compares each published field against it and drops the
   field when it is a copy of the address. The street value is only compared, never
   stored.
2. `looks_like_street_address` (normalize layer, backstop). The merge stage drops
   any city or name that has the shape of an address, whatever the source: a leading
   house number and a street-type word such as ``Rd`` or ``Rue``. Place names with a
   leading number (``29 Palms``, ``100 Mile House``) have no street-type word, so
   they survive.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable

# Anything that isn't a letter, digit, or '#' separates words for matching purposes,
# so "Ct." and "Ct" compare equal and "Rd," doesn't hide the "Rd".
_SEPARATORS = re.compile(r"[^0-9A-Z#]+")

# A street column value only counts as an address when it has a digit and at least
# three letters ("5 Pond Rd", "PO Box 12"). Rural filers often type their town into
# the street column ("Pawleys Island" in both street and city); that is not an
# address, and the matching city must survive.
_MIN_STREET_LETTERS = 3
_MIN_STREET_LENGTH = 6

# A published value that is a *prefix* of the street, at least this long, is a
# truncated copy: FCC's city column is 20 characters wide, so a long street typed
# there arrives cut off ("4000 Sample Water Co"). Shorter prefixes are not trusted,
# because a real place name can begin a street name ("29 Palms" / "29 Palms Hwy").
_MIN_TRUNCATED_COPY = 16

# A house number: digits, optionally a letter suffix ("233B"), an ordinal
# ("292ND"), or a range ("12-34").
_HOUSE_NUMBER = re.compile(r"^\d+(?:[A-Z]|ST|ND|RD|TH)?(?:-\d+[A-Z]?)?$")

# Street-type words (USPS suffix abbreviations plus Canadian English and French).
# Deliberately absent: words that also end real place names after a number, such as
# MILE, HOUSE, RANCH, PALMS, CITY, and TRAIL (Trail, BC).
STREET_TYPES: frozenset[str] = frozenset(
    {
        # Not "AV": in Norwegian (and Swedish) "av" means "of", and club names like
        # "1. Kolbotn gruppe av Norges Speiderforbund" would be blanked. AVE/AVENUE stay.
        "AVE",
        "AVENUE",
        "BLVD",
        "BOUL",
        "BOULEVARD",
        "CH",
        "CHEMIN",
        "CIR",
        "CIRCLE",
        "COURT",
        "COV",
        "COVE",
        "CRES",
        "CRESCENT",
        "CT",
        "DR",
        "DRIVE",
        "HWY",
        "HIGHWAY",
        "LANE",
        "LN",
        "PARKWAY",
        "PKWY",
        "PL",
        "PLACE",
        "RANG",
        "RD",
        "ROAD",
        "RTE",
        "RUE",
        "ST",
        "STREET",
        "TER",
        "TERRACE",
        "WAY",
    }
)


def _words(value: str) -> list[str]:
    return _SEPARATORS.sub(" ", value.upper()).split()


def _is_address_shaped(street: str) -> bool:
    letters = sum(c.isalpha() for c in street)
    return (
        len(street) >= _MIN_STREET_LENGTH
        and letters >= _MIN_STREET_LETTERS
        and any(c.isdigit() for c in street)
    )


def echoes_street(value: str | None, streets: Iterable[str | None]) -> bool:
    """True when `value` is a copy of one of the row's street addresses.

    A copy is the same words as the street (case and punctuation ignored), a longer
    value that contains the street as whole words ("5 Sample Ct. Somewhere"), or a
    long-enough prefix of it (a street truncated by a narrower column). Street values
    that aren't address-shaped (no digit, or under three letters) are ignored.
    """
    if not value:
        return False
    target = " ".join(_words(value))
    if not target:
        return False
    for street in streets:
        if not street:
            continue
        candidate = " ".join(_words(street))
        if not _is_address_shaped(candidate):
            continue
        if f" {candidate} " in f" {target} ":
            return True
        if len(target) >= _MIN_TRUNCATED_COPY and candidate.startswith(target):
            return True
    return False


# European and Latin American addresses put the number AFTER the street: "Waldstr. 35 A",
# "Storgata 10B", "ul. Przykładowa 5", "Calle Falsa 123". A street word (as a compound
# suffix or a standalone word) together with a number token marks one. Checked on the raw
# string, because _words() splits on non-ASCII letters (ß, ø, ł).
_STREET_SUFFIX = re.compile(
    r"\w*(?:str\.|straße|strasse|gasse|allee|veien|vegen|gata|gatan|vägen)(?=\W|$)",
    re.IGNORECASE,
)
_STREET_WORD = re.compile(
    r"(?:^|\s)(?:ul\.|al\.|os\.|calle|avenida|av\.|rua|travessa|rodovia|vei|gate|"
    r"weg|platz|straat|laan|via|viale|piazza)(?=\s|$)",
    re.IGNORECASE,
)
_NUMBER_TOKEN = re.compile(r"(?:^|\s)\d+\s?[a-z]?(?=\W|$)", re.IGNORECASE)
# Lot / block / flat numbers ("Barrio San Ramon Lote 245", "Manzana 4 Casa 12").
_PLOT = re.compile(
    r"\b(?:lote|manzana|mza?|casa|depto|dpto|piso|apto|apartamento|bloque|torre)\.?\s*\d",
    re.IGNORECASE,
)
# A bare number (a postcode or house number typed into a name or town field).
_ONLY_DIGITS = re.compile(r"^\s*\d[\d\s-]*$")


def looks_like_street_address(value: str | None) -> bool:
    """True when `value` has the shape of a street address.

    Three shapes are flagged:

    * North American: a leading house number followed, somewhere later, by a street-type
      word: "100 Fake Rd", "12345 Nowhere Pl NW", "500 Sample Ave #101", "1200 rue
      Fictive". Values without a street-type word ("29 Palms", "100 Mile House") and
      values without a leading number ("Six Mile", "St. Louis") are not flagged.
    * European / Latin American: a street word plus a number, in either order:
      "Waldstr. 35 A", "Storgata 10B", "ul. Przykładowa 5", "Calle Falsa 123". Place
      names that merely contain a number ("9 de Julio", "Comuna 1") have no street word.
    * Lot, block or flat numbers ("Lote 245", "Manzana 4"), and values that are only
      digits (a postcode or house number in the wrong field).
    """
    if not value:
        return False
    if _ONLY_DIGITS.match(value) or _PLOT.search(value):
        return True
    if _NUMBER_TOKEN.search(value) and (_STREET_SUFFIX.search(value) or _STREET_WORD.search(value)):
        return True
    words = _words(value)
    if len(words) < 2 or not _HOUSE_NUMBER.match(words[0]):
        return False
    return any(word in STREET_TYPES for word in words[1:])


def warn_scrubbed(source: str, count: int) -> None:
    """Print the importer-layer scrub count to the build log (stderr), when nonzero.

    Importers own their I/O, so they report here; the merge stage stays pure and
    returns its own count through `MergeStats` instead.
    """
    if count:
        print(
            f"WARNING: {source}: dropped {count} published field value(s) that copied "
            "the row's street address",
            file=sys.stderr,
        )
