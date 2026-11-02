"""Generate ``rufzeichenliste_sample.pdf``, a tiny synthetic BNetzA Rufzeichenliste.

Run from the repo root to regenerate the checked-in fixture:

    uv run python tests/fixtures/bnetza/make_fixture.py

The layout copies the real list (edition of 23 October 2026): a title page carrying the
edition date, the publisher's notes, a table of contents with dot leaders, a "Seite N"
page number as the first line of some pages, a heading page or heading block per list,
and entries set in three columns that wrap across lines, columns and pages. Every
person, street and callsign holder in it is invented (the repository is public); only
the postcode/town pairs are real places.

The PDF is written by hand (no PDF library needed): Helvetica with WinAnsiEncoding, one
text object per column, so text extractors see the same column-by-column order as in
the real file. Each line ends with a space unless it ends with a hyphen, as in the real
file. Byte 0x81 in one line reproduces a real quirk: one entry in the 2026-10-23 list
carries a DOS code page 850 "ü" (0x81), which WinAnsi leaves undefined.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).with_name("rufzeichenliste_sample.pdf")

# Each page is a list of columns; each column is a list of lines (without the trailing
# space, which `_line` adds).
TITLE = [
    "______________________________________________________",
    "Verzeichnis der zugeteilten",
    "deutschen Amateurfunkrufzeichen",
    "und ihrer Inhaber (Rufzeichenliste)",
    "",
    "vom 23. Oktober 2026",
    "_______________________________________________________",
    "Bearbeitet und herausgegeben von der",
    "Bundesnetzagentur für Elektrizität, Gas,",
    "Telekommunikation, Post und Eisenbahnen",
]
NOTES = [
    "Hinweise des Herausgebers",
    "Diese Rufzeichenliste basiert auf § 6 Nr. 2 des Gesetzes über den Amateurfunk",
    "Bestimmungen des § 5 des Urheberrechtsgesetzes (UrhG).",
]
CONTENTS = [
    "Inhaltsverzeichnis Seite",
    "0 Hinweise zur Rufzeichenliste.........................................1",
    "1 Liste der personengebundenen Rufzeichen gemäß § 3 Abs. 1",
    "und Abs. 3 Nr. 1 des Amateurfunkgesetzes ...............................2",
    "2 Liste der Rufzeichen für die Durchführung von",
    "Ausbildungsfunkbetrieb ................................................. 5",
    "3 Liste der Rufzeichen mit 1- bis 3-buchstabigem Suffix für den",
    "Betrieb einer Amateurfunkstelle als Klubstation ......................... 6",
]
HINTS = [
    "Seite 1",
    "0 Hinweise zur Rufzeichenliste",
    "Liste Nr. Verwendungszwecke (Bedeutung und Rechtsgrundlagen)",
    "1 Personengebundene Rufzeichen gemäß § 3 Abs. 1 und Abs. 3 Nr. 1 AFuG.",
    "In den Fällen, in denen der Eintragung in die Rufzeichenliste widersprochen wurde,",
]
LIST1_HEADING = [
    "1 Liste der personengebundenen Rufzeichen gemäß § 3 Abs. 1 und Abs. 3 Nr. 1 des",
    "Amateurfunkgesetzes",
    "",
]
LIST1_PAGE_A = [
    [
        "Seite 3",
        "DA1AA, A, Erika Mustermann; Musterstr.",
        "12, 38100 Braunschweig",
        "DA1AB, A, Dr. Max Beispiel",
        "DA1AC, E, Jürgen Größer; Hauptstraße 5,",
        "80331 München",
        "DB1XY, A, Hans-Peter von der Heide; Am",
        "Weiher 3 b, 36456 Barchfeld-",
        "Immelborn, Bahnhofstr. 2, 36433 Bad",
        "Salzungen",
    ],
    [
        "DC1AB, N, Ács Zoltán",
        "DD1AA, A, Klaus Komma, Kommaweg 7,",
        "10115 Berlin",
        "DF1ZZ, A, Ulrich Otto; Testweg 8 a,",
        "81475 M\x81nchen",
        "DG1AA, E, Pieter Ausland; Teststraat 1,",
        "2628 AB Delft",
        "DG1AB, A, Sepp Wiener; Testgasse 2, 1100",
        "Wien",
    ],
    [
        "DH1AB, A, Otto Echo; Lindenallee 9, 01067",
        "Lindenallee 9",
        "DJ1AA, A, Fritz Doppelt; Feldweg 1, 99084",
        "Erfurt, Feldweg 1, 99084 Erfurt; Bergstr. 2,",
        "98693 Ilmenau",
        "DJ1AA, A, Fritz Doppelt; Feldweg 1, 99084",
        "Erfurt",
        "DK1AA, A, Prof. Dr. Karl Theodor zu",
        "Testberg-Müller",
        "DL1ZZ, A, Gerda Seitenwechsel;",
    ],
]
LIST1_PAGE_B = [
    [
        "Birkenweg 7, 04109 Leipzig",
        "DO1AA, E, Lisa Klein",
        "DO1AB, E, Dr. Hans Eins,; Zweiweg 2, 01309",
        "Dresden",
    ],
]
LIST2 = [
    [
        "2 Liste der Rufzeichen für die Durchführung von Ausbildungsfunkbetrieb",
        "",
        "DN1AA, A, Otto Ausbilder; Schulweg 1, 73430",
        "Aalen",
        "DN1AB, E, Berta Lehrer",
    ],
]
LIST3 = [
    [
        "3 Liste der Rufzeichen mit 1- bis 3-buchstabigem Suffix für den Betrieb einer",
        "Amateurfunkstelle als Klubstation",
        "",
        "DL0AA, A, Karl Klub; Vereinsweg 1, 59302",
        "Oelde, Klubheim, Sportplatz 2, 59269",
        "Beckum",
    ],
]
LIST4 = [
    [
        "Seite 7",
        "4 Liste der Rufzeichen mit 4- bis 7-stelligem Suffix für den Betrieb einer",
        "Amateurfunkstelle zu besonderen Anlässen als Klubstation",
        "",
        "DA100XYZ, A, Jan Jubiläum; Festweg 100,",
        "25421 Pinneberg",
    ],
]
LIST5 = [
    [
        "5 Liste der Rufzeichen für den Betrieb einer Amateurfunkstelle als",
        "Relaisfunkstelle/Funkbake",
        "",
        "DB0XYZ, A, Rolf Relais, Wasserturm,",
        "Turmweg 3, 4617 Starkenberg",
        "DP0XYZ, A, Sven Satellit, Satellit TESTSAT,",
        "Orbit (LEO)",
    ],
]
LIST6 = [
    [
        "6 Liste der Rufzeichen für besondere experimentelle Studien mit einer",
        "Amateurfunkstelle",
        "",
        "DA4ZZZ, E, Ina Experiment; Laborweg 5,",
        "97074 Würzburg",
    ],
]

PAGES: list[list[list[str]]] = [
    [TITLE],
    [NOTES],
    [CONTENTS],
    [HINTS],
    [LIST1_HEADING],
    LIST1_PAGE_A,
    LIST1_PAGE_B,
    LIST2,
    LIST3,
    LIST4,
    LIST5,
    LIST6,
]

_COLUMN_X = (40, 220, 400)
_TOP_Y = 800
_LEADING = 11


def _line(text: str) -> str:
    """A text line as the real PDF sets it: trailing space unless it ends in a hyphen."""
    return text if text.endswith("-") else f"{text} "


def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252", errors="strict") if "\x81" not in text else _with_0x81(text)
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _with_0x81(text: str) -> bytes:
    head, _, tail = text.partition("\x81")
    return head.encode("cp1252") + b"\x81" + tail.encode("cp1252")


def _content(columns: list[list[str]]) -> bytes:
    out = [b"BT /F1 8 Tf"]
    for x, lines in zip(_COLUMN_X, columns, strict=False):
        out.append(f"1 0 0 1 {x} {_TOP_Y} Tm".encode())
        for i, line in enumerate(lines):
            if i:
                out.append(f"0 -{_LEADING} Td".encode())
            out.append(_pdf_string(_line(line) if line else " ") + b" Tj")
    out.append(b"ET")
    return b"\n".join(out) + b"\n"


def build_pdf(pages: list[list[list[str]]]) -> bytes:
    """Assemble a minimal, uncompressed PDF 1.4 with one page per entry of `pages`."""
    n = len(pages)
    # Object numbers: 1 catalog, 2 pages, 3 font, then (page, content) pairs.
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(n))
        + f"] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, columns in enumerate(pages):
        content = _content(columns)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream")

    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(body)


if __name__ == "__main__":
    OUT.write_bytes(build_pdf(PAGES))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
