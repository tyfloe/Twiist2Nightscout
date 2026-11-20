"""twiist (via Tidepool) -> Nightscout bridge. Standard library only."""
import base64, hashlib, json, logging, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger("ttn")
MGDL_PER_MMOL = 18.01559
DEVICE = "twiist-tidepool-bridge"


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


USER_AGENT = os.getenv("HTTP_USER_AGENT", "twiist-tidepool-bridge/1.0 (personal Nightscout uploader)")


def http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.headers, (json.loads(raw) if raw else None)


class Tidepool:
    def __init__(self, base, user, password, target_user_id=None):
        self.base, self.user, self.password = base.rstrip("/"), user, password
        self.target, self.token = target_user_id, None

    def login(self):
        cred = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        headers, body = http("POST", f"{self.base}/auth/login", {"Authorization": f"Basic {cred}"})
        self.token = headers["x-tidepool-session-token"]
        self.target = self.target or body["userid"]
        log.info("Logged in to Tidepool; reading data for user %s", self.target)

    def fetch(self, types, start, end=None):
        q = {"type": ",".join(types), "startDate": iso(start)}
        if end:
            q["endDate"] = iso(end)
        url = f"{self.base}/data/{self.target}?{urllib.parse.urlencode(q)}"
        for attempt in (1, 2):
            if not self.token:
                self.login()
            try:
                return http("GET", url, {"X-Tidepool-Session-Token": self.token})[1] or []
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    self.token = None  # session expired: re-login once
                    continue
                raise


class Nightscout:
    def __init__(self, url, api_secret=None, token=None):
        self.url, self.token = url.rstrip("/"), token
        self.headers = {}
        if api_secret:
            self.headers["api-secret"] = hashlib.sha1(api_secret.encode()).hexdigest()

    def _post(self, path, payload):
        if not payload:
            return
        url = f"{self.url}{path}"
        if self.token:
            url += "?" + urllib.parse.urlencode({"token": self.token})
        http("POST", url, self.headers, payload)

    def post_entries(self, entries):
        self._post("/api/v1/entries.json", entries)

    def post_treatments(self, treatments):
        self._post("/api/v1/treatments.json", treatments)


# ---- Tidepool -> Nightscout mapping -------------------------------------------------
def direction(prev_mgdl, cur_mgdl, minutes):
    if prev_mgdl is None or minutes <= 0:
        return None
    r = (cur_mgdl - prev_mgdl) / minutes  # mg/dL per minute
    for limit, name in ((3, "DoubleUp"), (2, "SingleUp"), (1, "FortyFiveUp"), (-1, "Flat"),
                        (-2, "FortyFiveDown"), (-3, "SingleDown")):
        if r > limit if limit > 0 else r >= limit:
            return name
    return "DoubleDown"


def map_cbg(records):
    out, prev, prev_t = [], None, None
    for r in sorted(records, key=lambda r: r["time"]):
        v = float(r["value"])
        mgdl = round(v * MGDL_PER_MMOL) if r.get("units", "mmol/L") == "mmol/L" else round(v)
        t = parse_time(r["time"])
        e = {"type": "sgv", "sgv": mgdl, "date": int(t.timestamp() * 1000),
             "dateString": iso(t), "device": DEVICE}
        d = direction(prev, mgdl, (t - prev_t).total_seconds() / 60 if prev_t else 0)
        if d:
            e["direction"] = d
        out.append(e)
        prev, prev_t = mgdl, t
    return out


def map_bolus(records):
    out = []
    for r in records:
        units = r.get("normal", r.get("extended", 0))
        if not units:
            continue
        auto = r.get("subType") == "automated"
        out.append({"eventType": "Correction Bolus" if auto else "Bolus", "insulin": units,
                    "created_at": iso(parse_time(r["time"])), "enteredBy": DEVICE,
                    "notes": "automated bolus" if auto else ""})
    return out


def map_food(records):
    out = []
    for r in records:
        carbs = ((r.get("nutrition") or {}).get("carbohydrate") or {}).get("net")
        if carbs:
            out.append({"eventType": "Carb Correction", "carbs": carbs,
                        "created_at": iso(parse_time(r["time"])), "enteredBy": DEVICE})
    return out


