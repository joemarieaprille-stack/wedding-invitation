import base64
import hmac
from html import escape as escape_html
import json
import logging
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
RSVP_FILE = DATA_DIR / "rsvps.json"
DATABASE_URL = os.environ.get("DATABASE_URL")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
SESSION_LIFETIME = 8 * 60 * 60
MAX_BODY_SIZE = 16_384
MAX_PLUS_ONES = 4
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
RSVPS_LOCK = threading.Lock()
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^[+()\d\s.-]{7,40}$")
LOG = logging.getLogger("wedding_invitation")


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class StorageError(Exception):
    pass


@contextmanager
def postgres_connection():
    try:
        import psycopg
    except ImportError as error:
        raise StorageError("PostgreSQL support is missing. Install the project requirements.") from error

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=10) as connection:
            yield connection
    except psycopg.Error as error:
        raise StorageError("Could not complete the RSVP database operation.") from error


def postgres_json(value):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as error:
        raise StorageError("PostgreSQL support is missing. Install the project requirements.") from error
    return Jsonb(value)


def json_response(handler, status, payload, extra_headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_rsvps():
    if DATABASE_URL:
        with postgres_connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM rsvps ORDER BY submitted_at, id"
            ).fetchall()
        return [upgrade_rsvp(row[0]) for row in rows]

    if not RSVP_FILE.exists():
        return []
    with RSVP_FILE.open("r", encoding="utf-8") as rsvp_file:
        return [upgrade_rsvp(rsvp) for rsvp in json.load(rsvp_file)]


def upgrade_rsvp(rsvp):
    upgraded = dict(rsvp)
    if "plusOnes" not in upgraded:
        companions = []
        if upgraded.get("plusOneRequested") and upgraded.get("plusOneName"):
            status = upgraded.get("plusOneStatus", "pending")
            companions.append({
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{upgraded.get('id', '')}:plus-one:0")),
                "name": upgraded["plusOneName"],
                "status": status if status in ("pending", "approved", "declined", "removed") else "pending",
            })
        upgraded["plusOnes"] = companions
        upgraded["plusOneReviewFinalized"] = bool(
            upgraded.get("reviewedAt") or any(item["status"] != "pending" for item in companions)
        )
    else:
        upgraded["plusOnes"] = [
            {
                "id": item.get("id") or str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{upgraded.get('id', '')}:plus-one:{index}")
                ),
                "name": item.get("name", ""),
                "status": item.get("status")
                if item.get("status") in ("pending", "approved", "declined", "removed")
                else "pending",
            }
            for index, item in enumerate(upgraded["plusOnes"])
        ]
        upgraded["plusOneReviewFinalized"] = upgraded.get("plusOneReviewFinalized", False)
    return upgraded


def write_rsvps(rsvps):
    if DATABASE_URL:
        with postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM rsvps")
                cursor.executemany(
                    "INSERT INTO rsvps (id, submitted_at, payload) VALUES (%s, %s, %s)",
                    [
                        (rsvp["id"], rsvp.get("submittedAt", ""), postgres_json(rsvp))
                        for rsvp in rsvps
                    ],
                )
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=DATA_DIR,
            prefix="rsvps-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(rsvps, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        temporary_path.replace(RSVP_FILE)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def valid_string(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= maximum


def valid_optional_string(value, maximum):
    return value is None or (isinstance(value, str) and len(value) <= maximum)


