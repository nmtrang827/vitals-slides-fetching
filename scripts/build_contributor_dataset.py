
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"


def load_json(filename):
    path = OUTPUT / filename

    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_timestamp(value):
    """Normalize API timestamps into ISO-8601 strings."""
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        seconds = value.get("seconds")
        nanos = value.get("nanos", 0)

        if seconds is not None:
            dt = datetime.fromtimestamp(
                int(seconds) + int(nanos) / 1_000_000_000,
                tz=timezone.utc
            )
            return dt.isoformat().replace("+00:00", "Z")

    return None


def get_actor_ids(actors):
    """Extract canonical Google Activity actor IDs."""
    actor_ids = []

    if not isinstance(actors, list):
        return actor_ids

    for actor in actors:
        if not isinstance(actor, dict):
            continue

        user = actor.get("user", {})
        known_user = user.get("knownUser", {})
        person_name = known_user.get("personName")

        if person_name:
            actor_ids.append(person_name)

    return actor_ids


def get_action_type(activity):
    detail = activity.get("primaryActionDetail", {})

    if isinstance(detail, dict) and detail:
        return next(iter(detail.keys()))

    return "unknown"


def load_actor_profiles():
    path = OUTPUT / "actor_profiles.json"

    if not path.exists():
        return {}

    data = load_json("actor_profiles.json")

    if isinstance(data, dict):
        return data

    return {}


def main():
    activities = load_json("activity_raw.json")
    revisions = load_json("revisions_raw.json")
    profiles = load_actor_profiles()

    if isinstance(activities, dict):
        activities = activities.get("activities", [])

    if isinstance(revisions, dict):
        revisions = revisions.get("revisions", [])

    contributors = defaultdict(lambda: {
        "actor_id": None,
        "display_name": None,
        "identity_status": "unresolved",
        "event_counts": Counter(),
        "first_activity": None,
        "last_activity": None,
    })

    activity_rows = []

    for activity in activities:
        action_type = get_action_type(activity)
        activity_timestamp = get_timestamp(activity.get("timestamp"))

        # Prefer action-level actor/timestamp if available.
        actions = activity.get("actions", [])

        action_records = []

        for action in actions:
            action_type_from_record = (
                next(iter(action.keys()))
                if isinstance(action, dict) and action
                else action_type
            )

            action_records.append({
                "action_type": action_type_from_record,
                "timestamp": get_timestamp(action.get("timestamp"))
                    if isinstance(action, dict) else None,
                "actors": action.get("actors")
                    if isinstance(action, dict) else None,
            })

        if not action_records:
            action_records = [{
                "action_type": action_type,
                "timestamp": activity_timestamp,
                "actors": activity.get("actors", []),
            }]

        for record in action_records:
            timestamp = record["timestamp"] or activity_timestamp
            actor_list = record["actors"]

            if actor_list is None:
                actor_list = activity.get("actors", [])

            actor_ids = get_actor_ids(actor_list)

            for actor_id in actor_ids:
                contributor = contributors[actor_id]
                contributor["actor_id"] = actor_id

                profile = profiles.get(actor_id, {})
                display_name = profile.get("display_name")

                if display_name:
                    contributor["display_name"] = display_name
                    contributor["identity_status"] = "resolved"

                contributor["event_counts"][record["action_type"]] += 1

                if timestamp:
                    if (
                        contributor["first_activity"] is None
                        or timestamp < contributor["first_activity"]
                    ):
                        contributor["first_activity"] = timestamp

                    if (
                        contributor["last_activity"] is None
                        or timestamp > contributor["last_activity"]
                    ):
                        contributor["last_activity"] = timestamp

                activity_rows.append({
                    "timestamp": timestamp,
                    "actor_id": actor_id,
                    "action_type": record["action_type"],
                })

    # Export contributor records.
    contributor_rows = []

    for actor_id in sorted(contributors):
        contributor = contributors[actor_id]

        contributor_rows.append({
            "actor_id": actor_id,
            "display_name": contributor["display_name"],
            "identity_status": contributor["identity_status"],
            "event_counts": dict(contributor["event_counts"]),
            "first_activity": contributor["first_activity"],
            "last_activity": contributor["last_activity"],
        })

    with (OUTPUT / "contributors.json").open("w", encoding="utf-8") as f:
        json.dump(contributor_rows, f, indent=2, ensure_ascii=False)

    # Export individual activity events.
    with (OUTPUT / "activity_events.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        fieldnames = ["timestamp", "actor_id", "action_type"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(activity_rows)

    # Keep revision authors separate from activity actors.
    revision_rows = []

    for revision in revisions:
        user = revision.get("lastModifyingUser", {})

        revision_rows.append({
            "revision_id": revision.get("id"),
            "modified_time": get_timestamp(
                revision.get("modifiedTime")
            ),
            "author_display_name": user.get("displayName"),
            "author_permission_id": user.get("permissionId"),
        })

    with (OUTPUT / "revision_events.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        fieldnames = [
            "revision_id",
            "modified_time",
            "author_display_name",
            "author_permission_id",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(revision_rows)

    print(f"Discovered contributors: {len(contributor_rows)}")
    print(f"Activity event records: {len(activity_rows)}")
    print(f"Revision records: {len(revision_rows)}")
    print("\nGenerated:")
    print("  output/contributors.json")
    print("  output/activity_events.csv")
    print("  output/revision_events.csv")


if __name__ == "__main__":
    main()
