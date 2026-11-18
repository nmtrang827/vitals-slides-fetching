
import csv
import json

from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent

ACTIVITY_FILE = ROOT / "output" / "activity_raw.json"
PROFILES_FILE = ROOT / "output" / "actor_profiles.json"
OUTPUT_FILE = ROOT / "output" / "edit_attribution.csv"

def to_chicago_time(timestamp):
    if not timestamp:
        return ""

    utc_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    chicago_time = utc_time.astimezone(ZoneInfo("America/Chicago"))

    return chicago_time.strftime("%Y-%m-%d %I:%M:%S %p")

def main():
    with ACTIVITY_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    with PROFILES_FILE.open("r", encoding="utf-8") as file:
        profiles = json.load(file)

    activities = (
        data if isinstance(data, list)
        else data.get("activities", [])
    )

    actor_names = {
        profile["person_resource"]: profile["display_name"]
        for profile in profiles
        if profile.get("status") == "resolved"
        and profile.get("display_name")
    }

    rows = []

    for activity in activities:
        # Only include actual edit events.
        if "edit" not in activity.get("primaryActionDetail", {}):
            continue

        timestamp = activity.get("timestamp", "")

        actor_ids = sorted({
            actor["user"]["knownUser"]["personName"]
            for actor in activity.get("actors", [])
            if actor.get("user", {}).get("knownUser", {}).get("personName")
        })

        if not actor_ids:
            rows.append({
                "timestamp_utc": timestamp,
                "timestamp_chicago": to_chicago_time(timestamp),
                "editor": "Unknown",
                "person_resource": "",
            })
            continue

        names = [
            actor_names.get(actor_id, "Unknown")
            for actor_id in actor_ids
        ]

        rows.append({
            "timestamp_utc": timestamp,
            "timestamp_chicago": to_chicago_time(timestamp),
            "editor": "; ".join(names),
            "person_resource": "; ".join(actor_ids),
        })

    rows.sort(key=lambda row: row["timestamp_utc"])

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "timestamp_utc",
            "timestamp_chicago",
            "editor",
            "person_resource",
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["editor"] for row in rows)

    print(f"Total edit events: {len(rows)}")
    print(f"Unique editors: {len(counts)}")
    print("\nEdits by editor:")

    for editor, count in counts.most_common():
        print(f"  {editor}: {count}")

    print(f"\nSaved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