def normalize_rsvp(body):
    if not valid_string(body.get("fullName"), 120):
        return None, "Please provide your full name."

    email = (body.get("email") or "").strip()
    phone = (body.get("phone") or "").strip()
    if email and not EMAIL_PATTERN.fullmatch(email):
        return None, "Please provide a valid email address or phone number."
    if phone and not PHONE_PATTERN.fullmatch(phone):
        return None, "Please provide a valid email address or phone number."
    if not email and not phone:
        return None, "Please provide a valid email address or phone number."

    attendance = body.get("attendance")
    if attendance not in ("attending", "declining"):
        return None, "Please choose whether you can attend."
    if not valid_optional_string(body.get("dietaryNotes"), 1000):
        return None, "Dietary notes must be 1,000 characters or fewer."
    if not valid_optional_string(body.get("songRequest"), 300):
        return None, "Song requests must be 300 characters or fewer."
    plus_one_names = body.get("plusOnes")
    if plus_one_names is None:
        legacy_requested = body.get("plusOneRequested", False)
        if not isinstance(legacy_requested, bool):
            return None, "Please check the plus-one request option."
        legacy_name = body.get("plusOneName", "")
        if attendance == "attending" and legacy_requested and not valid_string(legacy_name, 120):
            return None, "Please provide the requested guest's name."
        if legacy_requested and not valid_optional_string(body.get("plusOneNote"), 300):
            return None, "The relationship or note must be 300 characters or fewer."
        plus_one_names = [legacy_name] if legacy_requested and legacy_name else []
    if not isinstance(plus_one_names, list) or len(plus_one_names) > MAX_PLUS_ONES:
        return None, "You may request up to four additional guests."
    if attendance == "attending" and any(not valid_string(name, 120) for name in plus_one_names):
        return None, "Please provide a name for each requested guest."

    plus_ones = [
        {"id": str(uuid.uuid4()), "name": name.strip(), "status": "pending"}
        for name in plus_one_names
    ] if attendance == "attending" else []
    if plus_ones and not email:
        return None, "Please provide an email address to receive the final guest list."

    return {
        "id": str(uuid.uuid4()),
        "fullName": body["fullName"].strip(),
        "email": email.lower(),
        "phone": phone,
        "attendance": attendance,
        "dietaryNotes": (body.get("dietaryNotes") or "").strip(),
        "songRequest": (body.get("songRequest") or "").strip(),
        "plusOnes": plus_ones,
        "plusOneReviewFinalized": False,
        "submittedAt": datetime.now(timezone.utc).isoformat(),
    }, None


