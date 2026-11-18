# Google Slides Edit History Spike

An experiment to find out whether we can get **who added what, on which slide, and when** from a Google Slides deck, so that Slides can feed the same contribution analysis as Google Docs in VITALS.

**Short answer: yes, through Google's internal version-history changelog.** Downloading old revisions as PowerPoint files does not work. Full details, results and decisions are in [FINDINGS.md](FINDINGS.md).

This folder is an experiment, not production code. Nothing here is wired into an app.

## What's here

| File | What it does |
|---|---|
| `FINDINGS.md` | What we tried, what worked, what didn't and why, plus decisions and open questions. Start here. |
| `fetch_changelog.js` | Browser console script. Downloads a deck's full edit changelog as JSON. |
| `decode_changelog.py` | Replays a changelog and prints, per slide, who created or edited which text, images and shapes, and when. Optionally checks the result against the official Slides API. |
| `pptx_revision_export.py` | The approach that failed (downloading each revision as `.pptx`). Kept so the result can be reproduced. |
| `examples/changelog-sample.json` | Redacted excerpt of a real changelog: one slide, two editors. |
| `examples/decoded-sample.txt` | What `decode_changelog.py` prints for the sample. |

## Try it without any setup

Needs Python 3.10 or newer.

```
pip install -r requirements.txt
python -B decode_changelog.py examples/changelog-sample.json
```

You should see a summary and a per-slide timeline like `examples/decoded-sample.txt`. The decoder also writes `examples/changelog-sample.decoded.json`, which git ignores.

In the sample, names are replaced with "Editor A/B", typed text with placeholder text of the same length, and IDs with fake ones. Element and slide object IDs are real but meaningless outside the original deck.

## Run it on a real deck

You need **edit access** to the deck. Google does not show version history to viewers.

### 1. Download the changelog (browser)

1. Open the deck in Chrome. Use an **Incognito window signed into only one Google account**. If you're signed into several accounts, requests can go to the wrong account and fail with 400 errors.
2. Open **File → Version history → See version history** and wait for the panel to load. The script reuses the token from this request.
3. Press F12 and open the **Console** tab. If Chrome blocks pasting, type `allow pasting` and press Enter.
4. Paste the whole of `fetch_changelog.js` and press Enter.
5. The console prints a `SUMMARY`, and the browser downloads `slides-changelog-<deck id>.json`. Move it into `data/` in this folder. `data/` is gitignored, and the file contains real names and deck text.

To copy the script on Windows: `Get-Content fetch_changelog.js -Raw | Set-Clipboard`. On macOS: `pbcopy < fetch_changelog.js`.

### 2. Decode it

```
python -B decode_changelog.py data/slides-changelog-<deck id>.json
```

### 3. Optional: check against the Slides API

This compares the decoder's element types and slide assignments with what the official API reports for the same deck.

1. Go to the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground).
2. Enter these scopes: `https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/presentations.readonly`
3. Click **Authorize APIs**, sign in with an account that can open the deck, then click **Exchange authorization code for tokens**.
4. Copy the **access token** (starts with `ya29.`). It expires after about an hour.

```
python -B decode_changelog.py data/slides-changelog-<deck id>.json --token <access token>
```

The output gains a `validation` section:

- `kind_code_to_api_types` shows what each element type code really is.
- `page_attribution` shows how many elements were placed on the correct slide.

**Never commit or share access tokens.**

### Reproduce the failed approach (optional)

```
python -B pptx_revision_export.py "<slides url>" --token <access token>
```

This downloads recent revisions as `.pptx` files into `data/pptx-export/`. They can be 10–75 MB each. It then shows that every revision contains the same content, and that a revision number that can't exist still returns a file.

## Known limitations

- The changelog endpoints are **undocumented** Google internals and may change without notice.
- So far, fetching has only been done by hand in a browser. Automating it, for example with a headless Chrome worker, is not proven yet.
- Validated on one 51-slide deck. Rare element type codes are unconfirmed and are treated as shapes.
- Character counts for text edits assume operations 15 and 16 are insert and delete at the positions shown. Text has not yet been rebuilt and compared with the API, and table-cell text addressing is not decoded yet.
- Revision 1 can contain a whole copied deck. The decoder still includes it; the planned feature will exclude it from credit.

See the "Open questions" and "Risks" sections of `FINDINGS.md`.
