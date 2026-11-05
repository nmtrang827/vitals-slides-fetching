
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

INPUT_FILE = Path("output/activity_raw.json")
CSV_FILE = Path("output/activity_edits.csv")
SUMMARY_FILE = Path("output/activity_summary.txt")


def get_activities(data):
    """Handle either a raw API response or a saved list of activities."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("activities", [])
    return []


def get_actor_info(actor):
    user = actor.get("user", {})

    known_user = user.get("knownUser", {})
    if known_user:
        return (
            known_user.get("personName", "Unknown"),
            known_user.get("isCurrentUser", False),
        )

    unknown_user = user.get("unknownUser", {})
    if unknown_user:
        return ("Unknown Google user", False)

    return ("Unknown actor", False)


def get_action_type(activity):
    detail = activity.get("primaryActionDetail", {})
    if detail:
        return next(iter(detail), "unknown")
    return "unknown"


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Could not find {INPUT_FILE}")

    with INPUT_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    activities = get_activities(data)

    edit_rows = []
    actor_counts = Counter()
    actor_timestamps = defaultdict(list)

    for activity in activities:
        action_type = get_action_type(activity)

        if action_type != "edit":
            continue

        timestamp = activity.get("timestamp", "")
        actors = activity.get("actors", [])

        for actor in actors:
            person_name, is_current_user = get_actor_info(actor)

            row = {
                "timestamp": timestamp,
                "actor_id": person_name,
                "is_current_user": is_current_user,
                "action": action_type,
            }

            edit_rows.append(row)
            actor_counts[person_name] += 1
            actor_timestamps[person_name].append(timestamp)

    edit_rows.sort(key=lambda row: row["timestamp"])

    with CSV_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "actor_id",
                "is_current_user",
                "action",
            ],
        )
        writer.writeheader()
        writer.writerows(edit_rows)

    with SUMMARY_FILE.open("w", encoding="utf-8") as file:
        file.write(f"Total activities: {len(activities)}\n")
        file.write(f"Total EDIT actor records: {len(edit_rows)}\n")
        file.write(f"Distinct EDIT actors: {len(actor_counts)}\n\n")

        file.write("EDIT activity records by actor:\n")
        for actor, count in actor_counts.most_common():
            timestamps = sorted(actor_timestamps[actor])
            first = timestamps[0] if timestamps else "N/A"
            last = timestamps[-1] if timestamps else "N/A"

            file.write(f"\nActor: {actor}\n")
            file.write(f"  Event records: {count}\n")
            file.write(f"  First activity: {first}\n")
            file.write(f"  Last activity:  {last}\n")

    print("Activity processing complete!")
    print(f"Total activities: {len(activities)}")
    print(f"EDIT actor records: {len(edit_rows)}")
    print(f"Distinct EDIT actors: {len(actor_counts)}")
    print(f"\nSaved: {CSV_FILE}")
    print(f"Saved: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
