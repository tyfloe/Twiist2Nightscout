import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bridge


class T(unittest.TestCase):
    def test_cbg(self):
        out = bridge.map_cbg([{"type": "cbg", "value": 5.5, "units": "mmol/L", "time": "2026-11-19T12:00:00Z"},
                              {"type": "cbg", "value": 6.0, "units": "mmol/L", "time": "2026-11-19T12:05:00Z"}])
        self.assertEqual(out[0]["sgv"], 99)
        self.assertEqual(out[1]["sgv"], 108)
        self.assertEqual(out[1]["direction"], "FortyFiveUp")
        self.assertNotIn("direction", out[0])

    def test_treatments(self):
        b = bridge.map_bolus([{"subType": "automated", "normal": 0.5, "time": "2026-11-19T12:00:00Z"},
                              {"subType": "normal", "normal": 0, "time": "2026-11-19T12:01:00Z"}])
        self.assertEqual(len(b), 1)
        self.assertEqual(b[0]["eventType"], "Correction Bolus")
        f = bridge.map_food([{"nutrition": {"carbohydrate": {"net": 30}}, "time": "2026-11-19T12:00:00Z"}])
        self.assertEqual(f[0]["carbs"], 30)

    def test_basal(self):
        b = bridge.map_basal([{"deliveryType": "automated", "rate": 0.85, "duration": 300000, "time": "2026-11-19T12:00:00Z"},
                              {"deliveryType": "suspend", "duration": 600000, "time": "2026-11-19T12:05:00Z"}])
        self.assertEqual((b[0]["absolute"], b[0]["duration"]), (0.85, 5.0))
        self.assertEqual(b[1]["absolute"], 0)

    def test_sync_dedup(self):
        class TP:
            def fetch(self, types, start):
                return [{"type": "cbg", "value": 5.5, "units": "mmol/L", "time": "2026-11-19T12:00:00Z"},
                        {"type": "food", "nutrition": {"carbohydrate": {"net": 20}}, "time": "2026-11-19T12:00:00Z"}]
        class NS:
            def __init__(s): s.e, s.t = [], []
            def post_entries(s, x): s.e += x
            def post_treatments(s, x): s.t += x
        ns, st = NS(), {}
        st = bridge.sync_once(TP(), ns, st, backfill_hours=100000)
        bridge.sync_once(TP(), ns, st, backfill_hours=100000)  # same data again: nothing new
        self.assertEqual((len(ns.e), len(ns.t)), (1, 1))


if __name__ == "__main__":
    unittest.main()


class EversenseTests(unittest.TestCase):
    def test_parse_and_sync(self):
        import eversense
        recs = [{"value": 120, "glucoseDateTime": "2026-11-19T12:00:00"},
                {"Value": 65535, "GlucoseDateTime": "2026-11-19T12:05:00"},
                {"Value": 130, "GlucoseDateTime": "2026-11-19T12:10:00Z"}]
        out = eversense.map_readings(recs)
        self.assertEqual([e["sgv"] for e in out], [120, 130])
        self.assertEqual(out[0]["device"], "eversense-follower")

        class EV:
            def history(s, a, b): return recs
        class NS:
            def __init__(s): s.e = []
            def post_entries(s, x): s.e += x
        ns, st = NS(), {}
        st = eversense.sync_once(EV(), ns, st, backfill_hours=100000)
        eversense.sync_once(EV(), ns, st, backfill_hours=100000)
        self.assertEqual(len(ns.e), 2)

    def test_unknown_fields_fail_loudly(self):
        import eversense
        with self.assertRaises(KeyError) as c:
            eversense.map_readings([{"Foo": 1}])
        self.assertIn("Foo", str(c.exception))


class EversenseLoginGuard(unittest.TestCase):
    def test_rejected_login_stops_retrying(self):
        import eversense, urllib.error, io
        ev = eversense.Eversense("u", "bad", "secret")
        calls = []
        def boom(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError("x", 400, "bad", {}, io.BytesIO(b'{"error":"invalid_grant"}'))
        orig = eversense.bridge.urllib.request.urlopen
        eversense.bridge.urllib.request.urlopen = boom
        try:
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    ev.login()
        finally:
            eversense.bridge.urllib.request.urlopen = orig
        self.assertEqual(len(calls), 1)


class EversenseRealShape(unittest.TestCase):
    def test_observed_record_shape(self):
        import eversense
        base = {"Comments": None, "Deleted": False, "EventTypeID": 1, "IsValid": True, "UserId": 1, "Value": 110}
        recs = [dict(base, EventDate="2026-11-20T17:00:00"),
                dict(base, EventDate="2026-11-20T17:05:00", Deleted=True),
                dict(base, EventDate="2026-11-20T17:10:00", IsValid=False),
                dict(base, EventDate="2026-11-20T17:15:00", EventTypeID=2),
                dict(base, EventDate="2026-11-20T17:20:00", Value=65535)]
        out = eversense.map_readings(recs)
        self.assertEqual([e["dateString"] for e in out], ["2026-11-20T17:00:00.000Z"])
