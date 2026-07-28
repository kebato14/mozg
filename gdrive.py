"""
Загрузка файлов в Google Drive через OAuth2
"""

import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", os.path.join(os.path.dirname(__file__), "token.json"))
_secrets_token = "/app/secrets/token.json"
if os.path.exists(_secrets_token):
    TOKEN_FILE = _secrets_token

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Кэшируем сервис — не пересоздаём при каждом запросе
_service_cache = None


def get_drive_service():
    global _service_cache
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        _service_cache = None  # сбрасываем кэш при обновлении токена
    if _service_cache is None:
        _service_cache = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service_cache


# Кэш ID подпапок: {название: folder_id}
_folder_cache = {}


def get_or_create_folder(name: str, parent_id: str = None) -> str:
    """Находит подпапку по имени внутри parent_id или создаёт её. Возвращает folder_id."""
    parent = parent_id or FOLDER_ID
    cache_key = f"{parent}/{name}"
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    service = get_drive_service()

    # Ищем существующую папку
    query = (
        f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent}' in parents and trashed = false"
    )
    resp = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    files = resp.get("files", [])

    if files:
        folder_id = files[0]["id"]
    else:
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent],
        }
        folder = service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]

    _folder_cache[cache_key] = folder_id
    return folder_id


def upload_file(local_path: str, filename: str, mimetype: str = "image/jpeg", folder_name: str = None) -> dict:
    service = get_drive_service()

    file_size = os.path.getsize(local_path)

    # Определяем родительскую папку: подпапка категории или корневая
    if folder_name:
        parent_id = get_or_create_folder(folder_name)
    else:
        parent_id = FOLDER_ID

    metadata = {"name": filename}
    if parent_id:
        metadata["parents"] = [parent_id]

    # Для файлов до 5MB — простая загрузка (быстрее)
    # Для больших — resumable с оптимальным chunk
    if file_size < 5 * 1024 * 1024:
        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=False)
    else:
        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True,
                                chunksize=2 * 1024 * 1024)  # 2MB chunks

    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    # Открываем доступ по ссылке
    service.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"}
    ).execute()

    return {
        "id": file["id"],
        "url": file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}/view")
    }
