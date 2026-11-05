
import json
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = Path("credentials/token.json")
OUTPUT_FILE = Path("output/permissions_raw.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

PRESENTATION_ID = "1n-UtYz_pCoUk_fPlTmf58mQdPUgFMrcWBX2Kwaxlxy8"


def main():
    credentials = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )

    drive = build("drive", "v3", credentials=credentials)

    response = drive.permissions().list(
        fileId=PRESENTATION_ID,
        fields="permissions(id,type,emailAddress,displayName,role,deleted)",
        supportsAllDrives=True,
    ).execute()

    permissions = response.get("permissions", [])

    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(permissions, file, indent=2)

    print(f"Found {len(permissions)} permission records.")

    for permission in permissions:
        print(
            permission.get("displayName", "Unknown"),
            "|",
            permission.get("id", "No ID"),
            "|",
            permission.get("role", "Unknown role"),
        )


if __name__ == "__main__":
    main()