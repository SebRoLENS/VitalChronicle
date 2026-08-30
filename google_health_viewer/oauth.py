from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlsplit

import keyring
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from keyring.errors import KeyringError
from platformdirs import user_config_dir

from .branding import APP_NAME
from .constants import OAUTH_REDIRECT_URI
from .external_links import open_external_url

SERVICE_NAME = "GoogleHealthViewer"
TOKEN_USER = "google-health-oauth"


class OAuthError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self) -> None:
        self.config_dir = Path(user_config_dir("GoogleHealthViewer", "SebastianoRomi"))
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
        except OSError:
            pass
        self.client_file = self.config_dir / "client_secret.json"
        self.token_file = self.config_dir / "authorized_user.json"

    @staticmethod
    def validate_client_file(path: Path) -> dict:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OAuthError("Il file selezionato non è un JSON di credenziali valido.") from exc
        client = content.get("web") or content.get("installed")
        if not client or not client.get("client_id") or not client.get("client_secret"):
            raise OAuthError("Il file non contiene un client OAuth 2.0 valido.")
        redirects = client.get("redirect_uris") or []
        if OAUTH_REDIRECT_URI not in redirects:
            raise OAuthError(
                "Nelle credenziali manca l'URI di reindirizzamento "
                f"{OAUTH_REDIRECT_URI}. Aggiungilo nel client OAuth e scarica di nuovo il JSON."
            )
        return content

    def import_client_file(self, source: Path) -> None:
        self.validate_client_file(source)
        shutil.copy2(source, self.client_file)
        try:
            os.chmod(self.client_file, 0o600)
        except OSError:
            pass

    def has_client(self) -> bool:
        return self.client_file.exists()

    def save_credentials(self, credentials: Credentials) -> bool:
        payload = credentials.to_json()
        try:
            keyring.set_password(SERVICE_NAME, TOKEN_USER, payload)
            if self.token_file.exists():
                self.token_file.unlink()
            return True
        except (KeyringError, RuntimeError):
            self.token_file.write_text(payload, encoding="utf-8")
            try:
                os.chmod(self.token_file, 0o600)
            except OSError:
                pass
            return False

    def load_credentials(self) -> Credentials | None:
        payload = None
        try:
            payload = keyring.get_password(SERVICE_NAME, TOKEN_USER)
        except (KeyringError, RuntimeError):
            pass
        if not payload and self.token_file.exists():
            payload = self.token_file.read_text(encoding="utf-8")
        if not payload:
            return None
        try:
            return Credentials.from_authorized_user_info(json.loads(payload))
        except (ValueError, json.JSONDecodeError, TypeError):
            return None

    def clear_credentials(self) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, TOKEN_USER)
        except (KeyringError, RuntimeError):
            pass
        if self.token_file.exists():
            self.token_file.unlink()


class _CallbackHandler(BaseHTTPRequestHandler):
    callback_path: str | None = None
    callback_event: Event

    def do_GET(self) -> None:
        type(self).callback_path = self.path
        body = (
            "<!doctype html><html lang='it'><meta charset='utf-8'>"
            f"<title>{APP_NAME}</title>"
            "<style>body{font-family:system-ui;margin:4rem;max-width:42rem}"
            "h1{color:#1769aa}</style>"
            "<h1>Autenticazione completata</h1>"
            f"<p>Puoi chiudere questa scheda e tornare a {APP_NAME}.</p>"
            "</html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).callback_event.set()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def authenticate(
    client_file: Path,
    scopes: list[str],
    on_authorization_url: Callable[[str], None] | None = None,
    timeout_seconds: int = 300,
) -> Credentials:
    """Run Google OAuth in the system browser and capture the localhost callback."""
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_secrets_file(client_file, scopes=scopes, state=state)
    flow.redirect_uri = OAUTH_REDIRECT_URI
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _CallbackHandler.callback_path = None
    _CallbackHandler.callback_event = Event()
    try:
        server = HTTPServer(("localhost", 8765), _CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            "La porta locale 8765 è occupata. Chiudi l'altro programma che la usa e riprova."
        ) from exc
    server.timeout = timeout_seconds
    if on_authorization_url:
        on_authorization_url(authorization_url)
    if not open_external_url(authorization_url):
        server.server_close()
        raise OAuthError(
            "Non riesco ad aprire il browser. Apri manualmente l'indirizzo mostrato dal programma."
        )
    server.handle_request()
    server.server_close()
    path = _CallbackHandler.callback_path
    if not path:
        raise OAuthError("Autenticazione scaduta o annullata.")
    parsed = urlsplit(path)
    params = parse_qs(parsed.query)
    if params.get("state", [None])[0] != state:
        raise OAuthError("Risposta OAuth non valida: parametro di sicurezza state errato.")
    if "error" in params:
        raise OAuthError(f"Accesso non autorizzato: {params['error'][0]}")
    response_url = OAUTH_REDIRECT_URI.rstrip("/") + path
    previous = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        flow.fetch_token(authorization_response=response_url)
    except Exception as exc:  # OAuth library exposes several unrelated exception types.
        raise OAuthError(f"Google non ha accettato l'autenticazione: {exc}") from exc
    finally:
        if previous is None:
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        else:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = previous
    return flow.credentials


def port_is_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("localhost", 8765))
        except OSError:
            return False
    return True
