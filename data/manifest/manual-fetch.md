# Sources to fetch by hand

Generated after the collector run of 2026-11-23. 95 of 116 sources were downloaded and recorded in `collected.toml`; the 21 below were not. (The 199 MB 2018 RCRA response to comments was fetched on a second run with a 250 MB cap.)

To add a file you downloaded yourself:

```bash
uv run python -m branchiv_org.ingest.collect import <doc_id> <path-to-file>
```

It copies the file into `data/raw/<doc_id>/`, detects the format (PDF, HTML or text), records the checksum and marks the entry `method = "manual"` in `collected.toml`. Your original file is left where it is.

## Blocked (HTTP 403 or connection refused) (4)

The site refuses automated downloads. Open the link in a browser and save the file.

- **Operable Unit 11 Proposed Plan (final, Apr 2026)** (`army-2026-ou11-proposed-plan`)
  - Link: https://iaaaprestoration.com/wp-content/uploads/11005-PP-OU11_Proposed_Plan_Final-APR2026.pdf
  - Why: HTTP 403 (iaaaprestoration.com)
  - Import: `uv run python -m branchiv_org.ingest.collect import army-2026-ou11-proposed-plan ~/Downloads/<file>`
- **CC-06 supplemental remedial investigation report** (`army-cc06-remedial-investigation`)
  - Link: https://iaaaprestoration.com/wp-content/uploads/12003-CC-06_SRI_Final_e-deliverable.pdf
  - Why: HTTP 403 (iaaaprestoration.com)
  - Import: `uv run python -m branchiv_org.ingest.collect import army-cc06-remedial-investigation ~/Downloads/<file>`
- **Fourth Five-Year Review (2021), addendum (May 2025)** (`army-2025-five-year-review-addendum`)
  - Link: https://iaaaprestoration.com/wp-content/uploads/721-5YR-FINAL-2021-Addendum-MAY2025.pdf
  - Why: HTTP 403 (iaaaprestoration.com)
  - Import: `uv run python -m branchiv_org.ingest.collect import army-2025-five-year-review-addendum ~/Downloads/<file>`
- **American Ordnance LLC company website** (`aollc-company-site`)
  - Link: https://aollc.biz/
  - Why: server closed the connection without responding
  - Import: `uv run python -m branchiv_org.ingest.collect import aollc-company-site ~/Downloads/<file>`

## Moved or missing (HTTP 404 or redirected away) (4)

The link no longer points at the document. Find the current location, update `url` in sources.toml, then import or re-run the collector for that doc_id.

- **Emergency permit #1: storage of lead azide in place (Apr 2 to Jun 30, 2026)** (`epa-2026-lead-azide-emergency-permit-1`)
  - Link: https://www.epa.gov/node/299851
  - Why: HTTP 404. Permit #3's old node link now redirects to epa.gov/ia/iowa-army-ammunition-plant-middletown-iowa-2 and permit #4 is at ...-iowa-3, so #1 may be at a similar address (not checked). The EPA on-scene coordinator site's profile page does not link to it.
  - Import: `uv run python -m branchiv_org.ingest.collect import epa-2026-lead-azide-emergency-permit-1 ~/Downloads/<file>`
- **Emergency permit #2: storage (Jul 1 to Sep 28, 2026)** (`epa-2026-lead-azide-emergency-permit-2`)
  - Link: https://www.epa.gov/node/300666
  - Why: HTTP 404. Same pattern as permit #1 (not checked).
  - Import: `uv run python -m branchiv_org.ingest.collect import epa-2026-lead-azide-emergency-permit-2 ~/Downloads/<file>`
- **American Ordnance awarded $304 million contract for extrudable C-4** (`militaryleak-2021-ao-c4-contract`)
  - Link: https://militaryleak.com/2021/05/16/american-ordnance-awarded-304-million-contract-for-the-family-of-extrudable-c-4-items/
  - Why: HTTP 404. Look for the defense.gov contract announcement instead (it is needs_original anyway).
  - Import: `uv run python -m branchiv_org.ingest.collect import militaryleak-2021-ao-c4-contract ~/Downloads/<file>`
- **IDPH adds nearly 150 more high-risk ZIP codes to lead testing list** (`idph-2024-lead-testing-zip-codes`)
  - Link: https://www2.illinois.gov/IISNews/30185-IDPH_Adds_Nearly_150_More_High-Risk_ZIP_Codes_to_Lead_Testing_List.pdf
  - Why: redirected to the illinois.gov home page; the PDF has moved.
  - Import: `uv run python -m branchiv_org.ingest.collect import idph-2024-lead-testing-zip-codes ~/Downloads/<file>`

## Needs a browser (JavaScript-only page or archive viewer) (6)

The saved HTML is a viewer shell with no document text. It is kept in data/raw/ for reference but not recorded as collected.

