"""UniSync joint protocol: authority, lifecycle, memory, hash chain and replay. Offline and deterministic."""

import json
import unittest

from lestinxor import ManualClock
from lestinxor.unisync import (
    GENESIS,
    AgentCard,
    Kind,
    Ledger,
    ProtocolError,
    Role,
    envelope_digest,
)


def ledger() -> Ledger:
    return Ledger(clock=ManualClock())


class TestAuthority(unittest.TestCase):
    def test_unknown_sender_refused(self):
        with self.assertRaisesRegex(ProtocolError, "unknown sender"):
            ledger().post("mallory", Kind.HEARTBEAT)

    def test_watcher_cannot_request_tasks(self):
        with self.assertRaisesRegex(ProtocolError, "grok .watcher. may not post task.request"):
            ledger().post("grok", Kind.TASK_REQUEST, "T1", title="x")

    def test_only_owner_decides_approvals(self):
        lg = ledger()
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="deploy", requires_approval=True)
        for agent in ("lestinxor", "sebastian", "jules", "claude", "grok"):
            with self.assertRaisesRegex(ProtocolError, "may not post approval.decision"):
                lg.post(agent, Kind.APPROVAL_DECISION, "T1", approved=True)
        self.assertEqual(lg.status("T1"), "awaiting_approval")

    def test_missing_body_field_and_task_id(self):
        lg = ledger()
        with self.assertRaisesRegex(ProtocolError, "missing body field.*title"):
            lg.post("jonny", Kind.TASK_REQUEST, "T1")
        with self.assertRaisesRegex(ProtocolError, "needs a task_id"):
            lg.post("jonny", Kind.TASK_REQUEST, title="x")
        self.assertEqual(lg.entries, [])

    def test_string_kind_accepted(self):
        env = ledger().post("sebastian", "heartbeat")
        self.assertIs(env.kind, Kind.HEARTBEAT)


