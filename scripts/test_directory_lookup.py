
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "credentials" / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]

# Test one actor first.
PERSON_RESOURCE = "people/100704987127655153750"

def main():
    creds = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    directory = build("admin", "directory_v1", credentials=creds)

    # Directory API expects a user ID, not a People resource name.
    user_id = PERSON_RESOURCE.split("/")[-1]

    try:
        user = directory.users().get(
            userKey=user_id,
            viewType="domain_public",
            projection="basic",
        ).execute()

        print("Lookup successful!")
        print("Name:", user.get("name", {}).get("fullName"))
        print("Directory ID:", user.get("id"))
        print("Status: resolved")

    except HttpError as error:
        print("Directory lookup failed.")
        print("HTTP status:", error.resp.status)
        print("Details:", error.content.decode("utf-8"))

if __name__ == "__main__":
    main()