- **ATSDR Public Health Assessment, Iowa Army Ammunition Plant** (`atsdr-1999-public-health-assessment`)
  - Link: https://webarchive.library.unt.edu/eot2008/20090506010334/http://www.atsdr.cdc.gov/hac/pha/iowaarmy/iaa_toc.html
  - Why: UNT web archive player page. The raw archived copy is usually at the same URL with `id_` after the timestamp (…/20090506010334id_/http://…).
  - Import: `uv run python -m branchiv_org.ingest.collect import atsdr-1999-public-health-assessment ~/Downloads/<file>`
- **Operations Support Command annual history, FY2000** (`army-osc-fy2000-history`)
  - Link: https://webarchive.library.unt.edu/web/20130214190821/http://www.osc.army.mil/supportingdocs/fy00.pdf
  - Why: UNT web archive player page (see the `id_` tip above).
  - Import: `uv run python -m branchiv_org.ingest.collect import army-osc-fy2000-history ~/Downloads/<file>`
- **AFSC/JMC annual history, FY2003** (`army-afsc-jmc-fy2003-history`)
  - Link: https://webarchive.library.unt.edu/web/20130214190821/http://www.osc.army.mil/supportingdocs/fy03.pdf
  - Why: UNT web archive player page. The dossier gave the same archived path pattern as FY2000; confirm this is the FY2003 file.
  - Import: `uv run python -m branchiv_org.ingest.collect import army-afsc-jmc-fy2003-history ~/Downloads/<file>`
- **USAspending award record, ordering agreement W52P1J09G0001** (`usaspending-w52p1j09g0001`)
  - Link: https://www.usaspending.gov/award/CONT_AWD_W519TC25F0417_9700_W52P1J09G0001_9700
  - Why: JavaScript app. Use the page's download button (CSV/JSON) and import that file.
  - Import: `uv run python -m branchiv_org.ingest.collect import usaspending-w52p1j09g0001 ~/Downloads/<file>`
- **Complexities of Lead Azide (Shaneyfelt, 1984), unofficial Scribd copy** (`scribd-1984-shaneyfelt-complexities-of-lead-azide-copy`)
  - Link: https://www.scribd.com/document/148877975/Lead-Azide-p004872
  - Why: Scribd viewer page; downloading the PDF needs a Scribd sign-in.
  - Import: `uv run python -m branchiv_org.ingest.collect import scribd-1984-shaneyfelt-complexities-of-lead-azide-copy ~/Downloads/<file>`
- **CRS IF12251: Army organic industrial base** (`crs-if12251`)
  - Link: https://sgp.fas.org/crs/natsec/IF12251.pdf
  - Why: FAS mirror returned an empty page. Prefer the congress.gov copy (entry is needs_original).
  - Import: `uv run python -m branchiv_org.ingest.collect import crs-if12251 ~/Downloads/<file>`


## Server error (1)

Probably temporary. Try the collector again later before downloading by hand.

- **Army Materiel Command senior commander page** (`army-redstone-amc-leadership`)
  - Link: https://home.army.mil/redstone/index.php/about/leadership/senior-commander
  - Why: HTTP 502 after 4 attempts, on two runs (03:31 and 03:41 UTC, Nov 23)
  - Import: `uv run python -m branchiv_org.ingest.collect import army-redstone-amc-leadership ~/Downloads/<file>`

## Unavailable (1)

- **Complexities of Lead Azide (Shaneyfelt, DDESB Explosives Safety Seminar, Aug 29, 1984)** (`ddesb-1984-shaneyfelt-complexities-of-lead-azide`)
  - Link: https://apps.dtic.mil/sti/pdfs/ADP004872.pdf
  - Why: DTIC public access offline. Try NTIS, a library or FOIA (Distribution A).
  - Import: `uv run python -m branchiv_org.ingest.collect import ddesb-1984-shaneyfelt-complexities-of-lead-azide ~/Downloads/<file>`

## No link yet (4)

Find the document, then either add its `url` to sources.toml and re-run the collector, or download it and import it.

- **8th Circuit petition and docket (Iowa Environmental Council and Sierra Club v. EPA)** (`ca8-2026-lead-azide-docket`)
  - Why: PACER: petition, EPA's Nov 20 response, DOJ's Nov 21 filing.
  - Import: `uv run python -m branchiv_org.ingest.collect import ca8-2026-lead-azide-docket ~/Downloads/<file>`
- **Illinois EPA and IDPH statement on the planned Iowa detonation** (`iepa-idph-2026-detonation-statement`)
  - Why: Original release on illinois.gov not found yet.
  - Import: `uv run python -m branchiv_org.ingest.collect import iepa-idph-2026-detonation-statement ~/Downloads/<file>`
- **DoD Munitions: Planning and Leadership Needed to Address Persistent Shortages** (`gao-2026-munitions-shortages`)
  - Why: Report number not found yet.
  - Import: `uv run python -m branchiv_org.ingest.collect import gao-2026-munitions-shortages ~/Downloads/<file>`
- **Methodology: Iowa Army Ammunition Plant lead azide investigation** (`silvia-methodology`)
  - Why: Your methodology.md: add it to the repo at data/sources/methodology.md (no import needed).

## Already saved by you, not yet imported (1)

- **DOJ tells 8th Circuit the Army will detonate without a permit** (`reuters-2026-doj-detonate-without-permit`)
  - Link: https://www.reuters.com/legal/government/iowa-detonation-lead-will-proceed-under-emergency-powers-army-says-bypassing-2026-11-22/
  - Why: Your saved PDF of the Reuters article.
  - Import: `uv run python -m branchiv_org.ingest.collect import reuters-2026-doj-detonate-without-permit ~/Downloads/<file>`