class TestLifecycle(unittest.TestCase):
    def test_happy_path(self):
        lg = ledger()
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="add tests")
        self.assertEqual(lg.claimable(), ["T1"])
        lg.post("jules", Kind.TASK_CLAIM, "T1")
        self.assertEqual((lg.status("T1"), lg.claimant("T1")), ("claimed", "jules"))
        self.assertEqual(lg.claimable(), [])
        lg.post("jules", Kind.TASK_RESULT, "T1", summary="PR #4")
        self.assertEqual(lg.status("T1"), "done")

    def test_failure_path(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        lg.post("sebastian", Kind.TASK_FAILED, "T1", reason="checks red")
        self.assertEqual(lg.status("T1"), "failed")

    def test_duplicate_and_missing_tasks(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        with self.assertRaisesRegex(ProtocolError, "already exists"):
            lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="y")
        with self.assertRaisesRegex(ProtocolError, "does not exist"):
            lg.post("jules", Kind.TASK_CLAIM, "T9")

    def test_no_double_claim_and_only_claimant_reports(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        lg.post("jules", Kind.TASK_CLAIM, "T1")
        with self.assertRaisesRegex(ProtocolError, "not claimable"):
            lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        with self.assertRaisesRegex(ProtocolError, "claimed by jules, not sebastian"):
            lg.post("sebastian", Kind.TASK_RESULT, "T1", summary="stolen")

    def test_result_requires_claim(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        with self.assertRaisesRegex(ProtocolError, "open, not claimed"):
            lg.post("jules", Kind.TASK_RESULT, "T1", summary="skipped the claim")

    def test_gated_task_needs_owner_approval_before_claim(self):
        lg = ledger()
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="push to main", requires_approval=True)
        self.assertEqual(lg.pending_approvals(), ["T1"])
        with self.assertRaisesRegex(ProtocolError, "awaiting_approval, not claimable"):
            lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        self.assertEqual(lg.status("T1"), "approved")
        lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        self.assertEqual(lg.status("T1"), "claimed")

    def test_rejected_task_is_closed(self):
        lg = ledger()
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
        lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=False)
        self.assertEqual(lg.status("T1"), "rejected")
        self.assertEqual(lg.claimable(), [])
        with self.assertRaisesRegex(ProtocolError, "no pending approval"):
            lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)

    def test_mid_task_approval_request_pauses_work(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        lg.post("sebastian", Kind.APPROVAL_REQUEST, "T1", action="touch guarded path")
        self.assertEqual(lg.status("T1"), "awaiting_approval")
        with self.assertRaisesRegex(ProtocolError, "not claimed"):
            lg.post("sebastian", Kind.TASK_RESULT, "T1", summary="did it anyway")
        lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        lg.post("sebastian", Kind.TASK_CLAIM, "T1")
        lg.post("sebastian", Kind.TASK_RESULT, "T1", summary="done")
        self.assertEqual(lg.status("T1"), "done")

    def test_approval_request_refused_on_closed_task(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x")
        lg.post("jules", Kind.TASK_CLAIM, "T1")
        lg.post("jules", Kind.TASK_RESULT, "T1", summary="ok")
        with self.assertRaisesRegex(ProtocolError, "approval can only be requested"):
            lg.post("grok", Kind.APPROVAL_REQUEST, "T1", action="merge")

    def test_decision_must_be_bool(self):
        lg = ledger()
        lg.post("jonny", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
        with self.assertRaisesRegex(ProtocolError, "must be a bool"):
            lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved="yes")


class TestMemory(unittest.TestCase):
    def test_scoped_last_write_wins(self):
        lg = ledger()
        lg.post("sebastian", Kind.MEMORY_WRITE, key="model", value="a")
        lg.post("grok", Kind.MEMORY_WRITE, key="model", value="b")
        lg.post("jules", Kind.MEMORY_WRITE, key="model", value="private", scope="jules")
        lg.post("lestinxor", Kind.ESCALATE, reason="watcher alert")
        self.assertEqual(lg.memory(), {"model": "b"})
        self.assertEqual(lg.memory("jules"), {"model": "private"})
        self.assertEqual(lg.memory("nobody"), {})


class TestChain(unittest.TestCase):
    def build(self, clock_start=0.0):
        lg = Ledger(clock=ManualClock(start=clock_start))
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
        lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
        lg.post("jules", Kind.TASK_CLAIM, "T1")
        lg.post("jules", Kind.MEMORY_WRITE, key="k", value=[1, 2])
        lg.post("jules", Kind.TASK_RESULT, "T1", summary="ok")
        return lg

    def test_genesis_and_links(self):
        lg = self.build()
        self.assertEqual(lg.entries[0].prev, GENESIS)
        for a, b in zip(lg.entries, lg.entries[1:]):
            self.assertEqual(b.prev, a.digest)
        self.assertEqual(lg.head, lg.entries[-1].digest)
        self.assertEqual(Ledger().head, GENESIS)
        self.assertTrue(lg.verify())

    def test_deterministic_and_time_independent(self):
        self.assertEqual(self.build().head, self.build(clock_start=1e9).head)

    def test_verify_detects_tampering(self):
        lg = self.build()
        lg.entries[2].body["note"] = "edited"
        self.assertFalse(lg.verify())
        lg = self.build()
        lg.entries.pop(1)
        self.assertFalse(lg.verify())
        lg = self.build()
        e = lg.entries[0]
        lg.entries[0] = type(e)(e.seq, e.sender, e.kind, e.task_id, e.body, "f" * 64, e.digest, e.ts)
        self.assertFalse(lg.verify())

    def test_jsonl_round_trip(self):
        lg = self.build()
        again = Ledger.from_jsonl(lg.to_jsonl() + "\n")
        self.assertEqual(again.head, lg.head)
        self.assertEqual([e.ts for e in again.entries], [e.ts for e in lg.entries])
        self.assertEqual(again.status("T1"), "done")
        self.assertTrue(again.verify())

    def test_replay_rejects_tampered_body(self):
        lines = self.build().to_jsonl().splitlines()
        row = json.loads(lines[3])
        row["body"]["value"] = "forged"
        lines[3] = json.dumps(row)
        with self.assertRaisesRegex(ProtocolError, "hash chain broken at seq 3"):
            Ledger.from_jsonl("\n".join(lines))

    def test_replay_rejects_forged_digest_that_breaks_rules(self):
        # A forger who recomputes the digest still cannot smuggle in a non-owner approval.
        lg = ledger()
        lg.post("lestinxor", Kind.TASK_REQUEST, "T1", title="x", requires_approval=True)
        forged = {
            "v": "unisync/1",
            "seq": 1,
            "sender": "sebastian",
            "kind": "approval.decision",
            "task_id": "T1",
            "body": {"approved": True},
            "prev": lg.head,
            "digest": envelope_digest(1, "sebastian", Kind.APPROVAL_DECISION, "T1", {"approved": True}, lg.head),
            "ts": 0,
        }
        with self.assertRaisesRegex(ProtocolError, "may not post approval.decision"):
            Ledger.from_jsonl(lg.to_jsonl() + json.dumps(forged) + "\n")

    def test_replay_rejects_unknown_version(self):
        row = json.loads(self.build().to_jsonl().splitlines()[0])
        row["v"] = "unisync/0"
        with self.assertRaisesRegex(ProtocolError, "unsupported protocol version"):
            Ledger.from_jsonl(json.dumps(row))

    def test_custom_roster(self):
        lg = Ledger([AgentCard("boss", Role.OWNER), AgentCard("bot", Role.WORKER)], clock=ManualClock())
        lg.post("boss", Kind.TASK_REQUEST, "T1", title="x")
        lg.post("bot", Kind.TASK_CLAIM, "T1")
        with self.assertRaisesRegex(ProtocolError, "unknown sender 'jonny'"):
            lg.post("jonny", Kind.HEARTBEAT)
        self.assertEqual(lg.entries[1].to_dict()["kind"], "task.claim")


class TestO0Registered(unittest.TestCase):
    """O0 joins as an orchestrator: it may dispatch work, but only Jonny approves it."""

    def test_o0_dispatches_and_reports(self):
        lg = ledger()
        lg.post("o0", Kind.HEARTBEAT)
        lg.post("o0", Kind.MEMORY_WRITE, key="flywheel", value={"iteration": 1}, scope="o0")
        lg.post("o0", Kind.TASK_REQUEST, "o0-1", title="audit tasks.py", requires_approval=True)
        self.assertEqual(lg.status("o0-1"), "awaiting_approval")
        self.assertEqual(lg.memory("o0"), {"flywheel": {"iteration": 1}})

    def test_o0_cannot_approve_its_own_dispatch(self):
        lg = ledger()
        lg.post("o0", Kind.TASK_REQUEST, "o0-2", title="x", requires_approval=True)
        with self.assertRaises(ProtocolError):
            lg.post("o0", Kind.APPROVAL_DECISION, "o0-2", approved=True)
        lg.post("jonny", Kind.APPROVAL_DECISION, "o0-2", approved=True)
        self.assertEqual(lg.status("o0-2"), "approved")

    def test_ledgers_with_o0_entries_replay(self):
        lg = ledger()
        lg.post("o0", Kind.HEARTBEAT)
        self.assertTrue(Ledger.from_jsonl(lg.to_jsonl()).verify())

    def test_schema_seeds_o0(self):
        from pathlib import Path

        sql = (Path(__file__).resolve().parents[1] / "unisync" / "schema.sql").read_text()
        self.assertIn("('o0',              'orchestrator', '{jonnysteedman683-wq/O0}')", sql)


if __name__ == "__main__":
    unittest.main()
