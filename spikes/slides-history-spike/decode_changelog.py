"""Decode a Google Slides internal changelog (downloaded with fetch_changelog.js).

Replays every op to learn, for each element: its kind code, the slide it lives on,
who created it and when; then attributes every later edit (text, move, style,
delete) to an author, slide and category.

Optionally validates the guessed kind-code -> category mapping against the real
deck via the Slides API (`presentations.get`).

    python -B decode_changelog.py examples/changelog-sample.json
    python -B decode_changelog.py data/<changelog>.json --token <access token>   # + validation

Writes <json stem>.decoded.json next to the input. Read-only.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

SESSION_GAP_MS = 30 * 60 * 1000

# Validated against presentations.get on a 51-slide deck (2026-11-17):
#   1 -> shape CUSTOM, 3 -> image, 8 -> shape ELLIPSE, 108 -> TEXT_BOX (incl. title/body
#   placeholders), 153 -> line, 158 -> notes-page SLIDE_IMAGE placeholder.
# Other codes look like further shape types (unverified); report as "kind:<n>".
KIND_GUESS = {
    1: "shape",
    3: "image",
    8: "shape",
    108: "text",
    153: "line",
    158: "slide_thumbnail",
}

OP_NAMES = {
    0: "delete", 2: "group", 3: "create", 4: "batch", 5: "update_props", 6: "transform",
    8: "reparent_or_order", 9: "replace_image", 12: "create_slide", 13: "slide_op_13",
    14: "slide_op_14", 15: "insert_text", 16: "delete_text", 17: "style_text", 18: "apply_layout",
    22: "create_table", 23: "table_op_23", 27: "table_col_widths", 28: "table_row_heights",
    30: "table_cell_style", 35: "op_35", 36: "op_36", 38: "op_38", 40: "snapshot", 41: "list_op",
    42: "op_42", 44: "op_44", 47: "op_47",
}


def flatten(op: Any) -> Iterable[list]:
    if not isinstance(op, list) or not op:
        return
    code = op[0]
    if code == 4 and isinstance(op[1], list):
        for sub in op[1]:
            yield from flatten(sub)
    elif code == 40 and isinstance(op[1], list):
        yield from flatten(op[1])
    else:
        yield op


def is_id(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("g") or value.startswith("p") or value.startswith("n:")
                                       or value.startswith("i") or value.startswith("SLIDES_API"))


def target_ids(op: list) -> List[str]:
    """Element ids an op touches (best effort, per observed shapes)."""
    code = op[0]
    if code in (0, 5, 8, 18) and isinstance(op[1], list):
        return [x for x in op[1] if isinstance(x, str)]
    if code == 9 and len(op) > 3 and isinstance(op[3], str):
        return [op[3]]
    if len(op) > 1 and isinstance(op[1], str):
        return [op[1]]
    return []


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


class Replay:
    def __init__(self, user_map: Dict[str, Any]):
        self.user_map = user_map
        self.elements: Dict[str, Dict[str, Any]] = {}
        self.slides: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.unknown_targets: Counter = Counter()
        self.op_counts: Counter = Counter()

    def author(self, author_id: str) -> str:
        info = self.user_map.get(author_id) or {}
        return "Anonymous" if info.get("anonymous") else info.get("name") or author_id

    def page_of(self, element_id: str) -> Optional[str]:
        seen = set()
        while element_id in self.elements and element_id not in seen:
            seen.add(element_id)
            parent = self.elements[element_id].get("page")
            if parent is None or parent in self.slides or parent.endswith(":notes"):
                return parent
            element_id = parent  # nested in a group
        return None

    def category_of(self, element_id: str, op_code: int) -> str:
        if op_code in (15, 16, 17, 41, 42):
            return "text"
        if element_id in self.slides:
            return "slide"  # page-level op, e.g. background image (op 9)
        el = self.elements.get(element_id)
        if not el:
            return "unknown"
        return el["category"]

    def apply(self, op: list, ts: int, author_id: str, revision: int):
        code = op[0]
        self.op_counts[code] += 1
        who = self.author(author_id)

        if code == 12 and len(op) > 1 and isinstance(op[1], str):
            self.slides.setdefault(op[1], {"created_by": who, "created_at": ts, "revision": revision,
                                           "layout": op[4] if len(op) > 4 else None})
            self._event(ts, who, revision, "create_slide", op[1], op[1], "slide")
            return
        if code == 3:
            element_id, kind = op[1], op[2]
            page = op[5] if len(op) > 5 and isinstance(op[5], str) else None
            category = KIND_GUESS.get(kind, f"kind:{kind}")
            if category == "shape" and _has_image_blob(op):
                category = "image"
            self.elements[element_id] = {"kind": kind, "category": category, "page": page,
                                         "created_by": who, "created_at": ts, "revision": revision}
            if page and page not in self.slides and not page.endswith(":notes") and page not in self.elements:
                self.slides.setdefault(page, {"created_by": None, "created_at": None, "revision": None,
                                              "inferred": True})
            self._event(ts, who, revision, "create", element_id, self.page_of(element_id), category)
            return
        if code == 2:  # group: [2, groupId, [children], transform, page]
            group_id = op[1]
            page = op[4] if len(op) > 4 and isinstance(op[4], str) else None
            self.elements[group_id] = {"kind": "group", "category": "group", "page": page,
                                       "created_by": who, "created_at": ts, "revision": revision}
            for child in op[2] if isinstance(op[2], list) else []:
                if child in self.elements:
                    self.elements[child]["page"] = group_id
            return
        if code == 22:  # create table
            element_id = op[1]
            page = next((x for x in reversed(op) if isinstance(x, str) and x != element_id), None)
            self.elements[element_id] = {"kind": "table", "category": "table", "page": page,
                                         "created_by": who, "created_at": ts, "revision": revision}
            self._event(ts, who, revision, "create", element_id, self.page_of(element_id), "table")
            return

        ids = target_ids(op)
        if not ids:
            self._event(ts, who, revision, OP_NAMES.get(code, f"op_{code}"), None, None, "other")
            return
        for element_id in ids:
            if element_id not in self.elements and element_id not in self.slides:
                self.unknown_targets[OP_NAMES.get(code, f"op_{code}")] += 1
            if code == 0:
                if element_id in self.elements:
                    self.elements[element_id]["deleted_by"] = who
                    self.elements[element_id]["deleted_at"] = ts
            if code == 8 and len(op) > 3 and isinstance(op[3], str) and element_id in self.elements:
                self.elements[element_id]["page_op8"] = op[3]
            detail = {}
            if code == 15 and len(op) > 4 and isinstance(op[4], str):
                detail["chars_added"] = len(op[4])
                detail["text"] = op[4]
            if code == 16 and len(op) > 4 and isinstance(op[3], int) and isinstance(op[4], int):
                detail["chars_removed"] = op[4] - op[3] + 1
            page = element_id if element_id in self.slides else self.page_of(element_id)
            self._event(ts, who, revision, OP_NAMES.get(code, f"op_{code}"), element_id, page,
                        self.category_of(element_id, code), **detail)

    def _event(self, ts, who, revision, action, element_id, page, category, **detail):
        self.events.append({"ts": ts, "author": who, "revision": revision, "action": action,
                            "element": element_id, "page": page, "category": category, **detail})


def _has_image_blob(op: list) -> bool:
    return "IMAGE" in json.dumps(op[4] if len(op) > 4 else None)


def sessions(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group events per (author, page, element) into work sessions split by 30 min idle."""
    out: List[Dict[str, Any]] = []
    open_by_key: Dict[tuple, Dict[str, Any]] = {}
    for ev in events:
        if not ev["page"]:
            continue
        key = (ev["author"], ev["page"], ev["element"], ev["category"])
        cur = open_by_key.get(key)
        if cur is None or ev["ts"] - cur["end"] > SESSION_GAP_MS:
            cur = {"author": ev["author"], "page": ev["page"], "element": ev["element"],
                   "category": ev["category"], "start": ev["ts"], "end": ev["ts"],
                   "actions": Counter(), "chars_added": 0, "chars_removed": 0, "text": "",
                   "first_revision": ev["revision"], "last_revision": ev["revision"]}
            open_by_key[key] = cur
            out.append(cur)
        cur["end"] = ev["ts"]
        cur["last_revision"] = ev["revision"]
        cur["actions"][ev["action"]] += 1
        cur["chars_added"] += ev.get("chars_added", 0)
        cur["chars_removed"] += ev.get("chars_removed", 0)
        if ev.get("text") and len(cur["text"]) < 200:
            cur["text"] += ev["text"]
    for s in out:
        s["actions"] = dict(s["actions"])
    return out


