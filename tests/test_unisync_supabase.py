"""SupabaseLedger against an in-memory fake of PostgREST (urlopen is patched; no network)."""

import io
import json
import os
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from lestinxor import ManualClock
from lestinxor import unisync_supabase as us
from lestinxor.unisync import Kind, Ledger, ProtocolError
from lestinxor.unisync_supabase import SupabaseLedger

URL = "https://ref.supabase.co"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeRest:
    """Holds unisync_events rows and enforces the schema's unique seq/prev constraint."""

    def __init__(self):
        self.rows = []
        self.requests = []
        self.before_insert = []  # callables run before the next POST is applied (simulate a racing agent)
        self.fail_with = None
        self._busy = False

    def __call__(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url, dict(req.header_items())))
        if self.fail_with is not None:
            raise urllib.error.HTTPError(req.full_url, self.fail_with, "boom", {}, None)
        if req.get_method() == "GET":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            start = int(q["seq"][0].removeprefix("gte."))
            limit = int(q["limit"][0])
            rows = sorted((r for r in self.rows if r["seq"] >= start), key=lambda r: r["seq"])[:limit]
            return _Resp(json.dumps(rows).encode())
        if self.before_insert and not self._busy:  # one racing write per insert, never nested
            fn = self.before_insert.pop(0)
            self._busy = True
            try:
                fn()
            finally:
                self._busy = False
        row = json.loads(req.data)
        if any(r["seq"] == row["seq"] or r["prev"] == row["prev"] for r in self.rows):
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, None)
        self.rows.append(row)
        return _Resp(b"")


def make(fake, **kw):
    return SupabaseLedger(URL, "service-key", clock=ManualClock(), **kw)


class TestSupabaseLedger(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRest()
        patcher = mock.patch("urllib.request.urlopen", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_config_from_env_and_missing(self):
        with mock.patch.dict(os.environ, {"UNISYNC_SUPABASE_URL": URL + "/", "UNISYNC_SUPABASE_KEY": "k"}):
            lg = SupabaseLedger()
        self.assertEqual(lg.endpoint, URL + "/rest/v1/unisync_events")
        self.assertEqual(lg.headers["Authorization"], "Bearer k")
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "UNISYNC_SUPABASE_URL"):
            SupabaseLedger()

    def test_post_writes_rows_and_two_agents_share_state(self):
        a, b = make(self.fake), make(self.fake)
        a.post("lestinxor", Kind.TASK_REQUEST, "T1", title="x")
        b.post("jules", Kind.TASK_CLAIM, "T1")  # b syncs a's request first
        a.post("jonny", Kind.MEMORY_WRITE, key="model", value="m")
        b.post("jules", Kind.TASK_RESULT, "T1", summary="ok")
        a.sync()
        self.assertEqual([r["seq"] for r in self.fake.rows], [0, 1, 2, 3])
        self.assertEqual((a.status("T1"), b.status("T1")), ("done", "done"))
        self.assertEqual(a.memory(), {"model": "m"})
        self.assertEqual(a.ledger.head, b.ledger.head)
        self.assertTrue(a.ledger.verify())
        method, _url, headers = self.fake.requests[-1]
        self.assertEqual(method, "GET")  # the final a.sync()
        self.assertEqual(headers["Apikey"], "service-key")

    def test_conflict_resyncs_and_retries(self):
        a, b = make(self.fake), make(self.fake)
        a.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        b.sync()
        self.fake.before_insert.append(lambda: a.post("jonny", Kind.TASK_REQUEST, "T2", title="y"))
        env = b.post("lestinxor", Kind.TASK_REQUEST, "T3", title="z")
        self.assertEqual(env.seq, 2)
        self.assertEqual([r["task_id"] for r in self.fake.rows], ["T1", "T2", "T3"])
        self.assertEqual(b.ledger.entries[-1].seq, 2)
        self.assertEqual(b.claimable(), ["T1", "T2", "T3"])
        self.assertTrue(Ledger.from_records(self.fake.rows).verify())

    def test_conflict_that_invalidates_the_post_is_refused(self):
        a, b = make(self.fake), make(self.fake)
        a.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        b.sync()
        self.fake.before_insert.append(lambda: a.post("sebastian", Kind.TASK_CLAIM, "T1"))
        with self.assertRaisesRegex(ProtocolError, "claimed, not claimable"):
            b.post("jules", Kind.TASK_CLAIM, "T1")
        self.assertEqual(b.ledger.claimant("T1"), "sebastian")

    def test_gives_up_after_retries(self):
        lg = make(self.fake, retries=2)
        racer = make(self.fake)
        for i in range(3):
            self.fake.before_insert.append(lambda i=i: racer.post("jonny", Kind.HEARTBEAT, n=i))
        with self.assertRaisesRegex(ProtocolError, "gave up after 2"):
            lg.post("jonny", Kind.HEARTBEAT)

    def test_other_http_errors_propagate(self):
        lg = make(self.fake)
        self.fake.fail_with = 401
        with self.assertRaises(urllib.error.HTTPError):
            lg.sync()

    def test_post_error_other_than_conflict_propagates(self):
        lg = make(self.fake)
        fake = self.fake

        def post_fails(req, timeout=None):
            if req.get_method() == "POST":
                raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)
            return fake(req, timeout)

        with mock.patch("urllib.request.urlopen", post_fails), self.assertRaises(urllib.error.HTTPError):
            lg.post("jonny", Kind.HEARTBEAT)
        self.assertEqual(lg.ledger.entries, [])

    def test_tampered_row_stops_sync(self):
        make(self.fake).post("lestinxor", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
        forged = dict(self.fake.rows[0])
        forged.update(seq=1, prev=forged["digest"], sender="sebastian", kind="approval.decision")
        forged["body"] = {"approved": True}
        self.fake.rows.append(forged)
        with self.assertRaisesRegex(ProtocolError, "may not post approval.decision"):
            make(self.fake).sync()

    def test_sync_pages(self):
        w = make(self.fake)
        for i in range(5):
            w.post("jonny", Kind.HEARTBEAT, n=i)
        with mock.patch.object(us, "PAGE", 2):
            r = make(self.fake)
            self.assertEqual(r.sync(), 5)
            self.assertEqual(r.sync(), 0)
        self.assertEqual(r.ledger.head, w.ledger.head)
        self.assertEqual(r.pending_approvals(), [])


if __name__ == "__main__":
    unittest.main()
