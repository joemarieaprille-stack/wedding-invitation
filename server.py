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
INVITATIONS_FILE = DATA_DIR / "invitations.json"
DATABASE_URL = os.environ.get("DATABASE_URL")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
SESSION_LIFETIME = 8 * 60 * 60
MAX_BODY_SIZE = 16_384
MAX_PLUS_ONES = 20
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
RSVPS_LOCK = threading.Lock()
INVITATIONS_LOCK = threading.Lock()
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


def invitation_name_key(name):
    return " ".join(name.split()).casefold()


def read_invitations():
    if DATABASE_URL:
        with postgres_connection() as connection:
            rows = connection.execute(
                "SELECT id, name, name_key, allowed_plus_ones, created_at FROM invitations ORDER BY created_at, id"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "nameKey": row[2],
                "allowedPlusOnes": row[3],
                "createdAt": row[4],
            }
            for row in rows
        ]

    if not INVITATIONS_FILE.exists():
        return []
    with INVITATIONS_FILE.open("r", encoding="utf-8") as invitations_file:
        return json.load(invitations_file)


def write_invitations(invitations):
    if DATABASE_URL:
        with postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM invitations")
                cursor.executemany(
                    """
                    INSERT INTO invitations (id, name, name_key, allowed_plus_ones, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            invitation["id"],
                            invitation["name"],
                            invitation["nameKey"],
                            invitation["allowedPlusOnes"],
                            invitation["createdAt"],
                        )
                        for invitation in invitations
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
            prefix="invitations-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(invitations, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        temporary_path.replace(INVITATIONS_FILE)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def valid_plus_one_limit(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_PLUS_ONES


def invitation_has_rsvp(invitation, rsvps):
    return any(
        rsvp.get("inviteId") == invitation["id"]
        or (
            not rsvp.get("inviteId")
            and invitation_name_key(rsvp.get("fullName", "")) == invitation["nameKey"]
        )
        for rsvp in rsvps
    )


def invitation_response(invitation, rsvps):
    rsvp = next(
        (
            guest for guest in rsvps
            if guest.get("inviteId") == invitation["id"]
            or (
                not guest.get("inviteId")
                and invitation_name_key(guest.get("fullName", "")) == invitation["nameKey"]
            )
        ),
        None,
    )
    return {
        "id": invitation["id"],
        "name": invitation["name"],
        "allowedPlusOnes": invitation["allowedPlusOnes"],
        "rsvpStatus": rsvp.get("attendance") if rsvp else None,
        "requestedPlusOnes": len(rsvp.get("plusOnes", [])) if rsvp else 0,
    }


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
        return None, f"You may request up to {MAX_PLUS_ONES} additional guests."
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
        '<tr><td style="padding:0;line-height:0;">'
        '<img src="cid:invitation-email-header" width="420" '
        'alt="Wedding RSVP. Your RSVP is finalized. Aprille and Joemarie wedding artwork." '
        'style="display:block;width:100%;max-width:420px;height:auto;border:0;">'
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
        with (PUBLIC_DIR / "images" / "email-header.png").open("rb") as header_file:
            header_image = header_file.read()
    except OSError:
        LOG.exception("Could not read the RSVP email header artwork.")
        return {
            "sent": False,
            "reason": "The decision was saved, but the email header artwork could not be loaded.",
        }
    html_part.add_related(
        header_image,
        maintype="image",
        subtype="png",
        cid="<invitation-email-header>",
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
        try:
            error_payload = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = {}
        google_error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
        if isinstance(google_error, dict):
            google_status = google_error.get("status", "unknown")
            error_items = google_error.get("errors", [])
            if not isinstance(error_items, list):
                error_items = []
            google_reasons = [
                item["reason"]
                for item in error_items
                if isinstance(item, dict) and isinstance(item.get("reason"), str)
            ]
        elif isinstance(google_error, str):
            google_status = google_error
            google_reasons = []
        else:
            google_status = "unknown"
            google_reasons = []
        service = (
            "Gmail API"
            if error.url.startswith("https://gmail.googleapis.com/")
            else "Google OAuth token endpoint"
        )
        LOG.error(
            "%s request failed with HTTP status %s (Google status: %s; reason: %s).",
            service,
            error.code,
            google_status,
            ", ".join(google_reasons) or "unknown",
        )
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
        if route == "/api/admin/invitations":
            try:
                with RSVPS_LOCK, INVITATIONS_LOCK:
                    invitations = read_invitations()
                    rsvps = read_rsvps()
                    json_response(
                        self,
                        200,
                        {"invitations": [invitation_response(item, rsvps) for item in invitations]},
                    )
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not read invitation records.")
                json_response(self, 500, {"error": "The invitation list could not be loaded."})
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
        if route == "/api/invitations/lookup":
            try:
                body = self.read_json_body()
            except RequestError as error:
                json_response(self, error.status, {"error": str(error)})
                return
            if not valid_string(body.get("name"), 120):
                json_response(self, 400, {"error": "Please provide your full name."})
                return
            name_key = invitation_name_key(body["name"])
            try:
                with INVITATIONS_LOCK:
                    invitation = next(
                        (item for item in read_invitations() if item["nameKey"] == name_key),
                        None,
                    )
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not look up an invitation.")
                json_response(self, 500, {"error": "Your invitation could not be checked. Please try again."})
                return
            json_response(
                self,
                200,
                {
                    "invited": invitation is not None,
                    "allowedPlusOnes": invitation["allowedPlusOnes"] if invitation else 0,
                },
            )
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
                with RSVPS_LOCK, INVITATIONS_LOCK:
                    invitations = read_invitations()
                    name_key = invitation_name_key(rsvp["fullName"])
                    invitation = next(
                        (item for item in invitations if item["nameKey"] == name_key),
                        None,
                    )
                    if invitation is None:
                        json_response(
                            self,
                            403,
                            {"error": "We couldn't find an invitation for this name. Only invited guests can RSVP."},
                        )
                        return
                    if len(rsvp["plusOnes"]) > invitation["allowedPlusOnes"]:
                        json_response(
                            self,
                            400,
                            {
                                "error": (
                                    f"Your invitation allows up to "
                                    f"{invitation['allowedPlusOnes']} additional guests."
                                )
                            },
                        )
                        return
                    rsvps = read_rsvps()
                    if invitation_has_rsvp(invitation, rsvps):
                        json_response(
                            self,
                            409,
                            {"error": "An RSVP has already been received for this invitation. Please contact the host if you need to make a change."},
                        )
                        return
                    rsvp["inviteId"] = invitation["id"]
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

        if route == "/api/admin/invitations":
            try:
                body = self.read_json_body()
            except RequestError as error:
                json_response(self, error.status, {"error": str(error)})
                return
            if not valid_string(body.get("name"), 120):
                json_response(self, 400, {"error": "Please provide the invitee's full name."})
                return
            if not valid_plus_one_limit(body.get("allowedPlusOnes")):
                json_response(self, 400, {"error": f"Allowed plus-ones must be between 0 and {MAX_PLUS_ONES}."})
                return
            name = body["name"].strip()
            name_key = invitation_name_key(name)
            invitation = {
                "id": str(uuid.uuid4()),
                "name": name,
                "nameKey": name_key,
                "allowedPlusOnes": body["allowedPlusOnes"],
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            try:
                with RSVPS_LOCK, INVITATIONS_LOCK:
                    invitations = read_invitations()
                    if any(item["nameKey"] == name_key for item in invitations):
                        json_response(self, 409, {"error": "An invitation for this name already exists."})
                        return
                    rsvps = read_rsvps()
                    existing_rsvp = next(
                        (
                            item for item in rsvps
                            if not item.get("inviteId")
                            and invitation_name_key(item.get("fullName", "")) == name_key
                        ),
                        None,
                    )
                    if (
                        existing_rsvp
                        and len(existing_rsvp.get("plusOnes", [])) > invitation["allowedPlusOnes"]
                    ):
                        json_response(
                            self,
                            409,
                            {
                                "error": (
                                    f"This guest already requested "
                                    f"{len(existing_rsvp['plusOnes'])} additional guests."
                                )
                            },
                        )
                        return
                    for rsvp in rsvps:
                        if (
                            not rsvp.get("inviteId")
                            and invitation_name_key(rsvp.get("fullName", "")) == name_key
                        ):
                            rsvp["inviteId"] = invitation["id"]
                    invitations.append(invitation)
                    write_invitations(invitations)
                    if any(rsvp.get("inviteId") == invitation["id"] for rsvp in rsvps):
                        write_rsvps(rsvps)
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not add an invitation.")
                json_response(self, 500, {"error": "The invitation could not be added."})
                return
            json_response(self, 201, {"invitation": invitation})
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

        invitation_match = re.fullmatch(r"/api/admin/invitations/([a-f0-9-]+)", route, re.IGNORECASE)
        if invitation_match:
            try:
                body = self.read_json_body()
            except RequestError as error:
                json_response(self, error.status, {"error": str(error)})
                return
            if not valid_string(body.get("name"), 120):
                json_response(self, 400, {"error": "Please provide the invitee's full name."})
                return
            if not valid_plus_one_limit(body.get("allowedPlusOnes")):
                json_response(self, 400, {"error": f"Allowed plus-ones must be between 0 and {MAX_PLUS_ONES}."})
                return
            invitation_id = invitation_match.group(1)
            name = body["name"].strip()
            name_key = invitation_name_key(name)
            try:
                with RSVPS_LOCK, INVITATIONS_LOCK:
                    invitations = read_invitations()
                    invitation = next((item for item in invitations if item["id"] == invitation_id), None)
                    if invitation is None:
                        json_response(self, 404, {"error": "That invitation could not be found."})
                        return
                    if any(
                        item["id"] != invitation_id and item["nameKey"] == name_key
                        for item in invitations
                    ):
                        json_response(self, 409, {"error": "An invitation for this name already exists."})
                        return
                    rsvps = read_rsvps()
                    for rsvp in rsvps:
                        if (
                            not rsvp.get("inviteId")
                            and invitation_name_key(rsvp.get("fullName", "")) == invitation["nameKey"]
                        ):
                            rsvp["inviteId"] = invitation_id
                    requested_count = sum(
                        len(rsvp.get("plusOnes", []))
                        for rsvp in rsvps
                        if rsvp.get("inviteId") == invitation_id
                    )
                    if body["allowedPlusOnes"] < requested_count:
                        json_response(
                            self,
                            409,
                            {"error": f"This invitation already has {requested_count} requested additional guests."},
                        )
                        return
                    invitation["name"] = name
                    invitation["nameKey"] = name_key
                    invitation["allowedPlusOnes"] = body["allowedPlusOnes"]
                    write_invitations(invitations)
                    write_rsvps(rsvps)
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not update an invitation.")
                json_response(self, 500, {"error": "The invitation could not be updated."})
                return
            json_response(self, 200, {"invitation": invitation})
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

        invitation_match = re.fullmatch(r"/api/admin/invitations/([a-f0-9-]+)", route, re.IGNORECASE)
        if invitation_match:
            invitation_id = invitation_match.group(1)
            try:
                with RSVPS_LOCK, INVITATIONS_LOCK:
                    invitations = read_invitations()
                    invitation = next((item for item in invitations if item["id"] == invitation_id), None)
                    if invitation is None:
                        json_response(self, 404, {"error": "That invitation could not be found."})
                        return
                    if invitation_has_rsvp(invitation, read_rsvps()):
                        json_response(
                            self,
                            409,
                            {"error": "This guest already has an RSVP. Delete their RSVP before removing the invitation."},
                        )
                        return
                    write_invitations(
                        [item for item in invitations if item["id"] != invitation_id]
                    )
            except (OSError, json.JSONDecodeError, StorageError):
                LOG.exception("Could not remove an invitation.")
                json_response(self, 500, {"error": "The invitation could not be removed."})
                return
            json_response(self, 200, {"ok": True, "id": invitation_id})
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
        if decoded_route == "/print-assets/attendees.png":
            static_root = ROOT / "asset"
            relative_path = "ATTENDEES.png"
        else:
            static_root = PUBLIC_DIR
            relative_path = "index.html" if decoded_route == "/" else decoded_route.lstrip("/")
        file_path = (static_root / relative_path).resolve()
        try:
            file_path.relative_to(static_root.resolve())
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
            "Cache-Control": "no-store",
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    allowed_plus_ones INTEGER NOT NULL,
                    created_at TEXT NOT NULL
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
