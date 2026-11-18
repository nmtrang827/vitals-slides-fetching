"""Approach 1 (DEAD END, kept for reference): per-revision PPTX export of a Google Slides deck.

Result: Google ignores the revision number on Slides exports and always returns the
current deck (see FINDINGS.md). Kept so the result can be reproduced.

    python -B pptx_revision_export.py "<slides url>" --token <access token>

Options: [--max-revisions 15] [--probe 10] [--out data/pptx-export]

Read-only. Downloads revision exports of one deck, writes them plus a JSON report
to --out (gitignored by default), and prints a summary answering:

  1. Do Drive revisions of a Slides file expose a pptx exportLink?
  2. Does a per-revision pptx export return that revision's content
     (not the current deck)? Which URL pattern works?
  3. Are Google's PPTX element names ("Google Shape;<n>;<id>") unique and stable
     across revisions, and do they map to Slides API objectIds?
  4. Can intermediate (unlisted) revision IDs be probed like Docs?

Plus a per-slide diff (added / removed / modified element, with category text,
image, shape, table, line) between consecutive exported revisions.
"""
import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

DRIVE = "https://www.googleapis.com/drive/v3"
SLIDES = "https://slides.googleapis.com/v1"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
P = "{%s}" % NS["p"]
R_EMBED = "{%s}embed" % NS["r"]
R_ID = "{%s}id" % NS["r"]

EXPORT_PACING_SECONDS = 1.5
MAX_429_RETRIES = 4


def extract_presentation_id(target: str) -> str:
    match = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", target or "")
    if not match:
        raise ValueError("Could not extract Google Slides ID from URL")
    return match.group(1)


def short(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- HTTP

class Fetcher:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, connect=20.0),
            headers={"Authorization": f"Bearer {token}"},
        )
        self._last = 0.0

    async def close(self):
        await self.client.aclose()

    async def json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def paced_get(self, url: str, follow_redirects: bool = True) -> httpx.Response:
        """GET with pacing and 429 backoff. Returns the final response (any status)."""
        backoff = 5.0
        resp = None
        for _ in range(MAX_429_RETRIES):
            wait = EXPORT_PACING_SECONDS - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self.client.get(url, follow_redirects=follow_redirects)
            except httpx.TransportError:
                # Network blip (sleep, Wi-Fi drop, connect timeout): wait and retry.
                self._last = time.monotonic()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            self._last = time.monotonic()
            if resp.status_code != 429:
                return resp
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        if resp is None:
            raise httpx.ConnectError(f"network failed {MAX_429_RETRIES} times: {url[:80]}")
        return resp


