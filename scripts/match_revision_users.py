
import json
from pathlib import Path
from collections import defaultdict

REVISION_FILE = Path("output/revisions_raw.json")
ACTOR_FILE = Path("output/actor_profiles.json")


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def main():
    revisions_data = load_json(REVISION_FILE)
    actors_data = load_json(ACTOR_FILE)

    revisions = (
        revisions_data
        if isinstance(revisions_data, list)
        else revisions_data.get("revisions", [])
    )

    actor_ids = {
        item["person_resource"].removeprefix("people/")
        for item in actors_data
    }

    matches = defaultdict(list)

    for revision in revisions:
        user = revision.get("lastModifyingUser", {})
        permission_id = user.get("permissionId")
        display_name = user.get("displayName", "Unknown")

        if permission_id and permission_id in actor_ids:
            matches[permission_id].append(display_name)

    print("\nACTOR ID MATCH RESULTS\n")

    for actor_id in sorted(actor_ids):
        names = sorted(set(matches.get(actor_id, [])))

        if names:
            print(f"{actor_id} -> {', '.join(names)}")
        else:
            print(f"{actor_id} -> No revision metadata match")


if __name__ == "__main__":
    main()
