"""Owner signatures for UniSync: stateful hash-based signatures (Lamport + Merkle tree), stdlib only.

Why: the ledger's authority rules check the *name* on an entry. Anyone who can write rows could post
as the owner. With an owner key configured, an owner entry also needs a signature that only the holder
of the owner's secret seed can make, so approvals, halts and other owner actions cannot be forged.

Scheme (SHA-256 throughout, no third-party crypto):
* One-time keys: Lamport over the 256 bits of a 32-byte message. Secret key `sk[j][b]` is derived from
  the seed, public key `pk[j][b] = H(sk[j][b])`, and a leaf is `H(pk[0][0] || pk[0][1] || ... )`.
* Many-time key: 2**height leaves under a Merkle tree; the root (32 bytes, hex) is the public owner key.
* Signature for leaf i: i, then for each bit j the revealed `sk[j][bit]` and the unrevealed `pk[j][1-bit]`,
  then the Merkle authentication path. Verification rebuilds the leaf, walks the path, and compares the
  root. Base64, ~22 KB; owner actions are rare.
* Each leaf may sign one message only. Signers pick the next unused index; ledgers reject an index reused
  for a different message. Height 10 gives 1024 owner actions per key, then rotate to a new seed.

Security rests only on SHA-256 preimage/collision resistance (also post-quantum). Keep the seed secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

BITS = 256
NODE = 32


def _h(*parts: bytes) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


class OwnerSigError(ValueError):
    """A signature is malformed or does not verify. Never raised for well-formed valid input."""


def _sk(seed: bytes, i: int, j: int, b: int) -> bytes:
    return _h(b"unisync-owner-lamport", seed, i.to_bytes(4, "big"), j.to_bytes(2, "big"), bytes([b]))


def _leaf_from_pk(pk_pairs: list[tuple[bytes, bytes]]) -> bytes:
    return _h(b"leaf", *(p for pair in pk_pairs for p in pair))


def _bits(message: bytes) -> list[int]:
    digest = _h(b"msg", message)
    return [(digest[j // 8] >> (7 - j % 8)) & 1 for j in range(BITS)]


class OwnerKey:
    """The owner's signing key: a secret seed and the Merkle tree of its one-time keys."""

    def __init__(self, seed: bytes, height: int = 10) -> None:
        if len(seed) < 32:
            raise ValueError("owner seed must be at least 32 bytes")
        if not 1 <= height <= 16:
            raise ValueError("height must be between 1 and 16")
        self.seed = seed
        self.height = height
        self._levels: list[list[bytes]] | None = None

    @classmethod
    def generate(cls, height: int = 10) -> OwnerKey:
        return cls(os.urandom(32), height)

    @property
    def capacity(self) -> int:
        return 1 << self.height

    def _pk_pairs(self, i: int) -> list[tuple[bytes, bytes]]:
        return [(_h(_sk(self.seed, i, j, 0)), _h(_sk(self.seed, i, j, 1))) for j in range(BITS)]

    def _tree(self) -> list[list[bytes]]:
        if self._levels is None:
            level = [_leaf_from_pk(self._pk_pairs(i)) for i in range(self.capacity)]
            levels = [level]
            while len(level) > 1:
                level = [_h(b"node", level[k], level[k + 1]) for k in range(0, len(level), 2)]
                levels.append(level)
            self._levels = levels
        return self._levels

    @property
    def root(self) -> str:
        return self._tree()[-1][0].hex()

    def sign(self, message: bytes, index: int) -> str:
        if not 0 <= index < self.capacity:
            raise OwnerSigError(f"owner key exhausted: index {index} >= capacity {self.capacity}; rotate the key")
        out = [index.to_bytes(4, "big"), bytes([self.height])]
        for j, bit in enumerate(_bits(message)):
            out.append(_sk(self.seed, index, j, bit))  # revealed secret for this bit
            out.append(_h(_sk(self.seed, index, j, 1 - bit)))  # public half for the other bit
        levels = self._tree()
        node = index
        for level in levels[:-1]:
            out.append(level[node ^ 1])
            node >>= 1
        return base64.b64encode(b"".join(out)).decode("ascii")


def sig_index(signature: str) -> int:
    try:
        raw = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise OwnerSigError("signature is not base64") from exc
    if len(raw) < 5:
        raise OwnerSigError("signature too short")
    return int.from_bytes(raw[:4], "big")


def verify(message: bytes, signature: str, root_hex: str) -> int:
    """Check `signature` over `message` against the owner key `root_hex`. Returns the leaf index used."""
    try:
        raw = base64.b64decode(signature, validate=True)
        root = bytes.fromhex(root_hex)
    except (ValueError, TypeError) as exc:
        raise OwnerSigError("signature or owner key is not well-formed") from exc
    if len(raw) < 5 or len(root) != NODE:
        raise OwnerSigError("signature or owner key has the wrong size")
    index, height = int.from_bytes(raw[:4], "big"), raw[4]
    expect = 5 + BITS * 2 * NODE + height * NODE
    if not 1 <= height <= 16 or len(raw) != expect or index >= (1 << height):
        raise OwnerSigError("signature has the wrong shape")
    body = raw[5:]
    pairs: list[tuple[bytes, bytes]] = []
    for j, bit in enumerate(_bits(message)):
        revealed = body[j * 2 * NODE : j * 2 * NODE + NODE]
        other = body[j * 2 * NODE + NODE : (j + 1) * 2 * NODE]
        mine = _h(revealed)
        pairs.append((mine, other) if bit == 0 else (other, mine))
    node = _leaf_from_pk(pairs)
    path = body[BITS * 2 * NODE :]
    position = index
    for k in range(height):
        sibling = path[k * NODE : (k + 1) * NODE]
        node = _h(b"node", sibling, node) if position & 1 else _h(b"node", node, sibling)
        position >>= 1
    if node != root:
        raise OwnerSigError("signature does not verify against the owner key")
    return index


class OwnerSigner:
    """Signs owner envelopes, never reusing a leaf, even across failed or retried posts.

    `state_path` (optional) remembers the highest index ever handed out on this machine, so a post that
    was sent but not stored never has its leaf reused. The ledger's own highest index is also respected.
    """

    def __init__(self, key: OwnerKey, state_path: Path | None = None) -> None:
        self.key = key
        self.state_path = state_path

    def _burned(self) -> int:
        if not self.state_path or not self.state_path.exists():
            return -1
        try:
            return int(json.loads(self.state_path.read_text(encoding="utf-8"))["max_index"])
        except (OSError, ValueError, KeyError, TypeError):
            return -1

    def sign(self, message: bytes, ledger_max_index: int) -> str:
        index = max(ledger_max_index, self._burned()) + 1
        signature = self.key.sign(message, index)
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"max_index": index, "root": self.key.root}), encoding="utf-8")
        return signature


Signer = Callable[[bytes, int], str]
