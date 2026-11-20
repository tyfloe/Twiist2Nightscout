# twiist -> Nightscout bridge

Polls Tidepool (where twiist data lands once your My twiist Portal is linked) and uploads to Nightscout:
- CGM readings -> `entries` (sgv, mg/dL, with a computed trend arrow)
- Boluses (manual and automated) -> `treatments`
- Carb entries -> `treatments` (Carb Correction)
- Basal changes (automated, temp, scheduled, suspend) -> `treatments` (Temp Basal)

## Setup
## You will need:
Docker Desktop installed and running.
A Nightscout site, plus its API secret (or an access token).
At least one data source:
twiist: a Tidepool account with their My twiist Portal linked to it (app.tidepool.org, then Connect a Device Account, then twiist).
Eversense: a follower account that the sensor wearer has shared with, and an invite they’ve accepted.

## Setup continued:

Copy .env.example to .env.
Fill in the Nightscout section: NIGHTSCOUT_URL, and either NIGHTSCOUT_API_SECRET or NIGHTSCOUT_TOKEN.

For twiist, fill in TIDEPOOL_USER and TIDEPOOL_PASSWORD. Add TIDEPOOL_USER_ID only if they’re reading a shared account that isn’t their own.

For Eversense, fill in EVERSENSE_USER, EVERSENSE_PASSWORD, and EVERSENSE_REGION (US or OUS).

Optionally set POLL_SECONDS (default 120) and BACKFILL_HOURS (default 24, only applies on the first run).

From the project folder, run docker compose up -d --build.

Run docker compose logs -f and look for “Uploaded N ...” lines, then check that readings appear in Nightscout.

State (last uploaded timestamps) lives in the `ttn-data` volume, so restarts don't re-upload.
Run tests: `python -m unittest discover -s tests`.

## Notes
- Data is only as fresh as twiist's sync to Tidepool, so expect a delay versus the twiist app.
- Tidepool is moving to a newer login system; if password login stops working, it will be updated in the future.
- If Nightscout is on your LAN from Docker Desktop, use `http://host.docker.internal:PORT` for localhost.

## Eversense follower (EXPERIMENTAL)
Reads a followed Eversense sensor from the Eversense NOW cloud (the same service the follower app uses) and uploads CGM readings.
This is an unofficial, reverse-engineered API; it can break or be blocked at any time, and I have not tested it against a live account.

1. In Eversense, share the sensor wearer's data with a follower account (Eversense NOW), accept the invite.
2. In `.env` set `EVERSENSE_USER`, `EVERSENSE_PASSWORD` (the follower login).
3. `EVERSENSE_REGION` is `US` or `OUS`.

If the sensor is paired with twiist and already arrives via Tidepool, don't enable this too (duplicate readings).
If parsing fails, the log lists the response *field names* it saw (never values); send me those and it's a quick fix.
A wrong password locks the account for 30 minutes after 3 tries, so if Eversense rejects the login the bridge stops trying until you fix `.env` and restart the container.
