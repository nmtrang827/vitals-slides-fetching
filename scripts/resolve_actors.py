
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

ROOT = Path(__file__).resolve().parent.parent

TOKEN_FILE = ROOT / "credentials" / "token.json"
INPUT_FILE = ROOT / "output" / "activity_raw.json"
OUTPUT_FILE = ROOT / "output" / "actor_profiles.json"

SCOPES = [
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.activity.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]


def get_edit_actor_ids(activities):
    """Collect unique Google actor IDs from edit events."""
    person_names = set()

    for activity in activities:
        if "edit" not in activity.get("primaryActionDetail", {}):
            continue

        for actor in activity.get("actors", []):
            known_user = actor.get("user", {}).get("knownUser", {})
            person_name = known_user.get("personName")

            if person_name:
                person_names.add(person_name)

    return sorted(person_names)


def resolve_person(directory, person_resource):
    """Resolve an Activity API person resource through Directory API."""
    user_id = person_resource.split("/")[-1]

    result = {
        "person_resource": person_resource,
        "display_name": None,
        "status": "unresolved",
        "error_code": None,
        "error_message": None,
    }

    try:
        user = directory.users().get(
            userKey=user_id,
            viewType="domain_public",
            projection="basic",
        ).execute()

        name = user.get("name", {}).get("fullName")

        if name:
            result["display_name"] = name
            result["status"] = "resolved"
        else:
            result["status"] = "no_display_name"

    except HttpError as error:
        result["error_code"] = error.resp.status
        result["error_message"] = error.content.decode(
            "utf-8", errors="replace"
        )

        if error.resp.status == 403:
            result["status"] = "access_denied"
        elif error.resp.status == 404:
            result["status"] = "not_found"
        else:
            result["status"] = "api_error"

    except Exception as error:
        result["status"] = "unexpected_error"
        result["error_message"] = str(error)

    return result


def main():
    with INPUT_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    activities = (
        data if isinstance(data, list)
        else data.get("activities", [])
    )

    person_names = get_edit_actor_ids(activities)

    print(f"Found {len(person_names)} distinct edit actors.")

    creds = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    directory = build(
        "admin",
        "directory_v1",
        credentials=creds,
    )

    results = []

    for person_name in person_names:
        result = resolve_person(directory, person_name)
        results.append(result)

        print(
            f"{person_name} -> "
            f"{result['display_name'] or result['status']}"
        )

        if result["error_message"]:
            print("  Details:", result["error_message"])

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2, ensure_ascii=False)

    resolved = sum(
        result["status"] == "resolved" for result in results
    )

    print(f"\nResolved: {resolved}/{len(results)} actors")
    print(f"Saved results to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
