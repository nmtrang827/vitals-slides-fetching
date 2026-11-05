
import json
from pathlib import Path

INPUT_FILE = Path("output/activity_raw.json")

TARGETS = {
    "people/106874227402873951356",
    "people/109495600739852351449",
    "people/115392166139936982431",
}


def main():
    with INPUT_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    activities = (
        data if isinstance(data, list)
        else data.get("activities", [])
    )

    for activity in activities:
        if "edit" not in activity.get("primaryActionDetail", {}):
            continue

        for actor in activity.get("actors", []):
            known = actor.get("user", {}).get("knownUser", {})
            person = known.get("personName")

            if person in TARGETS:
                print("\nTimestamp:", activity.get("timestamp"))
                print("Actor metadata:")
                print(json.dumps(actor, indent=2))

                print("Action detail:")
                print(json.dumps(
                    activity.get("primaryActionDetail", {}),
                    indent=2
                ))


if __name__ == "__main__":
    main()