async def list_revisions(f: Fetcher, file_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    token = None
    while True:
        params = {
            "fields": "nextPageToken,revisions(id,modifiedTime,mimeType,exportLinks,"
                      "lastModifyingUser(displayName,emailAddress,permissionId))",
            "pageSize": 200,
            "supportsAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        payload = await f.json(f"{DRIVE}/files/{quote(file_id)}/revisions", params)
        out.extend(payload.get("revisions", []))
        token = payload.get("nextPageToken")
        if not token:
            break
    out.sort(key=lambda r: r.get("modifiedTime") or "")
    return out


def candidate_export_urls(file_id: str, revision: Dict[str, Any]) -> List[Tuple[str, str]]:
    rev = quote(str(revision["id"]), safe="")
    fid = quote(file_id, safe="")
    urls: List[Tuple[str, str]] = []
    link = (revision.get("exportLinks") or {}).get(PPTX_MIME)
    if link:
        urls.append(("drive_exportLinks", link))
    urls.append((
        "feed_presentations_export",
        f"https://docs.google.com/feeds/download/presentations/Export?id={fid}&revision={rev}&exportFormat=pptx",
    ))
    urls.append((
        "presentation_export_path",
        f"https://docs.google.com/presentation/d/{fid}/export/pptx?revision={rev}",
    ))
    return urls


def looks_like_pptx(body: bytes) -> bool:
    return body[:2] == b"PK"


async def export_revision(f: Fetcher, file_id: str, revision: Dict[str, Any], try_all: bool) -> Dict[str, Any]:
    """Try each URL pattern. With try_all, test every pattern (to compare them)."""
    attempts = []
    winner: Optional[Dict[str, Any]] = None
    for strategy, url in candidate_export_urls(file_id, revision):
        try:
            resp = await f.paced_get(url)
            body = resp.content
            ok = resp.status_code == 200 and looks_like_pptx(body)
            attempt = {
                "strategy": strategy,
                "status": resp.status_code,
                "is_pptx": ok,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest() if ok else None,
            }
        except httpx.HTTPError as exc:
            attempt = {"strategy": strategy, "status": None, "is_pptx": False, "error": type(exc).__name__}
            body = b""
            ok = False
        attempts.append(attempt)
        if ok and winner is None:
            winner = {"strategy": strategy, "body": body, "sha256": attempt["sha256"]}
            if not try_all:
                break
    return {"attempts": attempts, "winner": winner}


def content_fingerprint(parsed: Dict[str, Any]) -> str:
    """Hash of what a viewer sees: per slide, each element's identity, category, text, geometry, image."""
    rows = []
    for s in parsed["slides"]:
        for e in s["elements"]:
            rows.append((s["index"], e["name"], e["category"], e["text"], e["geometry"], e["media_hash"]))
        rows.append((s["index"], "notes", s["notes_text"]))
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:16]


async def bogus_revision_control(f: Fetcher, file_id: str) -> Dict[str, Any]:
    """Ask for a revision that cannot exist. A pptx back means the revision param is ignored."""
    out = {}
    fid = quote(file_id, safe="")
    for strategy, url in (
        ("feed_presentations_export",
         f"https://docs.google.com/feeds/download/presentations/Export?id={fid}&revision=987654321&exportFormat=pptx"),
        ("presentation_export_path",
         f"https://docs.google.com/presentation/d/{fid}/export/pptx?revision=987654321"),
    ):
        try:
            resp = await f.paced_get(url)
            out[strategy] = {"status": resp.status_code,
                             "returned_pptx": resp.status_code == 200 and looks_like_pptx(resp.content)}
        except httpx.HTTPError as exc:
            out[strategy] = {"status": f"error:{type(exc).__name__}"}
    return out


async def export_current(f: Fetcher, file_id: str) -> Optional[bytes]:
    resp = await f.paced_get(
        f"{DRIVE}/files/{quote(file_id)}/export?mimeType={quote(PPTX_MIME, safe='')}"
    )
    return resp.content if resp.status_code == 200 and looks_like_pptx(resp.content) else None


# --------------------------------------------------------------------------- PPTX parsing

def _rels(zf: zipfile.ZipFile, part: str) -> Dict[str, str]:
    folder, name = part.rsplit("/", 1)
    rels_path = f"{folder}/_rels/{name}.rels"
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out = {}
    for rel in root.findall("rel:Relationship", NS):
        target = rel.get("Target", "")
        if not target.startswith("/"):
            parts = folder.split("/")
            for seg in target.split("/"):
                if seg == "..":
                    parts.pop()
                elif seg and seg != ".":
                    parts.append(seg)
            target = "/".join(parts)
        out[rel.get("Id")] = target.lstrip("/")
    return out


def _text_of(el: ET.Element) -> str:
    paragraphs = []
    for para in el.iter("{%s}p" % NS["a"]):
        paragraphs.append("".join(t.text or "" for t in para.iter("{%s}t" % NS["a"])))
    return "\n".join(paragraphs).strip()


def _geometry(el: ET.Element) -> Optional[Tuple[int, int, int, int]]:
    off = el.find(".//a:off", NS)
    ext = el.find(".//a:ext", NS)
    if off is None or ext is None:
        return None
    try:
        return (int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _categorize(tag: str, el: ET.Element, text: str) -> str:
    if tag == "pic":
        return "image"
    if tag == "cxnSp":
        return "line"
    if tag == "graphicFrame":
        data = el.find(".//a:graphicData", NS)
        uri = data.get("uri", "") if data is not None else ""
        if uri.endswith("/table"):
            return "table"
        if "chart" in uri:
            return "chart"
        return "graphic_frame"
    if tag == "sp":
        if text:
            return "text"
        if el.find(".//p:nvPr/p:ph", NS) is not None:
            return "empty_placeholder"
        # A shape with an image fill is visually an image.
        if el.find(".//a:blipFill", NS) is not None:
            return "image"
        return "shape"
    return tag


def _walk_tree(tree: ET.Element, zf, rels, slide_part, group: Optional[str], out: List[Dict[str, Any]]):
    for child in list(tree):
        tag = child.tag.replace(P, "")
        if tag not in ("sp", "pic", "graphicFrame", "cxnSp", "grpSp"):
            continue
        nv = child.find(".//p:cNvPr", NS)
        name = nv.get("name", "") if nv is not None else ""
        shape_id = nv.get("id", "") if nv is not None else ""
        if tag == "grpSp":
            _walk_tree(child, zf, rels, slide_part, name or shape_id, out)
            continue
        text = _text_of(child)
        media_hash = None
        blip = child.find(".//a:blip", NS)
        if blip is not None and blip.get(R_EMBED) in rels:
            media_part = rels[blip.get(R_EMBED)]
            if media_part in zf.namelist():
                media_hash = hashlib.sha256(zf.read(media_part)).hexdigest()[:16]
        raw = ET.tostring(child)
        name_parts = name.split(";") if name.startswith("Google Shape;") else None
        out.append({
            "slide_part": slide_part,
            "tag": tag,
            "category": _categorize(tag, child, text),
            "cnv_id": shape_id,
            "name": name,
            "google_suffix": name_parts[2] if name_parts and len(name_parts) >= 3 else None,
            "group": group,
            "text": text,
            "geometry": _geometry(child),
            "media_hash": media_hash,
            "xml_hash": hashlib.sha256(raw).hexdigest()[:16],
        })


def parse_pptx(body: bytes) -> Dict[str, Any]:
    zf = zipfile.ZipFile(io.BytesIO(body))
    pres_part = "ppt/presentation.xml"
    pres = ET.fromstring(zf.read(pres_part))
    pres_rels = _rels(zf, pres_part)
    slides = []
    for index, sld in enumerate(pres.findall(".//p:sldIdLst/p:sldId", NS), start=1):
        slide_part = pres_rels.get(sld.get(R_ID))
        if not slide_part or slide_part not in zf.namelist():
            continue
        root = ET.fromstring(zf.read(slide_part))
        rels = _rels(zf, slide_part)
        csld = root.find("p:cSld", NS)
        elements: List[Dict[str, Any]] = []
        tree = root.find(".//p:cSld/p:spTree", NS)
        if tree is not None:
            _walk_tree(tree, zf, rels, slide_part, None, elements)
        notes_part = next((t for t in rels.values() if "notesSlide" in t), None)
        notes_text = ""
        if notes_part and notes_part in zf.namelist():
            notes_root = ET.fromstring(zf.read(notes_part))
            # Notes body placeholder carries the speaker notes; slide image placeholder has no text.
            notes_text = _text_of(notes_root)
        layout_part = next((t for t in rels.values() if "slideLayout" in t), None)
        slides.append({
            "index": index,
            "part": slide_part,
            "sld_id": sld.get("id"),
            "csld_name": csld.get("name") if csld is not None else None,
            "layout_part": layout_part,
            "notes_text": notes_text,
            "elements": elements,
        })
    return {"slides": slides}


# --------------------------------------------------------------------------- Identity + diff

def element_key(el: Dict[str, Any], slide: Dict[str, Any]) -> str:
    # Google names are the hoped-for stable identity; fall back to slide-local cNvPr id.
    if el["name"].startswith("Google Shape;"):
        return el["name"]
    return f"{slide['part']}#cnv{el['cnv_id']}"


def slide_key(slide: Dict[str, Any]) -> str:
    # Prefer an explicit name; otherwise the most common Google suffix on the slide
    # (if suffixes turn out to be slide objectIds), else the sldId.
    if slide.get("csld_name"):
        return f"name:{slide['csld_name']}"
    suffixes = [e["google_suffix"] for e in slide["elements"] if e["google_suffix"]]
    if suffixes:
        most = max(set(suffixes), key=suffixes.count)
        if suffixes.count(most) > 1 or len(suffixes) == 1:
            return f"suffix:{most}"
    return f"sldId:{slide['sld_id']}"


def index_elements(parsed: Dict[str, Any]) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    out = {}
    for slide in parsed["slides"]:
        for el in slide["elements"]:
            out[element_key(el, slide)] = (el, slide)
    return out


def diff_revisions(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    a, b = index_elements(old), index_elements(new)
    changes = []
    for key in b.keys() - a.keys():
        el, slide = b[key]
        changes.append({"change": "added", "slide": slide["index"], "slide_key": slide_key(slide),
                        "category": el["category"], "key": key, "text": short(el["text"])})
    for key in a.keys() - b.keys():
        el, slide = a[key]
        changes.append({"change": "removed", "slide": slide["index"], "slide_key": slide_key(slide),
                        "category": el["category"], "key": key, "text": short(el["text"])})
    for key in a.keys() & b.keys():
        (ea, sa), (eb, sb) = a[key], b[key]
        if ea["xml_hash"] == eb["xml_hash"] and sa["index"] == sb["index"]:
            continue
        what = []
        if ea["text"] != eb["text"]:
            what.append("text")
        if ea["media_hash"] != eb["media_hash"]:
            what.append("image_replaced")
        if ea["geometry"] != eb["geometry"]:
            what.append("moved_or_resized")
        if sa["index"] != sb["index"]:
            what.append("slide_reordered")
        if ea["category"] != eb["category"]:
            what.append(f"category {ea['category']}->{eb['category']}")
        if not what:
            what.append("style_or_other")
        changes.append({"change": "modified", "slide": sb["index"], "slide_key": slide_key(sb),
                        "category": eb["category"], "key": key, "what": what,
                        "text": short(eb["text"]),
                        "old_text": short(ea["text"]) if "text" in what else None})
    old_notes = {slide_key(s): s["notes_text"] for s in old["slides"]}
    for s in new["slides"]:
        if old_notes.get(slide_key(s), "") != s["notes_text"] and slide_key(s) in old_notes:
            changes.append({"change": "modified", "slide": s["index"], "slide_key": slide_key(s),
                            "category": "speaker_notes", "key": f"notes:{slide_key(s)}",
                            "text": short(s["notes_text"])})
    changes.sort(key=lambda c: (c["slide"], c["change"], c["category"]))
    return changes


def identity_report(parsed_by_rev: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, Any]:
    per_rev = []
    for rev_id, parsed in parsed_by_rev:
        names = [e["name"] for s in parsed["slides"] for e in s["elements"]]
        google_named = [n for n in names if n.startswith("Google Shape;")]
        suffixes = [e["google_suffix"] for s in parsed["slides"] for e in s["elements"] if e["google_suffix"]]
        per_rev.append({
            "revision": rev_id,
            "elements": len(names),
            "google_named": len(google_named),
            "names_unique": len(set(google_named)) == len(google_named),
            "suffixes_unique": len(set(suffixes)) == len(suffixes),
            "sample_names": google_named[:5],
        })
    stability = []
    for (ra, pa), (rb, pb) in zip(parsed_by_rev, parsed_by_rev[1:]):
        ka, kb = set(index_elements(pa)), set(index_elements(pb))
        stability.append({
            "from": ra, "to": rb,
            "kept": len(ka & kb), "added": len(kb - ka), "removed": len(ka - kb),
            "kept_ratio": round(len(ka & kb) / max(len(ka | kb), 1), 3),
        })
    return {"per_revision": per_rev, "adjacent_stability": stability}


async def slides_api_mapping(f: Fetcher, file_id: str, latest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        pres = await f.json(
            f"{SLIDES}/presentations/{quote(file_id)}",
            {"fields": "slides(objectId,pageElements(objectId,elementGroup(children(objectId))))"},
        )
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "status": exc.response.status_code,
                "note": "Slides API not reachable with current scopes (may need presentations.readonly)."}

    def walk(elements):
        for pe in elements or []:
            yield pe["objectId"]
            yield from walk((pe.get("elementGroup") or {}).get("children"))

    slide_ids = [s["objectId"] for s in pres.get("slides", [])]
    element_ids = {eid for s in pres.get("slides", []) for eid in walk(s.get("pageElements"))}
    result: Dict[str, Any] = {"ok": True, "slide_count": len(slide_ids), "element_count": len(element_ids),
                              "sample_slide_ids": slide_ids[:5], "sample_element_ids": sorted(element_ids)[:5]}
    if latest:
        suffixes = {e["google_suffix"] for s in latest["slides"] for e in s["elements"] if e["google_suffix"]}
        result["suffixes_matching_element_ids"] = len(suffixes & element_ids)
        result["suffixes_matching_slide_ids"] = len(suffixes & set(slide_ids))
        result["suffix_total"] = len(suffixes)
    return result


# --------------------------------------------------------------------------- Probing

async def probe_gap(f: Fetcher, file_id: str, revisions: List[Dict[str, Any]], budget: int) -> Dict[str, Any]:
    numeric = [r for r in revisions if str(r.get("id", "")).isdigit()]
    gaps = [(int(a["id"]), int(b["id"])) for a, b in zip(numeric, numeric[1:]) if int(b["id"]) - int(a["id"]) > 1]
    if not gaps or budget <= 0:
        return {"ran": False, "reason": "no numeric gaps" if not gaps else "budget 0",
                "listed_ids": [r.get("id") for r in revisions][-10:]}
    lo, hi = max(gaps, key=lambda g: g[1] - g[0])
    step = max((hi - lo) // (budget + 1), 1)
    candidates = list(range(lo + step, hi, step))[:budget]
    results = []
    for cid in candidates:
        url = (f"https://docs.google.com/feeds/download/presentations/Export"
               f"?id={quote(file_id, safe='')}&revision={cid}&exportFormat=pptx")
        try:
            resp = await f.paced_get(url, follow_redirects=False)
            entry = {"id": cid, "status": resp.status_code}
            if resp.status_code in (301, 302, 303, 307):
                full = await f.paced_get(url, follow_redirects=True)
                entry["followed_status"] = full.status_code
                entry["is_pptx"] = full.status_code == 200 and looks_like_pptx(full.content)
                if entry["is_pptx"]:
                    entry["sha256"] = hashlib.sha256(full.content).hexdigest()[:16]
        except httpx.HTTPError as exc:
            entry = {"id": cid, "status": f"error:{type(exc).__name__}"}
        results.append(entry)
        print(f"  probe {cid}: {entry['status']}{' pptx' if entry.get('is_pptx') else ''}", file=sys.stderr)
    found = [r for r in results if r.get("is_pptx")]
    return {"ran": True, "gap": [lo, hi], "checked": len(results), "found_pptx": len(found),
            "distinct_contents": len({r["sha256"] for r in found}), "results": results}


# --------------------------------------------------------------------------- Main

async def run(args) -> Dict[str, Any]:
    file_id = extract_presentation_id(args.target)
    token = (args.token or os.getenv("GOOGLE_ACCESS_TOKEN", "")).strip()
    if not token:
        raise SystemExit("Pass --token <access token> or set GOOGLE_ACCESS_TOKEN (see README).")
    os.makedirs(args.out, exist_ok=True)
    f = Fetcher(token)
    try:
        meta = await f.json(f"{DRIVE}/files/{quote(file_id)}",
                            {"fields": "id,name,mimeType,capabilities(canEdit,canReadRevisions,canDownload)",
                             "supportsAllDrives": "true"})
        if meta.get("mimeType") != SLIDES_MIME:
            raise SystemExit(f"Not a Google Slides file (mimeType={meta.get('mimeType')})")
        caps = meta.get("capabilities") or {}
        print(f"  access: canEdit={caps.get('canEdit')} canReadRevisions={caps.get('canReadRevisions')} "
              f"canDownload={caps.get('canDownload')}", file=sys.stderr)

        revisions = await list_revisions(f, file_id)
        if not revisions:
            raise SystemExit(
                "Drive returned 0 revisions. If canReadRevisions=False above, the token's Google account "
                "needs edit access to this deck (or use a deck that account owns)."
            )
        with_pptx_link = [r for r in revisions if PPTX_MIME in (r.get("exportLinks") or {})]
        check1 = {
            "revision_count": len(revisions),
            "with_any_exportLinks": sum(1 for r in revisions if r.get("exportLinks")),
            "with_pptx_exportLink": len(with_pptx_link),
            "sample_exportLink_formats": sorted((revisions[-1].get("exportLinks") or {}).keys()) if revisions else [],
        }

        selected = revisions[-args.max_revisions:] if args.max_revisions else revisions
        exports: List[Dict[str, Any]] = []
        parsed_by_rev: List[Tuple[str, Dict[str, Any]]] = []
        for i, rev in enumerate(selected):
            # Compare every URL pattern on the first two revisions; afterwards use the first that works.
            result = await export_revision(f, file_id, rev, try_all=i < 2)
            winner = result["winner"]
            entry = {
                "id": rev["id"],
                "modifiedTime": rev.get("modifiedTime"),
                "editor": (rev.get("lastModifyingUser") or {}).get("displayName"),
                "attempts": result["attempts"],
                "strategy": winner["strategy"] if winner else None,
                "sha256": winner["sha256"] if winner else None,
            }
            if winner:
                with open(os.path.join(args.out, f"rev-{rev['id']}.pptx"), "wb") as fh:
                    fh.write(winner["body"])
                try:
                    parsed = parse_pptx(winner["body"])
                    parsed_by_rev.append((rev["id"], parsed))
                    entry["content_fp"] = content_fingerprint(parsed)
                    entry["slides"] = len(parsed["slides"])
                    entry["elements"] = sum(len(s["elements"]) for s in parsed["slides"])
                except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
                    entry["parse_error"] = repr(exc)
            exports.append(entry)
            print(f"  rev {rev['id']:>8} {entry['modifiedTime']}  {entry['strategy'] or 'FAILED'}", file=sys.stderr)

        try:
            current = await export_current(f, file_id)
        except httpx.HTTPError:
            current = None
        # Raw bytes differ on every export (media re-encoding, part ordering), so compare
        # parsed slide content instead.
        current_fp = None
        if current:
            try:
                current_fp = content_fingerprint(parse_pptx(current))
            except (zipfile.BadZipFile, ET.ParseError, KeyError):
                pass
        fps = [e["content_fp"] for e in exports if e.get("content_fp")]
        control = await bogus_revision_control(f, file_id)
        check2 = {
            "exported": len(fps),
            "failed": len(exports) - len(fps),
            "distinct_contents": len(set(fps)),
            "older_revisions_equal_current": sum(1 for fp in fps[:-1] if fp == current_fp),
            "bogus_revision_control": control,
            "strategy_counts": {s: sum(1 for e in exports if e["strategy"] == s)
                                for s in {e["strategy"] for e in exports}},
            "note": "distinct_contents==1, older==current, or a pptx for the bogus revision means "
                    "the revision param is ignored.",
        }

        check3 = identity_report(parsed_by_rev)
        check3["slides_api"] = await slides_api_mapping(f, file_id, parsed_by_rev[-1][1] if parsed_by_rev else None)
        if parsed_by_rev:
            first_slide = parsed_by_rev[-1][1]["slides"][:1]
            check3["sample_slide"] = [{
                "csld_name": s["csld_name"], "sld_id": s["sld_id"], "slide_key": slide_key(s),
                "layout_part": s["layout_part"], "has_notes": bool(s["notes_text"]),
            } for s in first_slide]

        try:
            check4 = await probe_gap(f, file_id, revisions, args.probe)
        except httpx.HTTPError as exc:
            check4 = {"ran": False, "reason": f"probe failed: {type(exc).__name__}"}

        by_id = {e["id"]: e for e in exports}
        diffs = []
        for (ra, pa), (rb, pb) in zip(parsed_by_rev, parsed_by_rev[1:]):
            diffs.append({
                "from": ra, "to": rb,
                "modifiedTime": by_id[rb]["modifiedTime"],
                "editor": by_id[rb]["editor"],
                "changes": diff_revisions(pa, pb),
            })

        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "file": meta,
            "check1_exportLinks": check1,
            "check2_revision_exports": check2,
            "check3_identity": check3,
            "check4_probe": check4,
            "exports": exports,
            "diffs": diffs,
        }
    finally:
        await f.close()


def print_summary(report: Dict[str, Any]) -> None:
    c1, c2, c3, c4 = (report[k] for k in ("check1_exportLinks", "check2_revision_exports",
                                          "check3_identity", "check4_probe"))
    print(f"\nDeck: {report['file']['name']}")
    print(f"\n[1] exportLinks: {c1['with_pptx_exportLink']}/{c1['revision_count']} revisions have pptx link; "
          f"formats seen: {c1['sample_exportLink_formats']}")
    print(f"[2] revision exports: {c2['exported']} ok, {c2['failed']} failed, "
          f"{c2['distinct_contents']} distinct, {c2['older_revisions_equal_current']} older==current; "
          f"strategies {c2['strategy_counts']}")
    print(f"      bogus revision 987654321: {c2['bogus_revision_control']}")
    for e in report["exports"][:2]:
        print(f"      rev {e['id']} attempts: " + ", ".join(
            f"{a['strategy']}={a['status']}{'/pptx' if a['is_pptx'] else ''}" for a in e["attempts"]))
    names_ok = all(r["names_unique"] for r in c3["per_revision"])
    named = sum(r["google_named"] for r in c3["per_revision"])
    total = sum(r["elements"] for r in c3["per_revision"])
    print(f"[3] identity: {named}/{total} elements Google-named, names unique every rev: {names_ok}")
    if c3["per_revision"]:
        print(f"      sample names: {c3['per_revision'][-1]['sample_names']}")
    for s in c3["adjacent_stability"]:
        print(f"      {s['from']}->{s['to']}: kept {s['kept']} added {s['added']} removed {s['removed']} "
              f"(ratio {s['kept_ratio']})")
    print(f"      slides api: {c3['slides_api']}")
    print(f"      sample slide: {c3.get('sample_slide')}")
    if c4.get("ran"):
        print(f"[4] probe gap {c4['gap']}: checked {c4['checked']}, found {c4['found_pptx']} pptx, "
              f"{c4['distinct_contents']} distinct; statuses "
              f"{sorted({str(r['status']) for r in c4['results']})}")
    else:
        print(f"[4] probe skipped: {c4.get('reason')}")

    print("\nPer-revision changes:")
    for d in report["diffs"]:
        print(f"\n  {d['modifiedTime']}  {d['editor']}  ({d['from']} -> {d['to']}): {len(d['changes'])} changes")
        for c in d["changes"][:25]:
            extra = f" [{', '.join(c['what'])}]" if c.get("what") else ""
            text = f' "{c["text"]}"' if c.get("text") else ""
            print(f"    slide {c['slide']:>2} {c['change']:<8} {c['category']:<17}{extra}{text}")
        if len(d["changes"]) > 25:
            print(f"    … {len(d['changes']) - 25} more in report.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="Google Slides URL")
    parser.add_argument("--max-revisions", type=int, default=15, help="most recent N revisions to export (0 = all)")
    parser.add_argument("--probe", type=int, default=10, help="intermediate revision IDs to probe (0 = skip)")
    parser.add_argument("--out", default="data/pptx-export", help="output folder for pptx files + report.json")
    parser.add_argument("--token", help="Google OAuth access token (or set GOOGLE_ACCESS_TOKEN)")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print_summary(report)
    print(f"\nFull report: {os.path.join(args.out, 'report.json')}")


if __name__ == "__main__":
    main()
