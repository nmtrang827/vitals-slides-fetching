
import json
from pathlib import Path
from datetime import datetime, timezone

ACTIVITY_FILE = Path("output/activity_raw.json")
REVISION_FILE = Path("output/revisions_raw.json")
OUTPUT_FILE = Path("output/timestamp_matches.txt")

# A match within this window is worth investigating,
# but does NOT prove that the actor created the revision.
MAX_GAP_MINUTES = 10


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_records(data, key):
    if isinstance(data, list):
        return data
    return data.get(key, [])


def parse_time(value):
    if not value:
        return None

    if isinstance(value, dict):
        value = value.get("timestamp")

    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def main():
    activities = get_records(load_json(ACTIVITY_FILE), "activities")
    revisions = get_records(load_json(REVISION_FILE), "revisions")

    edit_events = []

    for activity in activities:
        if "edit" not in activity.get("primaryActionDetail", {}):
            continue

        activity_time = parse_time(activity.get("timestamp"))
        if activity_time is None:
            continue

        for actor in activity.get("actors", []):
            known = actor.get("user", {}).get("knownUser", {})
            person = known.get("personName", "Unknown")

            edit_events.append({
                "time": activity_time,
                "actor": person,
            })

    edit_events.sort(key=lambda event: event["time"])

    lines = []
    lines.append("REVISION / ACTIVITY TIMESTAMP CORRELATION")
    lines.append(f"Activity EDIT actor records: {len(edit_events)}")
    lines.append(f"Saved revisions: {len(revisions)}")
    lines.append(f"Maximum candidate time gap: {MAX_GAP_MINUTES} minutes")
    lines.append("")

    for revision in revisions:
        revision_time = parse_time(revision.get("modifiedTime"))

        if revision_time is None:
            continue

        user = revision.get("lastModifyingUser", {})
        revision_name = user.get("displayName", "Unknown")
        revision_id = revision.get("id", "Unknown")

        candidates = []

        for event in edit_events:
            gap = abs((event["time"] - revision_time).total_seconds())

            if gap <= MAX_GAP_MINUTES * 60:
                candidates.append((gap, event))

        candidates.sort(key=lambda item: item[0])

        lines.append(
            f"Revision {revision_id} | {revision_name} | "
            f"{revision_time.isoformat()}"
        )

        if not candidates:
            lines.append("  No EDIT activity within the time window.")
        else:
            for gap, event in candidates:
                lines.append(
                    f"  Candidate: {event['actor']} | "
                    f"Activity: {event['time'].isoformat()} | "
                    f"Gap: {gap / 60:.1f} minutes"
                )

        lines.append("")

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\nSaved report to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
