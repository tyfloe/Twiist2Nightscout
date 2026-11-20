"""EXPERIMENTAL: Eversense NOW follower -> Nightscout source.

Unofficial and reverse-engineered (hosts/paths/client id taken from the open-source Nocturne connector).
Field names in responses are unverified, so parsing is tolerant and fails loudly (listing keys, never values).
"""
import logging, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone

import bridge

log = logging.getLogger("ttn.eversense")
HOSTS = {  # (auth, data)
    "US": ("https://usiamapi.eversensedms.com", "https://usapialpha.eversensedms.com"),
    "OUS": ("https://ousiamapialpha.eversensedms.com", "https://ousalphaapiservices.eversensedms.com"),
}
CLIENT_ID = "eversenseMMAiOS"
NO_GLUCOSE = 65535
DEVICE = "eversense-follower"
ID_KEYS = ("UserID", "UserId", "userId", "PatientId", "PatientID")
VALUE_KEYS = ("Value", "Glucose", "GlucoseValue", "SensorGlucose", "CurrentGlucose")
TIME_KEYS = ("EventDate", "GlucoseDateTime", "GlucoseTime", "EventDateTime", "DateTime", "Time", "Timestamp", "UtcTime")


def pick(rec, keys):
    lower = {k.lower(): k for k in rec}
    for k in keys:
        if k.lower() in lower:
            return rec[lower[k.lower()]]
    raise KeyError(f"none of {keys} in record; keys present: {sorted(rec)}")


def as_list(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list):
                return v
        return [body]
    return []


def to_utc(s):
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, timezone.utc)
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class Eversense:
    def __init__(self, user, password, client_secret, region="US", patient_id=None):
        self.auth, self.data = HOSTS[region.upper()]
        self.user, self.password, self.secret, self.patient = user, password, client_secret, patient_id
        self.token, self.disabled = None, False

    def login(self):
        if self.disabled:
            raise RuntimeError("Eversense login previously rejected; fix credentials and restart the container")
        form = urllib.parse.urlencode({"grant_type": "password", "username": self.user,
                                       "password": self.password, "client_id": CLIENT_ID,
                                       "client_secret": self.secret}).encode()
        req = bridge.urllib.request.Request(f"{self.auth}/connect/token", data=form, method="POST",
                                            headers={"Content-Type": "application/x-www-form-urlencoded",
                                                     "User-Agent": bridge.USER_AGENT})
        try:
            with bridge.urllib.request.urlopen(req, timeout=30) as r:
                self.token = bridge.json.loads(r.read())["access_token"]
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):  # rejected credentials: stop, or repeated tries lock the account
                self.disabled = True
            raise RuntimeError(f"Eversense login failed (HTTP {e.code}): {e.read()[:200]!r}") from None

    def get(self, path, params=None):
        url = f"{self.data}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in (1, 2):
            if not self.token:
                self.login()
            try:
                return bridge.http("GET", url, {"Authorization": f"Bearer {self.token}"})[1]
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    self.token = None
                    continue
                raise

    def patient_id(self):
        if self.patient:
            return self.patient
        pats = as_list(self.get("/api/care/GetFollowingPatientList"))
        if len(pats) != 1:
            raise RuntimeError(f"Found {len(pats)} followed patients; set EVERSENSE_PATIENT_ID")
        self.patient = pick(pats[0], ID_KEYS)
        return self.patient

    def history(self, start, end):
        pid, out, day = self.patient_id(), [], start
        fmt = "%Y-%m-%dT%H:%M:%S"
        while day < end:
            nxt = min(day + timedelta(days=1), end)  # API is queried in one-day windows
            body = self.get("/api/care/GetFollowingUserSensorGlucose",
                            {"UserID": pid, "startDate": day.strftime(fmt), "endDate": nxt.strftime(fmt)})
            out += as_list(body)
            day = nxt
        return out


def map_readings(records):
    out, seen = [], set()
    for r in records:
        # Observed record fields include EventTypeID, IsValid, Deleted. Skip anything that is clearly
        # not a live glucose reading (type 1 = sensor glucose per the open-source connector).
        if r.get("Deleted") is True or r.get("IsValid") is False:
            continue
        if r.get("EventTypeID") not in (None, 1):
            continue
        v = float(pick(r, VALUE_KEYS))
        if v in (0, NO_GLUCOSE):
            continue
        t = to_utc(pick(r, TIME_KEYS))
        if t in seen:
            continue
        seen.add(t)
        out.append({"time": bridge.iso(t), "value": v, "units": "mmol/L" if v < 40 else "mg/dL"})
    return [dict(e, device=DEVICE) for e in bridge.map_cbg(out)]


def sync_once(ev, ns, state, backfill_hours=24):
    now = datetime.now(timezone.utc)
    last = state.get("eversense") or bridge.iso(now - timedelta(hours=backfill_hours))
    entries = [e for e in map_readings(ev.history(bridge.parse_time(last), now)) if e["dateString"] > last]
    ns.post_entries(entries)
    if entries:
        state["eversense"] = max(e["dateString"] for e in entries)
    log.info("Uploaded %d Eversense readings", len(entries))
    return state
