"""
Одноразовая авторизация Google OAuth2.
Запустите один раз: python3 auth_google.py
"""

import os
import json
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDS_FILE = os.path.join(os.path.dirname(__file__), "google_credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
REDIRECT_URI = "http://localhost:8080"

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>&#10003; \xd0\x90\xd0\xb2\xd1\x82\xd0\xbe\xd1\x80\xd0\xb8\xd0\xb7\xd0\xb0\xd1\x86\xd0\xb8\xd1\x8f \xd1\x83\xd1\x81\xd0\xbf\xd0\xb5\xd1\x88\xd0\xbd\xd0\xb0! \xd0\x9c\xd0\xbe\xd0\xb6\xd0\xbd\xd0\xbe \xd0\xb7\xd0\xb0\xd0\xba\xd1\x80\xd1\x8b\xd1\x82\xd1\x8c \xd0\xb2\xd0\xba\xd0\xbb\xd0\xb0\xd0\xb4\xd0\xba\xd1\x83.</h2>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    global auth_code

    with open(CREDS_FILE) as f:
        raw = json.load(f)

    client_config = raw.get("web") or raw.get("installed")
    if not client_config:
        print("❌ Неверный формат credentials.json")
        return

    flow = Flow.from_client_config(
        {"web": client_config} if "web" in raw else {"installed": client_config},
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    print(f"\n🌐 Открываю браузер для авторизации...")
    print(f"Если браузер не открылся — перейдите вручную:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Запускаем локальный сервер для получения кода
    server = HTTPServer(("localhost", 8080), CallbackHandler)
    server.timeout = 120

    print("⏳ Ожидаю авторизацию (2 минуты)...")
    server.handle_request()

    if not auth_code:
        print("❌ Код авторизации не получен.")
        return

    flow.fetch_token(code=auth_code)
    creds = flow.credentials

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Авторизация успешна! Токен сохранён в token.json")


if __name__ == "__main__":
    main()
