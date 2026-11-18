# Google Slides Edit History: Research Findings

*Spike results, November 17, 2026*

## Goal

Add Google Slides as a source for the same contribution and reflection analysis that Google Docs already feeds. The Slides source should plug into the current pipeline, produce the same contribution shape as Docs, and credit each person **per slide** with the **type of content** they added (text, image, shape, table), along with when they added it.

## Background: how Google Docs works today

The backend has two ways to collect Docs history:

- **API path** (VITALS-beta: `teamio-backend/core/google_docs_api_client.py`): uses the Drive API to list revisions and download each revision as plain text, then compares the texts.
- **Browser path** (VITALS-beta: `teamio-backend/api/google_docs_processor.py`): opens the document in a signed-in Chrome session and calls Google's internal version-history endpoints (`revisions/tiles` and `revisions/load`). These return a changelog of every individual edit with the author and timestamp. The backend groups edits into per-author work sessions (split after 30 minutes idle) and credits the words each author added that still exist at the end of their session.

The first idea for Slides was to copy the API path. That did not work. Copying the browser path did.

## Approach 1: download each revision as PowerPoint and compare (did not work)

**Idea.** For each Drive revision, download a `.pptx` file. A `.pptx` is a zip of XML files with one file per slide, and each element's type is explicit (text box, picture, shape, table). Comparing consecutive revisions would show what was added, removed or changed on each slide.

**What we tried.**

- A test script (`pptx_revision_export.py`) that lists a deck's revisions, downloads each one as `.pptx`, parses the slides and compares consecutive revisions.
- Three different download URLs: the `exportLinks` that Drive returns for each revision, the `docs.google.com/feeds/download/presentations/Export` URL (the Slides version of the URL the Docs path uses), and `docs.google.com/presentation/d/<id>/export/pptx?revision=<n>`.
- Three decks: a 2013 deck with 13 revisions from 4 editors, a 2026 deck with 10 revisions from 6 editors over 9 days, and an earlier small test.

**Results.**

- All three URLs returned valid `.pptx` files for every revision.
- **Every revision had the same content.** In the 2026 deck, the first revision (the moment the deck was created) already matched the final version exactly: 51 slides and 919 elements with the same text, positions and images. Six people editing for a week would have changed something.
- The only differences between downloads were noise. Images were re-encoded slightly differently each time, and two tables were written out with small XML differences. These differences flipped back and forth in no time order, so they were not real edits.
- **Control test:** asking for revision `987654321`, which cannot exist, still returned a full `.pptx` from both URL patterns.
- Each download was large: about 12 MB for the 2013 deck and 75 MB for the 2026 deck, because images are embedded in the file.

**Why it failed.** Google ignores the revision number when exporting Slides and always returns the current deck. Unlike Docs, there is no way to download past versions of a Slides deck as a file. Even if there were, downloading 75 MB per revision would not scale.

**Side findings.**

- The Drive revision list itself works: each revision has an editor and a timestamp. That gives *who* and *when*, but not *what*.
- Element names inside the `.pptx` (for example `Google Shape;155;p34`) do not match the element IDs used by the Slides API, so the two cannot be joined.

## Approach 2: the Slides API (useful for checking, not for history)

The official Slides API (`presentations.get`) returns the deck as it is now: every slide and element, with real element IDs and exact types. It has no history, so it cannot tell who added what. It turned out to be the right tool for **checking** the results of Approach 3.

## Approach 3: Google's internal version-history changelog (worked)

**Idea.** Slides runs on the same editor platform as Docs, so the internal `revisions/tiles` and `revisions/load` endpoints that the Docs browser path uses might also exist for Slides.

**What we tried.**

1. Watched which requests the Slides page makes when you open *File → Version history*. It calls `/presentation/d/<id>/revisions/tiles` with the same parameters Docs uses.
2. Wrote a browser console script (`fetch_changelog.js`) that calls `revisions/tiles` (revision ranges and the user list) and then `revisions/load` (the full changelog), and downloads the result as JSON.

**Problems we hit and how we solved them.**

- **400 errors with the token from the page data.** The token the Docs path uses was not accepted. The fix was to take the token from the page's own `revisions/tiles` request, which appears after opening version history.
- **400 errors when signed into several Google accounts.** Requests were sent for the wrong account. Running in an Incognito window signed into only the editing account fixed it. The backend's Chrome worker uses a dedicated profile with one account, so it should not hit this.

**Results (2026 deck).**

- The full history loaded in a single request: **10,093 changelog entries**, one per revision, covering February 23 to March 3.
- Each entry contains the edit operation, a timestamp in milliseconds, the author's ID and the revision number. The tiles response maps author IDs to names (13 editors).
- Operations refer to the **real element and slide IDs**, the same ones the Slides API uses.
- Text operations include the exact characters typed or deleted, so word counts can be computed the same way as for Docs.

