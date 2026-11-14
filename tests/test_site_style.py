"""Style checks for the public site pages. The rules live in STYLE.md."""

import re
from pathlib import Path

import pytest

from hamcall_db.reflectors import ATTRIBUTION_SUMMARY, MIXED_LICENSE, openapi_document

SITE = Path(__file__).resolve().parent.parent / "docs" / "site"

PAGES = sorted(p.relative_to(SITE).as_posix() for p in SITE.rglob("*.md"))

RESTRICTED = ("LoTW", "SOTA", "NMHH")
QUALIFIERS = (
    "excluded",
    "not published",
    "never published",
    "restricted",
    "--include-restricted",
)


def restricted_names_in(paragraph: str) -> list[str]:
    """Restricted source names present as whole words (so "Minnesota" is not SOTA)."""
    return [
        name
        for name in RESTRICTED
        if re.search(rf"\b{re.escape(name)}\b", paragraph, re.IGNORECASE)
    ]


def _text(page: str) -> str:
    return (SITE / page).read_text(encoding="utf-8")


@pytest.mark.parametrize("page", PAGES)
def test_site_page_has_no_em_dashes(page):
    lines = [
        str(n) for n, line in enumerate(_text(page).splitlines(), 1) if "—" in line
    ]
    assert not lines, f"{page}: em dash on line(s) {', '.join(lines)}"


@pytest.mark.parametrize("page", PAGES)
def test_restricted_sources_never_listed_as_published(page):
    for paragraph in re.split(r"\n\s*\n", _text(page)):
        lowered = paragraph.lower()
        for name in restricted_names_in(paragraph):
            assert any(q in lowered for q in QUALIFIERS), (
                f"{page}: {name} mentioned without an exclusion qualifier "
                f"in paragraph: {paragraph[:80]!r}"
            )


def test_restricted_source_match_ignores_substrings():
    assert restricted_names_in("Minnesota repeaters") == []
    assert restricted_names_in("SOTA summits") == ["SOTA"]


def _property_names(schema: object) -> set[str]:
    """Every property name declared anywhere inside an OpenAPI schema object."""
    names: set[str] = set()
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            names.add(name)
            names |= _property_names(sub)
        for key, sub in schema.items():
            if key != "properties":
                names |= _property_names(sub)
    elif isinstance(schema, list):
        for sub in schema:
            names |= _property_names(sub)
    return names


def test_api_page_documents_every_reflector_field():
    """reflectors/api.md names every field of the reflector schemas as `name`.

    Covers the entry (`Reflector` and every `Dial*` variant) and the envelopes it
    travels in (manifest, collections, talkgroup files). The AllStar schemas belong
    to a different page.
    """
    schemas = openapi_document()["components"]["schemas"]
    wanted = set()
    for name, schema in schemas.items():
        if not name.startswith("AllStar"):
            wanted |= _property_names(schema)
    page = _text("reflectors/api.md")
    missing = sorted(n for n in wanted if f"`{n}`" not in page)
    assert not missing, f"reflectors/api.md does not mention: {missing}"


def test_allstar_page_documents_every_field():
    """allstar.md names every field of the AllStar schemas as `name`."""
    schemas = openapi_document()["components"]["schemas"]
    wanted = _property_names(schemas["AllStarDirectory"]) | _property_names(
        schemas["AllStarNode"]
    )
    page = _text("allstar.md")
    missing = sorted(n for n in wanted if f"`{n}`" not in page)
    assert not missing, f"allstar.md does not mention: {missing}"


def _descriptions(node: object) -> list[str]:
    """Every "description" string anywhere in an OpenAPI document."""
    if isinstance(node, dict):
        found = [v for k, v in node.items() if k == "description" and isinstance(v, str)]
        for value in node.values():
            found += _descriptions(value)
        return found
    if isinstance(node, list):
        return [d for item in node for d in _descriptions(item)]
    return []


def test_openapi_descriptions_have_no_em_dashes():
    """openapi.json is served from the site, so STYLE.md's em dash ban applies.

    Exempt: the upstream attribution, quoted exactly as required, and the
    published `mixed` licence value, which is a field value clients match on.
    """
    offending = [
        d[:80]
        for d in _descriptions(openapi_document())
        if "—" in d.replace(ATTRIBUTION_SUMMARY, "").replace(MIXED_LICENSE, "")
    ]
    assert not offending, f"em dash in OpenAPI description(s): {offending}"