def map_basal(records):
    out = []
    for r in records:
        kind = r.get("deliveryType")
        if kind not in ("automated", "temp", "scheduled", "suspend"):
            continue
        rate = 0 if kind == "suspend" else r.get("rate")
        if rate is None:
            continue
        t = {"eventType": "Temp Basal", "absolute": rate, "rate": rate,
             "created_at": iso(parse_time(r["time"])), "enteredBy": DEVICE,
             "notes": f"{kind} basal"}
        if r.get("duration"):
            t["duration"] = round(r["duration"] / 60000, 2)  # ms -> minutes
        out.append(t)
    return out


# ---- sync loop ---------------------------------------------------------------------
def load_state(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(state, open(path, "w"))


def sync_once(tp, ns, state, backfill_hours=24):
    now = datetime.now(timezone.utc)
    default = iso(now - timedelta(hours=backfill_hours))
    last = {t: state.get(t, default) for t in ("cbg", "bolus", "food", "basal")}
    start = min(parse_time(v) for v in last.values())
    records = tp.fetch(list(last), start)
    by_type = {t: [r for r in records if r.get("type") == t and r["time"] > last[t]] for t in last}
    ns.post_entries(map_cbg(by_type["cbg"]))
    ns.post_treatments(map_bolus(by_type["bolus"]) + map_food(by_type["food"]) + map_basal(by_type["basal"]))
    for t, rs in by_type.items():
        if rs:
            state[t] = max(r["time"] for r in rs)
    log.info("Uploaded %s", {t: len(r) for t, r in by_type.items()})
    return state


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ
    if not env.get("NIGHTSCOUT_URL"):
        sys.exit("Missing required env var NIGHTSCOUT_URL")
    if not (env.get("NIGHTSCOUT_API_SECRET") or env.get("NIGHTSCOUT_TOKEN")):
        sys.exit("Set NIGHTSCOUT_API_SECRET or NIGHTSCOUT_TOKEN")
    ns = Nightscout(env["NIGHTSCOUT_URL"], env.get("NIGHTSCOUT_API_SECRET"), env.get("NIGHTSCOUT_TOKEN"))
    backfill = int(env.get("BACKFILL_HOURS", "24"))
    sources = []  # (name, sync_fn(state) -> state)
    if env.get("TIDEPOOL_USER"):
        if not env.get("TIDEPOOL_PASSWORD"):
            sys.exit("TIDEPOOL_USER set but TIDEPOOL_PASSWORD missing")
        tp = Tidepool(env.get("TIDEPOOL_API", "https://api.tidepool.org"), env["TIDEPOOL_USER"],
                      env["TIDEPOOL_PASSWORD"], env.get("TIDEPOOL_USER_ID"))
        sources.append(("tidepool", lambda st: sync_once(tp, ns, st, backfill)))
    if env.get("EVERSENSE_USER"):
        import eversense
        if not (env.get("EVERSENSE_PASSWORD") and env.get("EVERSENSE_CLIENT_SECRET")):
            sys.exit("EVERSENSE_USER set but EVERSENSE_PASSWORD / EVERSENSE_CLIENT_SECRET missing")
        ev = eversense.Eversense(env["EVERSENSE_USER"], env["EVERSENSE_PASSWORD"],
                                 env["EVERSENSE_CLIENT_SECRET"], env.get("EVERSENSE_REGION", "US"),
                                 env.get("EVERSENSE_PATIENT_ID"))
        sources.append(("eversense", lambda st: eversense.sync_once(ev, ns, st, backfill)))
    if not sources:
        sys.exit("Configure TIDEPOOL_USER and/or EVERSENSE_USER")
    path = env.get("STATE_FILE", "/data/state.json")
    interval = int(env.get("POLL_SECONDS", "300"))
    state = load_state(path)
    while True:
        for name, fn in sources:  # one source failing must not block the other
            try:
                state = fn(state)
            except Exception:
                log.exception("%s sync failed; will retry", name)
        save_state(path, state)
        time.sleep(interval)


if __name__ == "__main__":
    main()
