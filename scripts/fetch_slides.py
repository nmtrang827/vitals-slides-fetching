import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


ROOT_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = ROOT_DIR / "credentials"
OUTPUT_DIR = ROOT_DIR / "output"

CLIENT_SECRET_PATH = CREDENTIALS_DIR / "client_secret.json"
TOKEN_PATH = CREDENTIALS_DIR / "token.json"

PRESENTATION_ID = "1n-UtYz_pCoUk_fPlTmf58mQdPUgFMrcWBX2Kwaxlxy8"

SCOPES = [
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.activity.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]


def authenticate():
    """Authenticate using a local Desktop OAuth client."""
    credentials = None

    if TOKEN_PATH.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_PATH),
            SCOPES,
        )

    if not credentials or not credentials.valid:
        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(Request())
        else:
            if not CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"OAuth client file not found: {CLIENT_SECRET_PATH}"
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_PATH),
                SCOPES,
            )

            credentials = flow.run_local_server(port=0)

        # Restrict access to the local token file where supported.
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    return credentials


def extract_text(element):
    """Extract text from a Slides page element when available."""
    parts = []

    shape = element.get("shape")
    if shape:
        text_content = shape.get("text", {})
        for text_element in text_content.get("textElements", []):
            text_run = text_element.get("textRun")
            if text_run:
                parts.append(text_run.get("content", ""))

    table = element.get("table")
    if table:
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                cell_parts = []
                for text_element in cell.get("text", {}).get(
                    "textElements", []
                ):
                    text_run = text_element.get("textRun")
                    if text_run:
                        cell_parts.append(text_run.get("content", ""))

                parts.append("".join(cell_parts))

    # Groups can contain nested page elements.
    group = element.get("elementGroup")
    if group:
        for child in group.get("children", []):
            child_text = extract_text(child)
            if child_text:
                parts.append(child_text)

    return "".join(parts)


def build_summary(presentation):
    """Build a readable text summary of the presentation."""
    lines = [
        f"Title: {presentation.get('title', '(untitled)')}",
        f"Presentation ID: {presentation.get('presentationId', '')}",
        f"Page size: {presentation.get('pageSize', {})}",
        f"Fetched at (UTC): {datetime.now(timezone.utc).isoformat()}",
    ]

    slides = presentation.get("slides", [])
    lines.append(f"Number of slides: {len(slides)}")
    lines.append("")

    for index, slide in enumerate(slides, start=1):
        slide_id = slide.get("objectId", "(unknown)")
        lines.append("=" * 72)
        lines.append(f"Slide {index}")
        lines.append(f"Slide ID: {slide_id}")

        elements = slide.get("pageElements", [])
        lines.append(f"Page elements: {len(elements)}")
        lines.append("")

        for element_index, element in enumerate(elements, start=1):
            text = extract_text(element).strip()
            if text:
                lines.append(f"[Element {element_index}]")
                lines.append(text)
                lines.append("")

    return "\n".join(lines) + "\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Authenticating with Google...")
    credentials = authenticate()

    print("Fetching presentation...")
    service = build(
        "slides",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )

    try:
        presentation = (
            service.presentations()
            .get(presentationId=PRESENTATION_ID)
            .execute()
        )

    except HttpError as error:
        print(f"Google API request failed: {error}", file=sys.stderr)
        print(
            "Check presentation access, API enablement, and OAuth permissions.",
            file=sys.stderr,
        )
        return 1
    

    raw_path = OUTPUT_DIR / "presentation_raw.json"
    summary_path = OUTPUT_DIR / "presentation_summary.txt"

    raw_path.write_text(
        json.dumps(presentation, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary_path.write_text(
        build_summary(presentation),
        encoding="utf-8",
    )

    slides = presentation.get("slides", [])

    print("\nFetch successful!")
    print(f"Title: {presentation.get('title', '(untitled)')}")
    print(f"Presentation ID: {presentation.get('presentationId')}")
    print(f"Slides: {len(slides)}")
    print(f"Raw JSON: {raw_path}")
    print(f"Readable summary: {summary_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HttpError as error:
        print(f"Google API error: {error}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)