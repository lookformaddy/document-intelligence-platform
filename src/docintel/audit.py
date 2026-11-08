"""Append-only, hash-chained audit log.

Each entry's hash covers the previous entry's hash plus its own
payload, so tampering, reordering, or deletion breaks the chain.
``verify()`` replays the chain and reports the first broken link.
"""
from __future__ import annotations

import hashlib
import json

from docintel.models import AuditEntry


def _hash_entry(seq: int, timestamp: str, actor: str, action: str,
                doc_id: str, detail: str, prev_hash: str) -> str:
    payload = json.dumps(
        {"seq": seq, "timestamp": timestamp, "actor": actor,
         "action": action, "doc_id": doc_id, "detail": detail,
         "prev_hash": prev_hash},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def append(self, timestamp: str, actor: str, action: str,
               doc_id: str, detail: str = "") -> AuditEntry:
        prev_hash = self.entries[-1].entry_hash if self.entries else "GENESIS"
        entry = AuditEntry(
            seq=len(self.entries) + 1, timestamp=timestamp, actor=actor,
            action=action, doc_id=doc_id, detail=detail,
            prev_hash=prev_hash,
            entry_hash=_hash_entry(len(self.entries) + 1, timestamp, actor,
                                  action, doc_id, detail, prev_hash))
        self.entries.append(entry)
        return entry

    def verify(self) -> tuple[bool, str]:
        prev_hash = "GENESIS"
        for entry in self.entries:
            if entry.prev_hash != prev_hash:
                return False, f"broken chain at seq {entry.seq}: prev_hash mismatch"
            expected = _hash_entry(entry.seq, entry.timestamp, entry.actor,
                                  entry.action, entry.doc_id, entry.detail,
                                  entry.prev_hash)
            if expected != entry.entry_hash:
                return False, f"tampered entry at seq {entry.seq}"
            prev_hash = entry.entry_hash
        return True, f"chain intact ({len(self.entries)} entries)"

    def for_document(self, doc_id: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.doc_id == doc_id]
