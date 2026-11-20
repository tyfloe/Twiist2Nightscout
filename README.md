# twiist -> Nightscout bridge

Polls Tidepool (where twiist data lands once your My twiist Portal is linked) and uploads to Nightscout:
- CGM readings -> `entries` (sgv, mg/dL, with a computed trend arrow)
- Boluses (manual and automated) -> `treatments`
- Carb entries -> `treatments` (Carb Correction)
- Basal changes (automated, temp, scheduled, suspend) -> `treatments` (Temp Basal)

## Setup
1. Link your My twiist Portal account to Tidepool (app.tidepool.org -> Connect a Device Account -> twiist).
2. `cp .env.example .env` and fill it in.
3. `docker compose up -d --build` then `docker compose logs -f`.

State (last uploaded timestamps) lives in the `ttn-data` volume, so restarts don't re-upload.
Run tests: `python -m unittest discover -s tests`.

## Notes
- Data is only as fresh as twiist's sync to Tidepool, so expect a delay versus the twiist app.
- Tidepool is moving to a newer login system; if password login stops working, the auth in `Tidepool.login` needs updating.
- If Nightscout is on your LAN from Docker Desktop, use `http://host.docker.internal:PORT` for localhost.

## Eversense follower (EXPERIMENTAL)
Reads a followed Eversense sensor from the Eversense NOW cloud (the same service the follower app uses) and uploads CGM readings.
This is an unofficial, reverse-engineered API; it can break or be blocked at any time, and I have not tested it against a live account.

1. In Eversense, share the sensor wearer's data with a follower account (Eversense NOW), accept the invite.
2. In `.env` set `EVERSENSE_USER`, `EVERSENSE_PASSWORD` (the follower login) and `EVERSENSE_CLIENT_SECRET`.
   The client secret is the Eversense app's own embedded value; this project doesn't ship it. It is defined in the open-source
   Nocturne connector (`EversenseConstants.cs` in github.com/nightscout/nocturne).
3. `EVERSENSE_REGION` is `US` or `OUS`.

If the sensor is paired with twiist and already arrives via Tidepool, don't enable this too (duplicate readings).
If parsing fails, the log lists the response *field names* it saw (never values); send me those and it's a quick fix.
A wrong password locks the account for 30 minutes after 3 tries, so if Eversense rejects the login the bridge stops trying until you fix `.env` and restart the container.
