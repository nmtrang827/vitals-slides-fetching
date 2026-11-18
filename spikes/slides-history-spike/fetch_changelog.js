// Download the internal edit changelog of a Google Slides deck (the same
// revisions/tiles + revisions/load endpoints the VITALS Google Docs browser path uses).
//
// How to run:
//   1. Open the deck in Chrome, signed in with an account that has EDIT access.
//   2. Open File → Version history → See version history and let the panel load
//      (this makes the page fetch revisions/tiles, whose token the snippet reuses).
//      Then press F12 and open the Console tab.
//   3. If Chrome asks, type `allow pasting` and press Enter.
//   4. Paste this whole file and press Enter.
// It is read-only. It prints a summary and downloads slides-changelog-<id>.json
// (contains deck text and editor names: save it under data/, which is gitignored).
(async () => {
  const match = location.pathname.match(/\/presentation\/d\/([a-zA-Z0-9_-]+)/);
  if (!match) return console.error('Open a Google Slides deck (…/presentation/d/<id>/edit) first.');
  const docId = match[1];
  const prefix = `${location.origin}/presentation/d/${docId}`;

  // Token candidates. Best source: the token the page itself sent to revisions/* (present
  // after opening File → Version history once). Then the same places the Docs path looks.
  const tokens = new Set();
  performance.getEntriesByType('resource')
    .map((e) => e.name)
    .filter((u) => u.includes(`/presentation/d/${docId}/revisions/`))
    .forEach((u) => {
      const t = new URL(u).searchParams.get('token');
      if (t) tokens.add(t);
    });
  if (!tokens.size) console.warn('No page revisions request seen. Open File → Version history → See version history first, then rerun.');
  const flag = window._docs_flag_initialData;
  if (flag?.info_params?.token) tokens.add(String(flag.info_params.token));
  const scan = (text) => {
    for (const m of String(text || '').matchAll(/["'?&]token["']?\s*[:=]\s*["']?([A-Za-z0-9._~%-]{8,})/g)) {
      tokens.add(decodeURIComponent(m[1]));
    }
  };
  if (flag) scan(JSON.stringify(flag));
  [...document.querySelectorAll('script')].slice(0, 80).forEach((s) => scan(s.textContent));
  const candidates = [...tokens].slice(0, 12).concat([null]);

  const read = async (url) => {
    const res = await fetch(url, { credentials: 'same-origin' });
    let body = await res.text();
    if (body.startsWith(")]}'")) body = body.slice(4);
    if (!res.ok) return { error: `HTTP ${res.status}`, preview: body.slice(0, 200) };
    try { return { data: JSON.parse(body) }; } catch (e) { return { error: 'not JSON', preview: body.slice(0, 200) }; }
  };
  const withToken = (path, params, token) => {
    const p = new URLSearchParams(params);
    if (token) p.set('token', token);
    return `${prefix}/${path}?${p}`;
  };

  // 1. Tiles: revision ranges + user map.
  let tiles = null, token = null;
  const tileAttempts = [];
  for (const t of candidates) {
    const r = await read(withToken('revisions/tiles', { id: docId, start: '1', showDetailedRevisions: 'true' }, t));
    tileAttempts.push({ token: t ? `${t.slice(0, 6)}…` : '(none)', error: r.error || null, preview: r.preview });
    if (!r.error) { tiles = r.data; token = t; break; }
  }
  if (!tiles) return console.error('revisions/tiles failed for every token', tileAttempts);
  const tileInfo = tiles.tileInfo || [];
  const latest = tileInfo.length ? tileInfo[tileInfo.length - 1].end : null;
  console.log(`tiles ok: ${tileInfo.length} tiles, latest revision ${latest}, users ${Object.keys(tiles.userMap || {}).length}`);

  // 2. Changelog: try the whole range, then fall back to chunks.
  const changelog = [];
  const loadErrors = [];
  const loadRange = async (start, end) => {
    const r = await read(withToken('revisions/load', { id: docId, start: String(start), end: String(end) }, token));
    if (r.error) { loadErrors.push({ start, end, ...r }); return false; }
    changelog.push(...(r.data.changelog || []));
    if (start === 1 && !window.__slidesLoadKeys) window.__slidesLoadKeys = Object.keys(r.data);
    return true;
  };
  if (latest && !(await loadRange(1, latest))) {
    const CHUNK = 500;
    for (let s = 1; s <= latest; s += CHUNK) await loadRange(s, Math.min(s + CHUNK - 1, latest));
  }

  // 3. Summarize op shapes so we can see whether element type / slide id are present.
  const typeOf = (op) => (op && typeof op === 'object' && !Array.isArray(op)) ? (op.ty ?? Object.keys(op).join(',')) : Array.isArray(op) ? `array:${op[0]}` : typeof op;
  const opTypes = {};
  const samples = {};
  const byAuthor = {};
  changelog.forEach((entry) => {
    const [op, ts, author] = entry;
    const ty = String(typeOf(op));
    opTypes[ty] = (opTypes[ty] || 0) + 1;
    if (!samples[ty]) samples[ty] = entry;
    const name = tiles.userMap?.[author]?.name || author;
    byAuthor[name] = (byAuthor[name] || 0) + 1;
  });
  const first = changelog[0], last = changelog[changelog.length - 1];
  const summary = {
    docId,
    tiles: tileInfo.length,
    latestRevision: latest,
    changelogEntries: changelog.length,
    loadResponseKeys: window.__slidesLoadKeys || null,
    loadErrors,
    timeRange: first && last ? [new Date(first[1]).toISOString(), new Date(last[1]).toISOString()] : null,
    entriesByAuthor: byAuthor,
    opTypeCounts: opTypes,
  };
  console.log('SUMMARY', summary);
  console.log('ONE SAMPLE PER OP TYPE (first 15):');
  Object.entries(samples).slice(0, 15).forEach(([ty, e]) => console.log(ty, JSON.stringify(e).slice(0, 600)));

  const blob = new Blob([JSON.stringify({ summary, tileAttempts, tiles, samples, changelog }, null, 1)], { type: 'application/json' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `slides-changelog-${docId}.json` });
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Downloaded slides-changelog-${docId}.json`);
})();
