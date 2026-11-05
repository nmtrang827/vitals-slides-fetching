
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

TOKEN_FILE = Path("credentials/token.json")
INPUT_FILE = Path("output/activity_raw.json")
OUTPUT_FILE = Path("output/actor_profiles.json")

SCOPES = [
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.activity.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def main():
    with INPUT_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    activities = data if isinstance(data, list) else data.get("activities", [])

    # Collect unique Google People resource names from EDIT events.
    person_names = set()

    for activity in activities:
        detail = activity.get("primaryActionDetail", {})
        if "edit" not in detail:
            continue

        for actor in activity.get("actors", []):
            known_user = actor.get("user", {}).get("knownUser", {})
            person_name = known_user.get("personName")

            if person_name:
                person_names.add(person_name)

    print(f"Found {len(person_names)} distinct actors.")

    creds = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("people", "v1", credentials=creds)

    results = []

    for person_name in sorted(person_names):
        result = {
            "person_resource": person_name,
            "display_name": None,
            "status": "unresolved",
        }

        try:
            profile = service.people().get(
                resourceName=person_name,
                personFields="names",
            ).execute()

            names = profile.get("names", [])

            if names:
                primary_name = next(
                    (name for name in names if name.get("metadata", {}).get("primary")),
                    names[0],
                )
                result["display_name"] = primary_name.get("displayName")
                result["status"] = "resolved"

        except HttpError as error:
            result["error"] = str(error)
            print(f"Could not resolve {person_name}: {error}")

        results.append(result)

        print(
            f"{person_name} -> "
            f"{result['display_name'] or result['status']}"
        )

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2, ensure_ascii=False)

    print(f"\nSaved results to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
