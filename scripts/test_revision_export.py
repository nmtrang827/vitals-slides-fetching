from pathlib import Path

from googleapiclient.discovery import build
from google.auth.transport.requests import AuthorizedSession

from fetch_slides import authenticate, PRESENTATION_ID

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def test_revision_export(revision_id="1"):
    credentials = authenticate()

    # Get the export URL for this specific revision.
    drive_service = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    revision = drive_service.revisions().get(
        fileId=PRESENTATION_ID,
        revisionId=revision_id,
        fields="id,modifiedTime,lastModifyingUser,exportLinks",
    ).execute()

    export_url = revision["exportLinks"][
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ]

    # Request the export using your authenticated Google credentials.
    session = AuthorizedSession(credentials)
    response = session.get(export_url, allow_redirects=True)

    print("Requested revision:", revision_id)
    print("Editor:", revision.get("lastModifyingUser", {}).get("displayName"))
    print("HTTP status:", response.status_code)
    print("Content type:", response.headers.get("Content-Type"))
    print("Response size:", len(response.content), "bytes")
    print("Final URL:", response.url)

    if response.ok and response.content.startswith(b"PK"):
        output_path = OUTPUT_DIR / f"revision_{revision_id}.pptx"
        output_path.write_bytes(response.content)
        print("Saved file:", output_path)
    else:
        print("The response does not look like a PPTX file.")
        print("Response preview:", response.text[:300])


if __name__ == "__main__":
    test_revision_export("1")