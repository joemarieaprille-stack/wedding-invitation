# Wedding invitation

A mobile-first interactive wedding invitation with:

- A phone-width layout on desktop that fills the screen on mobile
- Animated envelope opening
- Invitation appears after the envelope opens
- Configurable invitation details (names, date, venue, schedule, dress code)
- Host-managed invite list with a per-invitation limit of up to 20 named additional guests
- Guest RSVP form that checks invitation eligibility by name and enforces each invitation's plus-one limit
- Host dashboard to selectively approve or decline each requested guest
- Final itemized RSVP notifications via Resend

## Run locally

Requires Python 3.9+. Local file-based use needs no packages; PostgreSQL storage uses the dependency in `requirements.txt`.

```powershell
$env:ADMIN_PASSWORD = "choose-a-private-host-password"
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
# or: python server.py
```

The execution-policy bypass applies only to the PowerShell process that starts the app.

Open [http://localhost:8080](http://localhost:8080). The server is already designed for mobile touch (envelope tap + fog wipe).

Guests use the invitation link. The host dashboard is separate at [http://localhost:8080/host](http://localhost:8080/host) when running locally. The dashboard password comes from the server environment and is never embedded in the client.

The host dashboard shows RSVP requests that still need review by default. RSVPs with no additional guest requests appear under **Finalized** automatically, as do requests after guest decisions are finalized. Guests who cannot attend appear in a separate **Can't attend** tab.

The host dashboard also manages the invite list. Add each invitee with an allowed number of additional guests (0–20). Guests who are not on the list cannot RSVP, and each invited guest's plus-one limit is checked again on the server when an RSVP is submitted. Local invitation records are stored in [`data/invitations.json`](./data/invitations.json); when `DATABASE_URL` is configured, invitation records are stored in PostgreSQL.

## Customize for another wedding

Edit [`data/wedding.json`](./data/wedding.json):

| Field | Purpose |
| --- | --- |
| `couple` | First / second / display names |
| `date`, `time` | Celebration timing |
| `venue` | Name, address, map link |
| `photo`, `photoCaption` | Hero image + caption |
| `welcome`, `message` | Opening copy |
| `schedule` | Itinerary rows |
| `dressCode` | Attire guidance |
| `rsvpBy` | RSVP deadline |

Locally, guest replies are stored in [`data/rsvps.json`](./data/rsvps.json). On Render, set the `DATABASE_URL` environment variable to a Render Postgres internal URL to store RSVPs in the database. The app creates its RSVP table automatically when it starts.

If you host the couple photo somewhere other than `images.unsplash.com`, update the `img-src` directive in `server.py`.

## Final RSVP notifications

When a host finalizes all requested guest decisions, the final RSVP and itemized approved/not-approved guest list are saved first. If Gmail API is configured, the primary guest receives a phone-friendly email with the RSVP title composed into the invitation artwork and displayed inline above the itemized guest-decision list. The notification does not include an invitation link. Guests requesting additional attendees should provide an email address.

For Gmail API, create Google OAuth credentials with the Gmail `gmail.send` scope and a refresh token. In the web service's private environment settings, set `GMAIL_USER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN`. Never put OAuth credentials or tokens in source control or share them.

Without all Gmail API settings, decisions are still saved and the dashboard reports that no email was sent.
If Gmail rejects a notification, Render logs include Google's error status and reason without logging credentials or tokens.

## Production notes

- Serve over HTTPS
- Use a strong unique `ADMIN_PASSWORD`
- Set `DATABASE_URL` on the web service to a persistent database; Render's free Postgres databases expire after 30 days
- Behind a TLS-terminating proxy, set `HOST=0.0.0.0` and `HTTPS=1` so the host session cookie is marked Secure

Host sessions are held in memory and will expire when the web service restarts. Locally, RSVPs use a file; deployed RSVPs use PostgreSQL when `DATABASE_URL` is configured.
