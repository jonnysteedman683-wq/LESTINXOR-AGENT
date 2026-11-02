"""Owner signatures: the scheme itself, and how the ledger enforces it. Offline and deterministic."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lestinxor import ManualClock
from lestinxor.owner_sig import OwnerKey, OwnerSigError, OwnerSigner, sig_index, verify
from lestinxor.unisync import Kind, Ledger, ProtocolError

SEED = bytes(range(32))
KEY = OwnerKey(SEED, height=3)  # 8 leaves keeps the tests fast
ROOT = KEY.root


def flip(sig: str, at: int) -> str:
    raw = bytearray(base64.b64decode(sig))
    raw[at] ^= 1
    return base64.b64encode(bytes(raw)).decode()


class TestScheme(unittest.TestCase):
    def test_root_is_deterministic_and_seed_specific(self):
        self.assertEqual(OwnerKey(SEED, height=3).root, ROOT)
        self.assertNotEqual(OwnerKey(bytes(32), height=3).root, ROOT)
        self.assertEqual(len(ROOT), 64)
        self.assertEqual(KEY.capacity, 8)

    def test_sign_verify_every_leaf(self):
        for i in range(KEY.capacity):
            self.assertEqual(verify(b"msg %d" % i, KEY.sign(b"msg %d" % i, i), ROOT), i)

    def test_tampering_fails(self):
        sig = KEY.sign(b"approve T1", 2)
        with self.assertRaisesRegex(OwnerSigError, "does not verify"):
            verify(b"approve T2", sig, ROOT)
        for at in (10, 5 + 512 * 32 + 3):  # a revealed secret, then the Merkle path
            with self.assertRaisesRegex(OwnerSigError, "does not verify"):
                verify(b"approve T1", flip(sig, at), ROOT)
        with self.assertRaisesRegex(OwnerSigError, "does not verify"):
            verify(b"approve T1", sig, OwnerKey(bytes(32), height=3).root)
        forged = OwnerKey(b"x" * 32, height=3).sign(b"approve T1", 2)  # attacker's own key
        with self.assertRaisesRegex(OwnerSigError, "does not verify"):
            verify(b"approve T1", forged, ROOT)

    def test_malformed_input(self):
        sig = KEY.sign(b"m", 0)
        for bad, msg in (
            ("not base64!!", "well-formed"),
            (base64.b64encode(b"abc").decode(), "wrong size"),
            (base64.b64encode(base64.b64decode(sig)[:-1]).decode(), "wrong shape"),
        ):
            with self.assertRaisesRegex(OwnerSigError, msg):
                verify(b"m", bad, ROOT)
        with self.assertRaisesRegex(OwnerSigError, "well-formed"):
            verify(b"m", sig, "zz")
        raw = bytearray(base64.b64decode(sig))
        raw[0:4] = (99).to_bytes(4, "big")  # index beyond 2**height
        with self.assertRaisesRegex(OwnerSigError, "wrong shape"):
            verify(b"m", base64.b64encode(bytes(raw)).decode(), ROOT)
        with self.assertRaisesRegex(OwnerSigError, "base64"):
            sig_index("***")
        with self.assertRaisesRegex(OwnerSigError, "too short"):
            sig_index(base64.b64encode(b"ab").decode())

    def test_key_limits(self):
        with self.assertRaisesRegex(OwnerSigError, "exhausted"):
            KEY.sign(b"m", 8)
        with self.assertRaisesRegex(ValueError, "32 bytes"):
            OwnerKey(b"short")
        with self.assertRaisesRegex(ValueError, "height"):
            OwnerKey(SEED, height=0)
        self.assertEqual(len(OwnerKey.generate(height=1).seed), 32)


class TestSigner(unittest.TestCase):
    def test_indices_advance_and_survive_failed_posts(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "sub" / "owner.state"
            signer = OwnerSigner(KEY, state)
            self.assertEqual(sig_index(signer.sign(b"a", -1)), 0)
            self.assertEqual(sig_index(signer.sign(b"b", -1)), 1)  # 0 was sent, maybe never stored: burned
            self.assertEqual(sig_index(signer.sign(b"c", 5)), 6)  # the ledger is further ahead
            self.assertEqual(json.loads(state.read_text())["max_index"], 6)
            state.write_text("garbage")
            self.assertEqual(sig_index(OwnerSigner(KEY, state).sign(b"d", 6)), 7)
        self.assertEqual(sig_index(OwnerSigner(KEY).sign(b"e", 2)), 3)


def signed_ledger(**kw):
    return Ledger(clock=ManualClock(), owner_root=ROOT, signer=OwnerSigner(KEY).sign, **kw)


class TestLedgerEnforcement(unittest.TestCase):
    def test_owner_posts_are_signed_and_replay(self):
        lg = signed_ledger()
        lg.post("o0", Kind.TASK_REQUEST, "T1", title="audit", requires_approval=True)
        lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        lg.post("jonny", Kind.MEMORY_WRITE, key="halt", value={"halted": True}, scope="linx/control")
        self.assertIn("sig", lg.entries[1].body)
        self.assertNotIn("sig", lg.entries[0].body)  # only the owner signs
        self.assertEqual(lg.max_owner_index(), 1)
        replay = Ledger.from_jsonl(lg.to_jsonl(), owner_root=ROOT)
        self.assertTrue(replay.verify())
        self.assertEqual(replay.status("T1"), "approved")
        self.assertEqual(replay.max_owner_index(), 1)

    def test_unsigned_or_forged_owner_entries_are_refused(self):
        lg = Ledger(clock=ManualClock(), owner_root=ROOT)  # someone with write access, but no seed
        lg.post("o0", Kind.TASK_REQUEST, "T1", title="audit", requires_approval=True)
        with self.assertRaisesRegex(ProtocolError, "must be signed"):
            lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        fake = OwnerKey(b"y" * 32, height=3).sign(b"whatever", 0)
        with self.assertRaisesRegex(ProtocolError, "owner signature rejected"):
            lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True, sig=fake)
        self.assertEqual(lg.status("T1"), "awaiting_approval")

    def test_replay_refuses_a_forged_row(self):
        unsigned = Ledger(clock=ManualClock())  # rows written by someone without the seed
        unsigned.post("o0", Kind.TASK_REQUEST, "T1", title="audit", requires_approval=True)
        unsigned.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        with self.assertRaisesRegex(ProtocolError, "must be signed"):
            Ledger.from_jsonl(unsigned.to_jsonl(), owner_root=ROOT)

    def test_signature_is_bound_to_its_place_in_the_chain(self):
        lg = signed_ledger()
        lg.post("jonny", Kind.MEMORY_WRITE, key="goal", value="ship")
        rows = [json.loads(line) for line in lg.to_jsonl().splitlines()]
        copied = Ledger(clock=ManualClock(), owner_root=ROOT)
        copied.post("o0", Kind.HEARTBEAT)  # the head moved: the old signature no longer fits
        with self.assertRaisesRegex(ProtocolError, "owner signature rejected"):
            copied.post("jonny", Kind.MEMORY_WRITE, key="goal", value="ship", sig=rows[0]["body"]["sig"])

    def test_key_reuse_is_refused(self):
        lg = Ledger(clock=ManualClock(), owner_root=ROOT)
        first = lg._unsigned_digest("jonny", Kind.MEMORY_WRITE, "", {"key": "a", "value": 1})
        lg.post("jonny", Kind.MEMORY_WRITE, key="a", value=1, sig=KEY.sign(bytes.fromhex(first), 4))
        second = lg._unsigned_digest("jonny", Kind.MEMORY_WRITE, "", {"key": "b", "value": 2})
        with self.assertRaisesRegex(ProtocolError, "index 4 reused"):
            lg.post("jonny", Kind.MEMORY_WRITE, key="b", value=2, sig=KEY.sign(bytes.fromhex(second), 4))

    def test_owner_from_keeps_old_unsigned_history_valid(self):
        old = Ledger(clock=ManualClock())
        old.post("jonny", Kind.MEMORY_WRITE, key="legacy", value=1)
        lg = Ledger.from_jsonl(old.to_jsonl(), owner_root=ROOT, owner_from=1)
        lg.signer = OwnerSigner(KEY).sign
        lg.post("jonny", Kind.MEMORY_WRITE, key="new", value=2)
        self.assertIn("sig", lg.entries[1].body)
        Ledger.from_jsonl(lg.to_jsonl(), owner_root=ROOT, owner_from=1)
        with self.assertRaisesRegex(ProtocolError, "must be signed"):
            Ledger.from_jsonl(lg.to_jsonl(), owner_root=ROOT, owner_from=0)

    def test_without_owner_root_nothing_changes(self):
        lg = Ledger(clock=ManualClock())
        lg.post("jonny", Kind.MEMORY_WRITE, key="k", value=1)
        self.assertNotIn("sig", lg.entries[0].body)
        self.assertEqual(lg.max_owner_index(), -1)

    def test_commit_refuses_an_envelope_from_another_head(self):
        lg = Ledger(clock=ManualClock())
        env = lg.prepare("o0", Kind.HEARTBEAT)
        lg.post("o0", Kind.HEARTBEAT)
        with self.assertRaisesRegex(ProtocolError, "another head"):
            lg.commit(env)


class TestSupabaseSigned(unittest.TestCase):
    def test_signed_post_round_trips_through_supabase(self):
        from lestinxor.unisync_supabase import SupabaseLedger
        from tests.test_unisync_supabase import URL, FakeRest

        fake = FakeRest()
        with mock.patch("urllib.request.urlopen", fake):
            remote = SupabaseLedger(URL, "k", clock=ManualClock(), owner_root=ROOT, signer=OwnerSigner(KEY).sign)
            remote.post("o0", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
            remote.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
            reader = SupabaseLedger(URL, "k", owner_root=ROOT)
            reader.sync()
        self.assertEqual(reader.status("T1"), "approved")
        self.assertEqual(reader.ledger.max_owner_index(), 0)


if __name__ == "__main__":
    unittest.main()
