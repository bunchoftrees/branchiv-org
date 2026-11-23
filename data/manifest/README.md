# Source manifest

`sources.toml` is the hand-curated list of every document allowed into the corpus.
Ingest reads only what is listed here, so the manifest is the one place that answers:
what is in the corpus, where did each document come from, and how much do we trust it?

## Top-level fields

| Field | Meaning |
|---|---|
| `snapshot_version` | Stamped on every document and chunk ingested from this manifest. Bump it whenever the set of sources changes or a source is re-fetched, so any answer can be traced back to the exact corpus it came from. |
| `as_of` | Date (`YYYY-MM-DD`) the statuses and notes were last checked. |

## Source fields

Each source is a `[[sources]]` block. Unknown field names are rejected, so a typo fails
validation instead of being silently ignored.

| Field | Required | Meaning |
|---|---|---|
| `doc_id` | yes | Stable slug (`lowercase-with-hyphens`), conventionally `<publisher>-<year>-<slug>`. It is the `documents` primary key and is cited in answers and eval labels, so never rename or reuse one. |
| `title` | yes | Human-readable title, shown in citations. |
| `publisher` | yes | Who issued it. |
| `source_type` | yes | What kind of document it is: `permit_notice`, `regulatory_record`, `press_release`, `public_notice`, `court_filing`, `testimony`, `legal_code`, `contract_record`, `contract_data`, `oversight_report`, `crs_report`, `gov_report`, `health_assessment`, `command_history`, `technical_paper`, `reference_data`, `video`, `news`, `advocacy`, `company`, `commentary`, `analysis`. |
| `tier` | yes | How much the source is trusted, 1 (most) to 4; see [Tiers](#tiers). |
| `published` | no | `"YYYY"`, `"YYYY-MM"` or `"YYYY-MM-DD"` (quoted), at whatever precision is known. Omit if unknown. The `documents` table gets the first day of that period, for recency. |
| `url` | see below | Canonical `http(s)` link that citations point to. |
| `local_path` | see below | Where the collected copy lives, e.g. under `data/sources/`. |
| `format` | when collected | File format of the collected copy: `pdf`, `html`, `markdown` or `text`. Picks the parser. Optional until the source is collected. |
| `status` | yes | `to_collect`, `collected`, `unavailable`, or `needs_original` (the link is a copy or a pointer; find the original record first). |
| `verified` | yes | `false` = reported but not yet matched to a primary record (the dossier's "Unverified"). |
| `safety_review` | yes | `true` = may contain material handling, chemistry or method detail. Every passage goes through the ingest-time safety filter **and** a human spot check. |
| `sha256` | no | Checksum of the collected file, so a changed source is caught rather than silently re-ingested. |
| `copy_of` | no | `doc_id` of the canonical record this source is a copy of. Must exist in the manifest and not be the source itself. Requires at least one caveat. |
| `caveats` | no | Labels for why the copy needs a warning; see [Caveats and copies](#caveats-and-copies). |
| `caveat_note` | no | Plain-language warning shown to users with every citation of this source. |
| `notes` | no | Anything ingest or a reviewer needs to know. Internal only, never shown to users. |

Location rules:

- `collected` needs a `url` or a `local_path`, and a `format`.
- `needs_original` needs the `url` of the copy it points at.
- `to_collect` and `unavailable` may have neither (some records are known but not yet
  linked, e.g. the 8th Circuit docket, which has to come from PACER).

The validator checks the manifest itself, not the filesystem: a `local_path` may point at a
file that has not been added yet (see `silvia-methodology` and the Reuters PDF). Ingest must
fail on a missing file.

## Caveats and copies

Sometimes the best available copy of a record is not the official one. Such a source stays
in the corpus but carries labels:

| Caveat | Meaning |
|---|---|
| `unofficial_copy` | Third-party upload, not checked against the original. |
| `archived_copy` | Web archive snapshot (e.g. the UNT End of Term archive, EPA's Jan 2021 snapshot). |
| `advocacy_host` | An official record hosted by an advocacy group. |
| `live_page` | Content changes over time; ingest saves a dated snapshot. |

Rules:

- When an answer cites a caveated source, it must show the source's `caveat_note` (or, if
  there is none, the caveat labels) next to the citation.
- A copy points at its canonical record with `copy_of` and needs at least one caveat. Once
  the canonical record is collected, the copy is superseded: cite the original and remove
  the copy from the corpus.

Example: `scribd-1984-shaneyfelt-complexities-of-lead-azide-copy` stands in for the DDESB
paper while DTIC public access is offline.

## Tiers

| Tier | What it is | Examples |
|---|---|---|
| 1 | Primary records | Court filings, EPA permits and dockets, Army releases, congressional records, FOIA responses |
| 2 | Official technical and historical | GAO, Inspector General and CRS reports, ATSDR assessments, command histories, Army technical papers |
| 3 | Secondary | News, contract aggregators, advocacy groups, company claims |
| 4 | Own analysis | Always labeled as analysis in answers, never presented as a record |

Full definitions: methodology.md, "How sources are rated".

## Deliberately excluded

- Wikipedia, "Lead(II) azide": tertiary and chemistry-heavy. Use its underlying sources instead.
- The source dossier and summary of findings: working documents built from the sources
  above. Answers should cite the records, not notes about them.

## Workflow

1. Add a `[[sources]]` block in the right section.
2. Run `uv run python -m branchiv_org.ingest.manifest` to validate it. It prints counts by
   tier and status and how many sources need safety review. Typos in field names,
   duplicate ids, bad tiers or dates and missing locations all fail with a clear message.
3. Bump `snapshot_version` (and `as_of` if you re-checked statuses) and commit. The test
   suite also validates this file, so CI catches a broken manifest.
