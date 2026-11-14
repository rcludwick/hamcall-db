# Writing style for the hamcall-db site

This file is binding on every page in `docs/site/`. It applies to human
contributors and to any AI assistant editing the site. The method and the
language rules come from `STYLE.md` in the how-ambe-works project,
retargeted here to a data pipeline.

## 1. Method

Start from something the reader can see or do: a callsign they know, a
file they can download, a query they can run. Explain one step at a time,
and define each term when it first appears.

Where a page explains a design decision, show the obvious alternative
first and let it fail. This applies to three decisions:

- 4-character grids. Publishing a 6-character subsquare would place a
  licensee within a few kilometres of a home.
- Dropping street addresses. Keeping them would make the dataset a list of
  homes.
- Separate licences for the reflector data and the callsign dataset.
  Their upstream terms differ, so one licence cannot cover both.

Everywhere else, state the fact and move on. Not every page has a
decision to defend.

A good test: could an attentive reader, stopped mid-section, predict the
next paragraph? If yes, the order is right.

## 2. Language

Write technical English. Dry is acceptable. Precise and slightly flat
beats energetic and vague.

**Punctuation**

- Do not use em dashes. This is the most common tell and it is banned
  outright. Use a period or a comma instead. A colon or parentheses also
  work. If a sentence seems to need an em dash, it is usually two
  sentences.
  The one exception: a required upstream attribution line is quoted
  exactly even when it contains an em dash. Write it as `&mdash;` in
  Markdown, or as the escape `\u2014` inside a JSON example, so the page
  itself still holds no em dash character.
- Prefer short sentences. Break long ones.
- Use ordinary lists rather than sentences that contain three parallel
  clauses.

**Banned constructions**

These read as machine-generated. Do not use them:

- "It's worth noting that", "It's important to understand", "Note that"
  as a paragraph opener
- "delve", "leverage", "seamless", "robust", "crucial", "profound",
  "elegant", "powerful", "remarkable"
- "landscape", "tapestry", "realm", "journey", "world of" used as
  metaphor
- "not just X, but Y" and "isn't merely X, it's Y"
- "In today's...", "At its core", "Simply put", "In essence"
- Rhetorical triples used for rhythm rather than content
- Sentences that summarise what the previous paragraph just said
- Closing paragraphs that restate the section with no new information

**Scaffolding, which is the subtler tell**

These are harder to grep for:

- Announcing significance instead of showing it: "What matters most is
  the breadth", "The key insight here", "This is the important part".
  Delete the announcement and keep the content.
- Telling the reader what they already think: "You may be
  wondering...". Cut.
- Rhetorical flourishes at the end of a sentence that add nothing.
- Self-assessment of the page's own worth. A page does not review
  itself.
- Defensive lists of things that were not done, where no one has accused
  you of doing them. If a paragraph exists to establish innocence rather
  than to inform, delete it.
- Restating a caution that is already in a warning box a few paragraphs
  above.

**Length**

Short beats complete. When a sentence and a paragraph say the same thing,
keep the sentence. A three-line warning that a reader actually reads
beats a three-paragraph warning they skip.

**Person and voice**

The site has one author. Write "the author" rather than "we", and do not
adopt an institutional voice the project does not have. Use "you" when
guiding the reader through a query or a file, and plain third person for
mechanism.

**Metaphor**

Allowed when a specific property transfers from the known thing to the
unknown one. If you cannot say which property transfers, cut it.

## 3. Tone about what is and is not known

State what is known plainly, without hedging. State what is unknown as an
open question with a path to answering it.

Do not apologise for gaps. Delete phrasing of this shape:

- "necessarily incomplete"
- "we cannot establish"
- "this may not be accurate"
- "unfortunately, the upstream data does not..."

Replace it with a direct statement and a status. Not "unfortunately the
regulator does not tell us the grid", but "ISED publishes no grid. The
build geocodes the postal code and truncates to 4 characters."

An open question is a piece of work someone has not done yet. Present it
that way.

This does not apply to the licence and attribution notices. Keep them
verbatim and prominent.

## 4. Sourcing

Every substantive claim names its source inline, in the sentence or the
one after it. Four kinds are permitted:

- an upstream URL (a regulator's download page, a specification)
- a `NOTICE` source number
- a repo code path, such as `hamcall_db/sources/fcc.py`
- a value read from the published files, with its date

If a claim has none of those, it does not go on the site.

## 5. Counts and dates

- Never give an undated count. "About 800,000 records" is not allowed.
  "812,345 rows in `hamcall-db-2026-11-13.parquet`" is. The count is read
  from the published files, and the date travels with it.
- Pages that carry counts or status carry a "last reviewed" date.

## 6. Restricted sources and private data

- Restricted sources (LoTW, SOTA, NMHH) are only ever described as
  excluded, restricted, or not published. Never write a sentence that
  lists one as part of the published dataset. A test enforces this
  (`tests/test_site_style.py`).
- Never publish street addresses, 6-character grids, secrets, tokens or
  hostnames of private infrastructure on the site, in examples or in
  screenshots.

## 7. Checklist before committing a page

- [ ] No em dashes anywhere in the file
- [ ] No banned constructions from section 2
- [ ] Opens with something concrete, not a definition
- [ ] Naive approach shown failing, if the page explains a design decision
- [ ] Every claim carries a source (section 4)
- [ ] Every count carries its date (section 5)
- [ ] No apologetic hedging about gaps
- [ ] LoTW, SOTA and NMHH appear only as excluded or restricted
- [ ] No street addresses, 6-character grids or secrets
- [ ] "Last reviewed" date current
- [ ] `uv run pytest tests/test_site_style.py` passes