def send_final_status_email(rsvp):
    gmail_user = os.environ.get("GMAIL_USER")
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not all((gmail_user, client_id, client_secret, refresh_token)):
        return {"sent": False, "reason": "Email notifications are not configured."}
    if not rsvp.get("email"):
        return {"sent": False, "reason": "No email address was provided for this RSVP."}

    attendance = "attending" if rsvp["attendance"] == "attending" else "declining"
    approved = [item["name"] for item in rsvp["plusOnes"] if item["status"] == "approved"]
    declined = [item["name"] for item in rsvp["plusOnes"] if item["status"] == "declined"]
    lines = [
        f"Hi {rsvp['fullName']},",
        "",
        f"Your RSVP is finalized: {attendance}.",
        "",
        "Approved guests:",
        *((f"- {name}" for name in approved) if approved else ["- None"]),
        "",
        "Not approved:",
        *((f"- {name}" for name in declined) if declined else ["- None"]),
        "",
        "We look forward to celebrating with you.",
    ]
    subject = "Your final wedding RSVP status"
    message = "\n".join(lines)
    approved_items = "".join(f"<li>{escape_html(name)}</li>" for name in approved) or "<li>None</li>"
    declined_items = "".join(f"<li>{escape_html(name)}</li>" for name in declined) or "<li>None</li>"
    html_message = (
        '<!doctype html><html><body style="margin:0;padding:0;background-color:#f4eee5;'
        'font-family:Arial,Helvetica,sans-serif;color:#30271d;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;background-color:#f4eee5;"><tr><td align="center" style="padding:16px 10px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;max-width:420px;background-color:#fffdf9;border:1px solid #e8dfd1;">'
        '<tr><td width="100%" height="210" background="cid:invitation-email-background" '
        'style="height:210px;padding:0 16px 0 40%;background-color:#fffdf9;'
        'background-image:url(cid:invitation-email-background);background-position:center;'
        'background-repeat:no-repeat;background-size:cover;text-align:center;vertical-align:middle;">'
        '<p style="margin:0 0 8px;color:#805600;font-size:10px;font-weight:bold;'
        'letter-spacing:1.5px;text-transform:uppercase;">Wedding RSVP</p>'
        '<h1 style="margin:0;font-family:Georgia,serif;font-size:22px;line-height:1.2;'
        'font-weight:bold;color:#30271d;">Your RSVP is finalized</h1>'
        '</td></tr><tr><td style="padding:24px 20px 26px;">'
        f'<p style="margin:0 0 12px;font-size:16px;line-height:1.5;">Hi {escape_html(rsvp["fullName"])},</p>'
        f'<p style="margin:0 0 20px;font-size:15px;line-height:1.6;">Your response is '
        f'<strong>{attendance}</strong>. Here is the final decision for your requested guests.</p>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td style="padding:14px 14px;background-color:#faf5eb;border-bottom:1px solid #eee5d8;">'
        '<h2 style="margin:0 0 8px;font-family:Georgia,serif;font-size:17px;line-height:1.3;font-weight:normal;">Approved guests</h2>'
        f'<ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.8;overflow-wrap:anywhere;">{approved_items}</ul>'
        '</td></tr><tr><td style="padding:14px 14px;background-color:#fffdf9;border-bottom:1px solid #eee5d8;">'
        '<h2 style="margin:0 0 8px;font-family:Georgia,serif;font-size:17px;line-height:1.3;font-weight:normal;">Not approved</h2>'
        f'<ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.8;overflow-wrap:anywhere;">{declined_items}</ul>'
        '</td></tr></table>'
        '<p style="margin:22px 0 0;font-family:Georgia,serif;font-size:16px;line-height:1.6;text-align:center;">'
        'We look forward to celebrating with you.</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )

    email = EmailMessage()
    email["From"] = gmail_user
    email["To"] = rsvp["email"]
    email["Subject"] = subject
    email.set_content(message)
    email.add_alternative(html_message, subtype="html")
    html_part = email.get_body(preferencelist=("html",))
    try:
        with (PUBLIC_DIR / "images" / "email-background.png").open("rb") as background_file:
            background_image = background_file.read()
    except OSError:
        LOG.exception("Could not read the RSVP email background image.")
        return {
            "sent": False,
            "reason": "The decision was saved, but the email design image could not be loaded.",
        }
    html_part.add_related(
        background_image,
        maintype="image",
        subtype="png",
        cid="<invitation-email-background>",
        filename="email-background.png",
        disposition="inline",
    )

    try:
        token_request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urlencode({
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(token_request, timeout=15) as response:
            token_payload = json.loads(response.read())
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            LOG.error("Gmail API token exchange returned no access token.")
            return {
                "sent": False,
                "reason": "The decision was saved, but the email account could not be authorized.",
            }

        gmail_request = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=json.dumps({
                "raw": base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(gmail_request, timeout=15) as response:
            if not 200 <= response.status < 300:
                LOG.error("Gmail API returned status %s.", response.status)
                return {
                    "sent": False,
                    "reason": "The decision was saved, but the email could not be sent.",
                }
    except urllib.error.HTTPError as error:
        LOG.error("Gmail API request failed with HTTP status %s.", error.code)
        return {
            "sent": False,
            "reason": "The decision was saved, but the email account could not send the notification.",
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        LOG.exception("Gmail API notification request failed.")
        return {
            "sent": False,
            "reason": "The decision was saved, but the email could not be sent.",
        }
    return {"sent": True}


class WeddingRequestHandler(BaseHTTPRequestHandler):
    server_version = "WeddingInvitation"

    def log_message(self, format_string, *args):
        LOG.info("%s - %s", self.address_string(), format_string % args)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/host":
            self.serve_static("/")
            return
        if route == "/api/wedding":
            try:
                with (DATA_DIR / "wedding.json").open("r", encoding="utf-8") as wedding_file:
                    wedding = json.load(wedding_file)
            except (OSError, json.JSONDecodeError):
                LOG.exception("Could not load wedding configuration.")
                json_response(self, 500, {"error": "The wedding details could not be loaded."})
                return
            json_response(
                self,
                200,
                wedding,
                {"Cache-Control": "no-cache"},
            )
            return

        if route.startswith("/api/admin/") and not self.is_admin():
            json_response(self, 401, {"error": "Please sign in to the host dashboard."})
            return
        if route == "/api/admin/rsvps":
            try:
                json_response(self, 200, {"rsvps": read_rsvps()})
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not read RSVP records.")
                json_response(self, 500, {"error": "The guest list could not be loaded."})
            return
        self.serve_static(route)

    def do_POST(self):
        route = urlsplit(self.path).path
        if route.startswith("/api/") and not self.valid_origin():
            json_response(self, 403, {"error": "Request origin is not allowed."})
            return
        if route == "/api/rsvps":
            try:
                body = self.read_json_body()
            except RequestError as error:
                json_response(self, error.status, {"error": str(error)})
                return
            rsvp, error = normalize_rsvp(body)
            if error:
                json_response(self, 400, {"error": error})
                return
            try:
                with RSVPS_LOCK:
                    rsvps = read_rsvps()
                    rsvps.append(rsvp)
                    write_rsvps(rsvps)
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not save RSVP.")
                json_response(self, 500, {"error": "Your RSVP could not be saved. Please try again."})
                return
            json_response(self, 201, {"ok": True, "id": rsvp["id"]})
            return

        if route == "/api/admin/login":
            configured_password = os.environ.get("ADMIN_PASSWORD") or "host123"
            if not configured_password:
                json_response(
                    self,
                    503,
                    {"error": "Host access is not configured. Set ADMIN_PASSWORD on the server."},
                )
                return
            try:
                body = self.read_json_body()
            except RequestError as error:
                json_response(self, error.status, {"error": str(error)})
                return
            submitted = body.get("password")
            if not isinstance(submitted, str) or not hmac.compare_digest(
                submitted.encode("utf-8"),
                configured_password.encode("utf-8"),
            ):
                json_response(self, 401, {"error": "That host password isn't correct."})
                return

            token = secrets.token_urlsafe(32)
            with SESSIONS_LOCK:
                current_time = time.time()
                for expired_token in [
                    key for key, expires_at in SESSIONS.items() if expires_at < current_time
                ]:
                    del SESSIONS[expired_token]
                SESSIONS[token] = current_time + SESSION_LIFETIME
            cookie = f"host_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_LIFETIME}"
            if os.environ.get("HTTPS") == "1":
                cookie += "; Secure"
            json_response(self, 200, {"ok": True}, {"Set-Cookie": cookie})
            return

        if route == "/api/admin/logout":
            token = self.session_token()
            with SESSIONS_LOCK:
                SESSIONS.pop(token, None)
            json_response(
                self,
                200,
                {"ok": True},
                {"Set-Cookie": "host_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"},
            )
            return

        json_response(self, 404, {"error": "Not found."})

    def do_PATCH(self):
        route = urlsplit(self.path).path
        if route.startswith("/api/") and not self.valid_origin():
            json_response(self, 403, {"error": "Request origin is not allowed."})
            return
        if not route.startswith("/api/admin/") or not self.is_admin():
            json_response(self, 401, {"error": "Please sign in to the host dashboard."})
            return

        match = re.fullmatch(r"/api/admin/rsvps/([a-f0-9-]+)/plus-ones", route, re.IGNORECASE)
        if not match:
            json_response(self, 404, {"error": "Not found."})
            return
        try:
            body = self.read_json_body()
        except RequestError as error:
            json_response(self, error.status, {"error": str(error)})
            return
        decisions = body.get("decisions")
        if not isinstance(decisions, list):
            json_response(self, 400, {"error": "Please provide a decision for each requested guest."})
            return
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item.get("decision") not in ("approved", "declined")
            for item in decisions
        ):
            json_response(self, 400, {"error": "Each guest must be approved or declined."})
            return
        decision_map = {item["id"]: item["decision"] for item in decisions}
        if len(decision_map) != len(decisions):
            json_response(self, 400, {"error": "Each requested guest must have one decision."})
            return

        try:
            with RSVPS_LOCK:
                rsvps = read_rsvps()
                rsvp = next((guest for guest in rsvps if guest["id"] == match.group(1)), None)
                if rsvp is None or not rsvp.get("plusOnes"):
                    json_response(self, 404, {"error": "That plus-one request could not be found."})
                    return
                companion_ids = {item["id"] for item in rsvp["plusOnes"]}
                if set(decision_map) != companion_ids:
                    json_response(self, 400, {"error": "Please decide on every requested guest."})
                    return
                if rsvp.get("plusOneReviewFinalized"):
                    json_response(self, 409, {"error": "These guest decisions have already been finalized."})
                    return
                for item in rsvp["plusOnes"]:
                    item["status"] = decision_map[item["id"]]
                rsvp["plusOneReviewFinalized"] = True
                rsvp["reviewedAt"] = datetime.now(timezone.utc).isoformat()
                write_rsvps(rsvps)
                reviewed_rsvp = dict(rsvp)
        except (OSError, json.JSONDecodeError, StorageError):
            LOG.exception("Could not save plus-one decision.")
            json_response(self, 500, {"error": "The decision could not be saved."})
            return

        notification = send_final_status_email(reviewed_rsvp)
        json_response(self, 200, {"rsvp": reviewed_rsvp, "notification": notification})

    def do_DELETE(self):
        route = urlsplit(self.path).path
        if route.startswith("/api/") and not self.valid_origin():
            json_response(self, 403, {"error": "Request origin is not allowed."})
            return
        if not route.startswith("/api/admin/") or not self.is_admin():
            json_response(self, 401, {"error": "Please sign in to the host dashboard."})
            return

        match = re.fullmatch(r"/api/admin/rsvps/([a-f0-9-]+)", route, re.IGNORECASE)
        if not match:
            json_response(self, 404, {"error": "Not found."})
            return

        try:
            with RSVPS_LOCK:
                rsvps = read_rsvps()
                remaining = [guest for guest in rsvps if guest["id"] != match.group(1)]
                if len(remaining) == len(rsvps):
                    json_response(self, 404, {"error": "That RSVP could not be found."})
                    return
                write_rsvps(remaining)
        except (OSError, json.JSONDecodeError, StorageError):
            LOG.exception("Could not delete RSVP.")
            json_response(self, 500, {"error": "The RSVP could not be deleted."})
            return

        json_response(self, 200, {"ok": True, "id": match.group(1)})

    def do_HEAD(self):
        route = urlsplit(self.path).path
        self.serve_static(route, head_only=True)

    def read_json_body(self):
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise RequestError(415, "Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise RequestError(400, "Invalid request body length.") from error
        if length < 0 or length > MAX_BODY_SIZE:
            raise RequestError(413, "Request body is too large.")
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RequestError(400, "Request body must be valid JSON.") from error
        if not isinstance(body, dict):
            raise RequestError(400, "Request body must be a JSON object.")
        return body

    def session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        morsel = cookie.get("host_session")
        return morsel.value if morsel else ""

    def is_admin(self):
        token = self.session_token()
        with SESSIONS_LOCK:
            expires_at = SESSIONS.get(token)
            if not token or expires_at is None or expires_at < time.time():
                SESSIONS.pop(token, None)
                return False
        return True

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        expected_host = self.headers.get("Host", "").lower()
        return parsed.scheme in ("http", "https") and parsed.netloc.lower() == expected_host

    def serve_static(self, route, head_only=False):
        decoded_route = unquote(route)
        relative_path = "index.html" if decoded_route == "/" else decoded_route.lstrip("/")
        file_path = (PUBLIC_DIR / relative_path).resolve()
        try:
            file_path.relative_to(PUBLIC_DIR.resolve())
        except ValueError:
            json_response(self, 403, {"error": "Forbidden."})
            return

        try:
            body = file_path.read_bytes()
        except FileNotFoundError:
            json_response(self, 404, {"error": "Not found."})
            return
        except OSError:
            LOG.exception("Could not serve static file.")
            json_response(self, 500, {"error": "The requested file could not be served."})
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        headers = {
            "Content-Type": content_type or "application/octet-stream",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' https://images.unsplash.com data:; "
                "style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; script-src 'self'; "
                "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
            ),
        }
        self.send_response(200)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if DATABASE_URL:
        with postgres_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rsvps (
                    id TEXT PRIMARY KEY,
                    submitted_at TEXT NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
        LOG.info("Using PostgreSQL for RSVP storage.")
    else:
        LOG.info("DATABASE_URL is not set; using the local RSVP file.")
    server = ThreadingHTTPServer((HOST, PORT), WeddingRequestHandler)
    LOG.info("Wedding invitation ready at http://localhost:%s", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Shutting down wedding invitation server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
