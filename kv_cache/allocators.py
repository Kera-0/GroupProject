"""Real bytearray-backed storage; no model, attention kernel or scheduler yet."""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


class OutOfMemory(RuntimeError):
    """A valid operation cannot fit in the fixed-capacity pool."""


@dataclass
class Sequence:
    limit: int
    length: int = 0
    slots: List[int] = field(default_factory=list)


class Cache:
    def __init__(self, capacity_tokens: int, bytes_per_token: int = 16):
        if capacity_tokens <= 0 or bytes_per_token <= 0:
            raise ValueError("capacity and token width must be positive")
        self.capacity = capacity_tokens
        self.width = bytes_per_token
        self.pool = bytearray(capacity_tokens * bytes_per_token)
        self.sequences: Dict[str, Sequence] = {}
        self.failures = 0

    def _validate_new(self, request_id: str, limit: int) -> None:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a nonempty string")
        if request_id in self.sequences or limit <= 0:
            raise ValueError("duplicate request or nonpositive limit")

    def _fail(self) -> None:
        self.failures += 1
        raise OutOfMemory("KV pool exhausted or fragmented")

    def _write(self, slot: int, payload: bytes) -> None:
        start = slot * self.width
        self.pool[start:start + self.width] = payload

    def _validate_append(self, request_id: str, payload: bytes) -> Sequence:
        seq = self.sequences[request_id]
        if not isinstance(payload, bytes) or len(payload) != self.width:
            raise ValueError("payload must contain exactly bytes_per_token bytes")
        if seq.length >= seq.limit:
            raise ValueError("sequence length limit reached")
        return seq

    def _physical_slot(self, seq: Sequence, index: int) -> int:
        raise NotImplementedError

    def read(self, request_id: str, index: int) -> bytes:
        seq = self.sequences[request_id]
        if not 0 <= index < seq.length:
            raise IndexError(index)
        start = self._physical_slot(seq, index) * self.width
        return bytes(self.pool[start:start + self.width])

    def _occupied_slots(self) -> List[int]:
        return [slot for seq in self.sequences.values() for slot in seq.slots]

    def snapshot(self) -> dict:
        allocated = len(self._occupied_slots())
        live = sum(seq.length for seq in self.sequences.values())
        return {
            "pool_payload_bytes": len(self.pool),
            "allocated_bytes": allocated * self.width,
            "live_bytes": live * self.width,
            "slack_bytes": (allocated - live) * self.width,
            "free_bytes": (self.capacity - allocated) * self.width,
            "active_requests": len(self.sequences),
            "allocation_failures": self.failures,
        }

    def check_invariants(self) -> None:
        occupied = self._occupied_slots()
        assert len(occupied) == len(set(occupied)), "overlapping ownership"
        assert all(0 <= slot < self.capacity for slot in occupied)
        assert all(0 <= seq.length <= seq.limit for seq in self.sequences.values())
        assert len(self.pool) == self.capacity * self.width


class ContiguousCache(Cache):
    """First-fit reservation of the declared maximum length; coalescing on free."""

    def __init__(self, capacity_tokens: int, bytes_per_token: int = 16):
        super().__init__(capacity_tokens, bytes_per_token)
        self.free_ranges: List[Tuple[int, int]] = [(0, capacity_tokens)]

    def reserve(self, request_id: str, limit: int) -> None:
        self._validate_new(request_id, limit)
        for i, (start, size) in enumerate(self.free_ranges):
            if size >= limit:
                self.sequences[request_id] = Sequence(
                    limit, slots=list(range(start, start + limit)))
                if size == limit:
                    self.free_ranges.pop(i)
                else:
                    self.free_ranges[i] = (start + limit, size - limit)
                return
        self._fail()

    def append(self, request_id: str, payload: bytes) -> None:
        seq = self._validate_append(request_id, payload)
        self._write(seq.slots[seq.length], payload)
        seq.length += 1

    def _physical_slot(self, seq: Sequence, index: int) -> int:
        return seq.slots[index]

    def free(self, request_id: str) -> None:
        seq = self.sequences.pop(request_id)
        ranges = sorted(self.free_ranges + [(seq.slots[0], seq.limit)])
        merged: List[Tuple[int, int]] = []
        for start, size in ranges:
            if merged and sum(merged[-1]) == start:
                previous, length = merged.pop()
                merged.append((previous, length + size))
            else:
                merged.append((start, size))
        self.free_ranges = merged

    def snapshot(self) -> dict:
        result = super().snapshot()
        largest = max((size for _, size in self.free_ranges), default=0)
        result["largest_free_span_bytes"] = largest * self.width
        result["external_fragmentation_ratio"] = (
            1 - largest * self.width / result["free_bytes"]
            if result["free_bytes"] else 0.0)
        return result

    def check_invariants(self) -> None:
        super().check_invariants()
        free = [slot for start, size in self.free_ranges
                for slot in range(start, start + size)]
        assert self.free_ranges == sorted(self.free_ranges)
        assert all(size > 0 for _, size in self.free_ranges)
        assert all(a + size < b for (a, size), (b, _) in
                   zip(self.free_ranges, self.free_ranges[1:]))
        assert sorted(free + self._occupied_slots()) == list(range(self.capacity))
        for seq in self.sequences.values():
            assert seq.slots == list(range(seq.slots[0], seq.slots[0] + seq.limit))


class PagedCache(Cache):
    """Fixed blocks allocated only when append crosses a block boundary."""

    def __init__(self, capacity_tokens: int, bytes_per_token: int = 16,
                 block_tokens: int = 4):
        if block_tokens <= 0 or capacity_tokens % block_tokens:
            raise ValueError("capacity must be divisible by positive block size")
        super().__init__(capacity_tokens, bytes_per_token)
        self.block = block_tokens
        self.free_blocks = list(reversed(range(capacity_tokens // block_tokens)))

    def reserve(self, request_id: str, limit: int) -> None:
        self._validate_new(request_id, limit)
        self.sequences[request_id] = Sequence(limit)

    def append(self, request_id: str, payload: bytes) -> None:
        seq = self._validate_append(request_id, payload)
        if seq.length % self.block == 0:
            if not self.free_blocks:
                self._fail()
            seq.slots.append(self.free_blocks.pop())
        self._write(self._physical_slot(seq, seq.length), payload)
        seq.length += 1

    def _physical_slot(self, seq: Sequence, index: int) -> int:
        return seq.slots[index // self.block] * self.block + index % self.block

    def _occupied_slots(self) -> List[int]:
        return [block * self.block + offset
                for seq in self.sequences.values() for block in seq.slots
                for offset in range(self.block)]

    def free(self, request_id: str) -> None:
        self.free_blocks.extend(self.sequences.pop(request_id).slots)

    def snapshot(self) -> dict:
        result = super().snapshot()
        result["largest_free_span_bytes"] = None
        result["external_fragmentation_ratio"] = 0.0
        return result

    def check_invariants(self) -> None:
        super().check_invariants()
        blocks = [block for seq in self.sequences.values() for block in seq.slots]
        assert sorted(blocks + self.free_blocks) == list(range(self.capacity // self.block))
        assert all(len(seq.slots) == (seq.length + self.block - 1) // self.block
                   for seq in self.sequences.values())
