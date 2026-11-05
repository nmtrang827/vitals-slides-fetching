
import json
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from fetch_slides import authenticate, PRESENTATION_ID

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch_activity():
    credentials = authenticate()

    activity_service = build(
        "driveactivity",
        "v2",
        credentials=credentials,
        cache_discovery=False,
    )

    all_activities = []
    page_token = None

    try:
        while True:
            request_body = {
                "itemName": f"items/{PRESENTATION_ID}",
                "pageSize": 100,
                "consolidationStrategy": {
                    "none": {}
                },
            }

            if page_token:
                request_body["pageToken"] = page_token

            response = activity_service.activity().query(
                body=request_body
            ).execute()

            all_activities.extend(
                response.get("activities", [])
            )

            page_token = response.get("nextPageToken")

            if not page_token:
                break

        output_path = OUTPUT_DIR / "activity_raw.json"

        with output_path.open("w", encoding="utf-8") as file:
            json.dump(
                {"activities": all_activities},
                file,
                indent=2,
                ensure_ascii=False,
            )

        print(f"Activities returned: {len(all_activities)}")
        print(f"Saved activity data to: {output_path}")

        for activity in all_activities:
            timestamp = activity.get("timestamp", "Unknown time")

            if "timeRange" in activity:
                time_range = activity["timeRange"]
                timestamp = (
                    f"{time_range.get('startTime')} - "
                    f"{time_range.get('endTime')}"
                )

            for action in activity.get("actions", []):
                detail = action.get("detail", {})
                action_type = next(iter(detail), "Unknown")

                actor = action.get("actor", {})
                user = actor.get("user", {}).get("knownUser", {})
                name = user.get("personName", "Unknown user")

                print(
                    timestamp,
                    "|",
                    action_type,
                    "|",
                    name,
                )

    except HttpError as error:
        print(f"Google Drive Activity API error: {error}")


if __name__ == "__main__":
    fetch_activity()
