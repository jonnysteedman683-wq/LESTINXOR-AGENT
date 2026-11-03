"""Fuzz the ledger replay (L10): damaged history is refused with ProtocolError, never another crash, and
whatever is accepted is a true prefix of the real history. Seeded and deterministic; set
LESTINXOR_FUZZ_ITERS for a deeper run."""

import json
import os
import random
import unittest
from typing import Any

from lestinxor import ManualClock
from lestinxor.owner_sig import OwnerKey, OwnerSigner
from lestinxor.unisync import Envelope, Kind, Ledger, ProtocolError

ITERS = int(os.environ.get("LESTINXOR_FUZZ_ITERS", "300"))
JUNK: list[Any] = [None, 0, -1, 2**70, 1.5, float("inf"), True, "", "x", [], [1], {}, {"a": 1}, "‮", "\x00"]
KEY = OwnerKey(b"f" * 32, height=3)


def real_ledger(signed: bool = False) -> Ledger:
    kw: dict[str, Any] = {"owner_root": KEY.root, "signer": OwnerSigner(KEY).sign} if signed else {}
    lg = Ledger(clock=ManualClock(), **kw)
    lg.post("jonny", Kind.TASK_REQUEST, "T1", title="audit")
    lg.post("o0", Kind.APPROVAL_REQUEST, "T1", action="merge")
    lg.post("jonny", Kind.APPROVAL_DECISION, "T1", approved=True)
    lg.post("o0", Kind.TASK_CLAIM, "T1")
    for i in range(8):
        lg.post("sebastian", Kind.MEMORY_WRITE, key=f"k{i % 3}", value={"n": i}, scope="shared")
    lg.post("o0", Kind.TASK_RESULT, "T1", summary="done")
    return lg


def mutate(r: random.Random, lines: list[str]) -> list[str]:
    out = list(lines)
    op = r.randrange(9)
    i = r.randrange(len(out))
    if op == 0:
        del out[i]
    elif op == 1:
        j = r.randrange(len(out))
        out[i], out[j] = out[j], out[i]
    elif op == 2:
        out.insert(r.randrange(len(out) + 1), out[i])
    elif op == 3:
        out[i] = out[i][: r.randrange(len(out[i]) + 1)]
    elif op == 4:
        k = r.randrange(len(out[i]))
        out[i] = out[i][:k] + chr(r.randrange(32, 0x2FF)) + out[i][k + 1 :]
    else:
        try:
            rec = json.loads(out[i])
        except ValueError:
            return out
        if not isinstance(rec, dict) or not rec:
            return out
        key = r.choice(sorted(rec))
        if op == 5:
            rec[key] = r.choice(JUNK)
        elif op == 6:
            del rec[key]
        elif op == 7:
            rec["body"] = r.choice(JUNK) if r.random() < 0.5 else {**rec.get("body", {}), "z": r.choice(JUNK)}
        else:
            out[i] = json.dumps(r.choice(JUNK))
            return out
        out[i] = json.dumps(rec, default=str)
    return out


def same(a: Envelope, b: Envelope) -> bool:
    return (a.seq, a.sender, a.kind, a.task_id, a.body, a.prev, a.digest) == (
        b.seq,
        b.sender,
        b.kind,
        b.task_id,
        b.body,
        b.prev,
        b.digest,
    )


class ReplayFuzz(unittest.TestCase):
    def run_fuzz(self, signed: bool) -> None:
        lg = real_ledger(signed)
        lines = lg.to_jsonl().splitlines()
        kw: dict[str, Any] = {"owner_root": KEY.root} if signed else {}
        for seed in range(ITERS):
            r = random.Random(seed)
            bad = lines
            for _ in range(r.randint(1, 3)):
                bad = mutate(r, bad) or list(lines)
            text = "\n".join(bad) + "\n"
            try:
                got = Ledger.from_jsonl(text, **kw)
            except ProtocolError:
                continue
            except Exception as exc:  # pragma: no cover - a failure here is the finding
                self.fail(f"seed {seed}: {type(exc).__name__}: {exc}\n{text}")
            self.assertLessEqual(len(got.entries), len(lg.entries), f"seed {seed}")
            for mine, orig in zip(got.entries, lg.entries, strict=False):
                self.assertTrue(same(mine, orig), f"seed {seed}: forged #{mine.seq} accepted\n{text}")

    def test_unsigned_ledger(self) -> None:
        self.run_fuzz(signed=False)

    def test_owner_signed_ledger(self) -> None:
        self.run_fuzz(signed=True)

    def test_malformed_rows_are_protocol_errors(self) -> None:
        good = real_ledger().entries[0].to_dict()
        cases: list[Any] = [None, [], "x", {**good, "seq": True}, {**good, "body": []}, {**good, "kind": "nope"}]
        cases += [{k: v for k, v in good.items() if k != name} for name in good]
        for raw in cases:
            with self.assertRaises(ProtocolError, msg=repr(raw)):
                Ledger().append_record(raw)
        with self.assertRaisesRegex(ProtocolError, "line 2 is not JSON"):
            Ledger.from_jsonl(json.dumps(good) + "\n{\n")


if __name__ == "__main__":
    unittest.main()