## Checking the changelog against the real deck

A decoder script (`decode_changelog.py`) replays every operation in order. It tracks which slide each element lives on (including elements inside groups), the element type, who created it and when, and who deleted it. It then compares the result against `presentations.get` for the same deck.

- **1,086 of 1,086** elements in the live deck were placed on the correct slide or speaker-notes page.
- **51 of 51** slides were found in the changelog.
- No element in the live deck was missing from the changelog.
- 34,130 of 34,234 edit events (99.7%) could be assigned to a slide.

**Element type codes, confirmed against the Slides API:**

| Code | Real type | Category |
|---|---|---|
| 108 | Text box, including title, subtitle and body placeholders | text |
| 3 | Image | image |
| 1 | Custom shape | shape |
| 8 | Ellipse | shape |
| 153 | Line or connector | shape |
| 158 | Slide thumbnail on the speaker-notes page | ignored |
| (operation 2) | Group | not credited; its elements are |
| (operation 22) | Table | table |

Other codes appeared only for elements that were later deleted, so they could not be confirmed. They appear to be other shape types and will count as **shape**.

**Main operation codes (inferred from samples):**

| Code | Meaning |
|---|---|
| 0 | Delete element |
| 2 | Group elements |
| 3 | Create element (element ID, type code, position, properties, slide ID) |
| 4 | Batch of several operations |
| 5 | Change element properties |
| 6 | Move or resize |
| 9 | Replace image or set slide background |
| 12 | Create slide (includes the layout) |
| 15 / 16 / 17 | Insert text / delete text / text style |
| 22, 23, 27, 28, 30 | Table creation and structure changes |
| 40 | Snapshot of the whole deck (seen in revision 1) |

**Other observations.**

- About 115 elements that the replay considers still present are not in the API. These are placeholders on layout and master pages, which are not real slides. The real feature should keep only real slide IDs.
- Revision 1 of the 2026 deck contained a full copy of an earlier deck (about 8,000 elements, most deleted later). Without a rule, the person who made the copy would get credit for all of it.
- Moves are mostly small adjustments. Of 1,697 moves, 350 were under 1% of the slide width, 810 were 1–5%, 297 were 5–20%, and 240 were 20% or more. There were also 1,807 text-style changes.

## Decisions made

- **Starting content in revision 1 is not credited.** It is treated as the starting point.
- **Groups:** creating a group is not credited; the elements inside are.
- **Tables** get their own category. Text typed into table cells counts as text.
- **Lines, arrows and connectors** count as shape.
- **Layout and formatting** (moving, resizing, text styling, fill and outline) are **not** contributions for now.
- **Deletions follow the Docs rule:** they are logged as events but earn no credit. Credit is the content an author added that still exists at the end of their own work session. If someone else deletes it later, the original author keeps the credit.
- **Rule of thumb:** where in doubt, match the current Google Docs implementation.
- **Categories (filterable):** text, image, shape, table.
- **Per-slide credit:** one work session that touches several slides becomes one contribution per slide.

## Open questions

- Should deleting someone else's work be shown or credited differently?
- Should layout and formatting earn credit later? If so, a likely rule is to compare an element's position at the start and end of a session, ignore small nudges (for example under about 2% of the slide width and under 5% resize), and report real moves under a separate layout category.
- Should speaker notes be included? They are easy to add because they live on separate notes pages in the changelog.

## Risks

- The changelog endpoints are **undocumented**. Google could change them without notice. The Docs browser path already depends on the same kind of endpoint, so this adds no new kind of risk.
- The account doing the collection needs **edit access** to each deck. Drive does not give revision history to viewers.
- Rare element type codes are unconfirmed. Unknown codes fall back to shape.

## Files

All paths are relative to this folder. See `README.md` for how to run each script.

- `fetch_changelog.js`: browser console script that downloads a deck's changelog (Approach 3).
- `decode_changelog.py`: replays a changelog, credits edits per slide and category, and optionally checks the result against the Slides API.
- `pptx_revision_export.py`: Approach 1 test (PowerPoint revision downloads). Kept for reference; it is a dead end.
- `examples/changelog-sample.json`: a redacted one-slide excerpt of the 2026 deck's changelog, and `examples/decoded-sample.txt`, the decoder's output for it.

## Next step

Write the implementation plan: fetch the Slides changelog through the existing browser worker, add a Slides normalizer that turns the changelog into per-slide contributions in the Docs format, and plug it into the existing pipeline, including the date-range filter.