# ---------------------------------------------------------------- Slides API validation

def api_elements(token: str, presentation_id: str) -> Dict[str, Any]:
    resp = httpx.get(
        f"https://slides.googleapis.com/v1/presentations/{presentation_id}",
        params={"fields": "slides(objectId,pageElements,slideProperties(notesPage(objectId,pageElements)))"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    pres = resp.json()
    elements: Dict[str, Dict[str, Any]] = {}
    slide_order: List[str] = []

    def api_type(pe: Dict[str, Any]) -> str:
        if "shape" in pe:
            shape = pe["shape"]
            has_text = bool((shape.get("text") or {}).get("textElements"))
            placeholder = (shape.get("placeholder") or {}).get("type")
            return f"shape:{shape.get('shapeType')}" + (f"/ph:{placeholder}" if placeholder else "") + \
                ("/text" if has_text else "")
        for key in ("image", "line", "table", "video", "sheetsChart", "wordArt", "elementGroup", "speakerSpotlight"):
            if key in pe:
                return key
        return "other"

    def walk(pes, page, parent_group=None):
        for pe in pes or []:
            elements[pe["objectId"]] = {"page": page, "type": api_type(pe), "group": parent_group}
            if "elementGroup" in pe:
                walk(pe["elementGroup"].get("children"), page, pe["objectId"])

    for slide in pres.get("slides", []):
        slide_order.append(slide["objectId"])
        walk(slide.get("pageElements"), slide["objectId"])
        notes = (slide.get("slideProperties") or {}).get("notesPage") or {}
        walk(notes.get("pageElements"), f"{slide['objectId']}:notes")
    return {"elements": elements, "slide_order": slide_order}


def validate(replay: Replay, api: Dict[str, Any]) -> Dict[str, Any]:
    by_kind: Dict[Any, Counter] = defaultdict(Counter)
    page_match = Counter()
    for element_id, api_el in api["elements"].items():
        el = replay.elements.get(element_id)
        if not el:
            page_match["in_api_not_in_changelog"] += 1
            continue
        by_kind[el["kind"]][api_el["type"]] += 1
        replay_page = replay.page_of(element_id)
        page_match["page_match" if replay_page == api_el["page"] else "page_mismatch"] += 1
    alive = {k for k, v in replay.elements.items() if "deleted_at" not in v}
    return {
        "kind_code_to_api_types": {str(k): dict(v.most_common()) for k, v in by_kind.items()},
        "page_attribution": dict(page_match),
        "alive_in_changelog_not_in_api": len(alive - set(api["elements"]) - set(api["slide_order"])),
        "slides_in_api": len(api["slide_order"]),
        "slides_known_to_changelog": sum(1 for s in api["slide_order"] if s in replay.slides),
    }


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("changelog_json")
    parser.add_argument("--token", help="Google access token for Slides API validation (or GOOGLE_ACCESS_TOKEN)")
    parser.add_argument("--slides", type=int, default=6, help="slides to print in the timeline")
    args = parser.parse_args()

    with open(args.changelog_json, encoding="utf-8") as fh:
        data = json.load(fh)
    user_map = (data.get("tiles") or {}).get("userMap") or {}
    replay = Replay(user_map)
    for entry in data["changelog"]:
        op, ts, author_id, revision = entry[0], entry[1], entry[2], entry[3]
        for sub in flatten(op):
            replay.apply(sub, ts, author_id, revision)

    work = sessions(replay.events)
    attributed = sum(1 for e in replay.events if e["page"])
    report: Dict[str, Any] = {
        "docId": data.get("summary", {}).get("docId"),
        "ops_by_code": {f"{k} {OP_NAMES.get(k, '?')}": v for k, v in sorted(replay.op_counts.items())},
        "elements_created": len(replay.elements),
        "elements_by_kind": dict(Counter(str(e["kind"]) for e in replay.elements.values())),
        "elements_by_category": dict(Counter(e["category"] for e in replay.elements.values())),
        "slides_seen": len(replay.slides),
        "events": len(replay.events),
        "events_with_slide": attributed,
        "events_by_category": dict(Counter(e["category"] for e in replay.events)),
        "ops_on_unknown_elements": dict(replay.unknown_targets),
        "sessions": len(work),
    }

    token = (args.token or os.getenv("GOOGLE_ACCESS_TOKEN", "")).strip()
    slide_order: List[str] = []
    if token and report["docId"]:
        try:
            api = api_elements(token, report["docId"])
            slide_order = api["slide_order"]
            report["validation"] = validate(replay, api)
        except httpx.HTTPError as exc:
            report["validation"] = {"error": f"{type(exc).__name__}: {getattr(getattr(exc, 'response', None), 'status_code', '')}"}

    out_path = os.path.splitext(args.changelog_json)[0] + ".decoded.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"report": report, "slides": replay.slides, "elements": replay.elements,
                   "sessions": work}, fh, indent=1, default=str)

    print(json.dumps(report, indent=1))

    # Timeline for a few slides (API order when available, else creation order).
    order = slide_order or list(replay.slides)
    print("\nPer-slide work sessions (author, time, category, actions):")
    by_page = defaultdict(list)
    for s in work:
        by_page[s["page"]].append(s)
    for index, page in enumerate(order[: args.slides], start=1):
        rows = sorted(by_page.get(page, []), key=lambda s: s["start"])
        print(f"\n  slide {index} ({page}): {len(rows)} sessions")
        for s in rows[:12]:
            chars = f" +{s['chars_added']}/-{s['chars_removed']} chars" if s["chars_added"] or s["chars_removed"] else ""
            text = f' "{" ".join(s["text"].split())[:40]}"' if s["text"].strip() else ""
            print(f"    {fmt_ts(s['start'])}  {s['author'][:28]:<28} {s['category']:<10} "
                  f"{','.join(f'{k}x{v}' for k, v in s['actions'].items())}{chars}{text}")
        if len(rows) > 12:
            print(f"    … {len(rows) - 12} more")
    print(f"\nFull output: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
