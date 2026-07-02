"""
Загрузка файлов в Google Drive через OAuth2
"""

import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", os.path.join(os.path.dirname(__file__), "token.json"))
# В Docker secrets монтируются в /app/secrets/
_secrets_token = "/app/secrets/token.json"
if os.path.exists(_secrets_token):
    TOKEN_FILE = _secrets_token
FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def upload_file(local_path: str, filename: str, mimetype: str = "image/jpeg") -> dict:
    service = get_drive_service()

    metadata = {"name": filename}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    service.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"}
    ).execute()

    return {
        "id": file["id"],
        "url": file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")
    }
