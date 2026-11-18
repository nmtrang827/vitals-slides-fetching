import json
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from fetch_slides import authenticate, PRESENTATION_ID

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch_revisions():
    credentials = authenticate()

    drive_service = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    
    try:
        revision = drive_service.revisions().get(
            fileId=PRESENTATION_ID,
            revisionId="535",
            fields="id,modifiedTime,lastModifyingUser,exportLinks",
        ).execute()

        print("\n--- Single Revision Test ---")
        print("Revision ID:", revision.get("id"))
        print("Modified:", revision.get("modifiedTime"))
        print(
            "Editor:",
            revision.get("lastModifyingUser", {}).get(
                "displayName", "Unknown user"
            ),
        )
        print("Export formats:", list(revision.get("exportLinks", {}).keys()))

    except HttpError as error:
        print(f"Single revision test failed: {error}")

    all_revisions = []
    page_token = None

    try:
        while True:
            response = drive_service.revisions().list(
                fileId=PRESENTATION_ID,
                pageSize=1000,
                fields=(
                    "nextPageToken,"
                    "revisions("
                    "id,"
                    "modifiedTime,"
                    "lastModifyingUser("
                    "displayName,emailAddress,permissionId"
                    "),"
                    "exportLinks"
                    ")"
                ),
                pageToken=page_token,
            ).execute()

            all_revisions.extend(response.get("revisions", []))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        output_path = OUTPUT_DIR / "revisions_raw.json"

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(
                {"revisions": all_revisions},
                file,
                indent=2,
                ensure_ascii=False,
            )

        users = set()

        for revision in all_revisions:
            user = revision.get("lastModifyingUser", {})
            email = user.get("emailAddress")

            if email:
                users.add(email)

        print(f"Revisions returned: {len(all_revisions)}")
        print(f"Distinct identified users: {len(users)}")
        print(f"Saved revision data to: {output_path}")

        for revision in all_revisions:
            user = revision.get("lastModifyingUser", {})

            print(
                revision.get("modifiedTime"),
                "|",
                user.get("displayName", "Unknown user"),
                "|",
                user.get("emailAddress", "No email available"),
                "| Revision:",
                revision.get("id"),
            )

    except HttpError as error:
        print(f"Google Drive API error: {error}")


if __name__ == "__main__":
    fetch_revisions()