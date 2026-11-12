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
# MILE, HOUSE, RANCH, PALMS, and CITY. TRAIL is listed even though Trail, BC exists:
# the word only counts after a leading house number ("12 Pine Trail"), and the town
# name never carries one.
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
        "LOOP",
        "PARKWAY",
        "PKWY",
        "PIKE",
        "PL",
        "PLACE",
        "RANG",
        "RD",
        "ROAD",
        "ROUTE",
        "RTE",
        "RUE",
        "RUN",
        "ST",
        "STREET",
        "TER",
        "TERRACE",
        "TRAIL",
        "TRL",
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


def location_has_street_address(value: str | None) -> bool:
    """True when a free-text location holds a street address anywhere in it.

    A location such as an AllStar node's often lists town, state and then the street:
    "Anytown, IA, 1431 Example St". The house number is not at the start of the whole
    value, so `looks_like_street_address` alone misses it. This checks the whole value
    and then each comma-separated part on its own.

    Two differences from the per-field backstop, both specific to a free-text location:

    * A part that is only digits is a postcode beside a town ("Anytown, IA, 50000"),
      not an address, so it is not flagged on its own. (A whole value that is only
      digits still is.)
    * "AV" counts as a street-type word after a house number ("12 Example Av"). The
      shared list leaves it out because Norwegian club names use "av" ("of"); a
      location is not a club name, and blanking one is the cheap mistake.
    * A part that starts with a 1-4 digit house number followed by a word is flagged
      even with no street-type word ("12 Example Strete", "7 Example"), unless the
      word is a radio or battery unit ("220 MHz", "000 mah"). This over-blocks some
      numbered place names ("108 Mile Ranch"); the location is only blanked.
    """
    if not value:
        return False
    if looks_like_street_address(value):
        return True
    return any(_part_is_address(part.strip()) for part in value.split(","))


def _part_is_address(part: str) -> bool:
    if not part or _ONLY_DIGITS.match(part):
        return False
    if looks_like_street_address(part):
        return True
    words = _words(part)
    if len(words) >= 2 and _HOUSE_NUMBER.match(words[0]) and "AV" in words[1:]:
        return True
    return _leads_with_house_number(part)


# A location part that starts with a short house number and then a word ("12 Example
# Strete", "7 Example") is treated as an address even without a street-type word:
# filers misspell the type or leave it out. 1-4 digits, optionally one letter, a
# range ("12B", "12-14"), or a sub-number ("99/3", as Thai addresses write it). Five
# or more digits is a postcode ("75001 Paris"); a number with a decimal point is a
# frequency ("443.825 Repeater") and never matches, because the token must end in
# whitespace. A number glued to "m" is a band ("2m Sampleton").
_LEADING_HOUSE_NUMBER = re.compile(
    r"^(\d{1,4}(?:[A-Za-z]|-\d{1,4}[A-Za-z]?|/\d{1,4})?)\s+([A-Za-z]+)"
)

# Words after a number that make it a radio setting, a band, a battery size or a
# distance ("10 Miles N. of Sampleton", "108 Mile Ranch").
_UNIT_WORDS = frozenset(
    {
        "MHZ",
        "KHZ",
        "GHZ",
        "HZ",
        "PL",
        "MAH",
        "DB",
        "W",
        "WATT",
        "WATTS",
        "M",
        "CM",
        "METER",
        "METERS",
        "METRE",
        "METRES",
        "CTCSS",
        "DCS",
        "MILE",
        "MILES",
        "MI",
        "KM",
        "KILO",
        "KILOMETERS",
        "KILOMETRES",
    }
)


def _leads_with_house_number(part: str) -> bool:
    match = _LEADING_HOUSE_NUMBER.match(part)
    if not match:
        return False
    number, word = match.group(1), match.group(2)
    if number[-1] in "mM":  # a band ("2m Sampleton", "6M FM Hub")
        return False
    return word.upper() not in _UNIT_WORDS


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
