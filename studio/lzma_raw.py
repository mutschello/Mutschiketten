"""Minimal LZMA1 encoder that writes *no* end-of-payload marker.

The T50 firmware expects an LZMA-"alone" stream (props 0x5D, dict 8 KiB,
exact uncompressed size in the header) whose payload ends without the
end marker. Python's ``lzma`` module always writes the marker, which the
firmware is reported to choke on (heeen/supvan-cups, MIT). This module
implements the encoder side of the LZMA format directly:

* literals (plain and "matched" after a match, as the format requires)
* normal matches found with a small hash-chain match finder
* rep0 matches (cheap repeats of the last distance)

Parameters are fixed to lc=3, lp=0, pb=2, dict=8192 to match the firmware.
The output is verified in the test-suite with Python's own LZMA decoder.
"""

from __future__ import annotations

import struct

LC, LP, PB = 3, 0, 2
DICT_SIZE = 8192
MIN_MATCH = 2
MAX_MATCH = 273

_K_TOP = 1 << 24
_PROB_INIT = 1024
_N_STATES = 12
_POS_STATES = 1 << PB
_END_POS_MODEL = 14
_FULL_DIST = 128
_ALIGN_BITS = 4


class _RangeEncoder:
    __slots__ = ("low", "range", "cache", "cache_size", "out")

    def __init__(self) -> None:
        self.low = 0
        self.range = 0xFFFFFFFF
        self.cache = 0
        self.cache_size = 1
        self.out = bytearray()

    def _shift_low(self) -> None:
        low = self.low
        if (low & 0xFFFFFFFF) < 0xFF000000 or (low >> 32) != 0:
            carry = low >> 32
            temp = self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if self.cache_size == 0:
                    break
            self.cache = (low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (low & 0x00FFFFFF) << 8

    def bit(self, probs: list, i: int, b: int) -> None:
        p = probs[i]
        bound = (self.range >> 11) * p
        if b == 0:
            self.range = bound
            probs[i] = p + ((2048 - p) >> 5)
        else:
            self.low += bound
            self.range -= bound
            probs[i] = p - (p >> 5)
        while self.range < _K_TOP:
            self.range = (self.range << 8) & 0xFFFFFFFF
            self._shift_low()

    def direct(self, value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            self.range >>= 1
            if (value >> i) & 1:
                self.low += self.range
            while self.range < _K_TOP:
                self.range = (self.range << 8) & 0xFFFFFFFF
                self._shift_low()

    def flush(self) -> None:
        for _ in range(5):
            self._shift_low()


def _tree(rc: _RangeEncoder, probs: list, base: int, nbits: int, sym: int) -> None:
    m = 1
    for i in range(nbits - 1, -1, -1):
        b = (sym >> i) & 1
        rc.bit(probs, base + m, b)
        m = (m << 1) | b


def _tree_rev(rc: _RangeEncoder, probs: list, base: int, nbits: int, sym: int) -> None:
    m = 1
    for _ in range(nbits):
        b = sym & 1
        sym >>= 1
        rc.bit(probs, base + m, b)
        m = (m << 1) | b


class _LenEncoder:
    def __init__(self) -> None:
        self.choice = [_PROB_INIT] * 2
        self.low = [[_PROB_INIT] * 8 for _ in range(_POS_STATES)]
        self.mid = [[_PROB_INIT] * 8 for _ in range(_POS_STATES)]
        self.high = [_PROB_INIT] * 256

    def encode(self, rc: _RangeEncoder, length: int, pos_state: int) -> None:
        l = length - MIN_MATCH
        if l < 8:
            rc.bit(self.choice, 0, 0)
            _tree(rc, self.low[pos_state], 0, 3, l)
        elif l < 16:
            rc.bit(self.choice, 0, 1)
            rc.bit(self.choice, 1, 0)
            _tree(rc, self.mid[pos_state], 0, 3, l - 8)
        else:
            rc.bit(self.choice, 0, 1)
            rc.bit(self.choice, 1, 1)
            _tree(rc, self.high, 0, 8, l - 16)


def _pos_slot(dist: int) -> int:
    if dist < 4:
        return dist
    n = dist.bit_length() - 1
    return (n << 1) | ((dist >> (n - 1)) & 1)


def _longest(data: bytes, a: int, b: int, limit: int) -> int:
    """Length of the common run starting at a and b (a < b), up to limit."""
    n = 0
    while n < limit and data[a + n] == data[b + n]:
        n += 1
    return n


def compress_raw(data: bytes) -> bytes:
    """Return the LZMA1 payload (without header) for *data*, no end marker."""
    rc = _RangeEncoder()
    is_match = [_PROB_INIT] * (_N_STATES << PB)
    is_rep = [_PROB_INIT] * _N_STATES
    is_rep_g0 = [_PROB_INIT] * _N_STATES
    is_rep0_long = [_PROB_INIT] * (_N_STATES << PB)
    literal = [_PROB_INIT] * (0x300 << (LC + LP))
    pos_slot = [[_PROB_INIT] * 64 for _ in range(4)]
    spec_pos = [_PROB_INIT] * (1 + _FULL_DIST - _END_POS_MODEL)
    align = [_PROB_INIT] * (1 << _ALIGN_BITS)
    len_enc = _LenEncoder()
    rep_len_enc = _LenEncoder()

    state = 0
    rep0 = 0
    n = len(data)
    pos = 0
    head: dict[int, int] = {}
    chain = [-1] * n

    def insert(p: int) -> None:
        if p + 2 < n:
            h = data[p] | (data[p + 1] << 8) | (data[p + 2] << 16)
            chain[p] = head.get(h, -1)
            head[h] = p

    while pos < n:
        pos_state = pos & (_POS_STATES - 1)
        limit = min(MAX_MATCH, n - pos)

        # --- candidate: rep0 (repeat last distance) ---------------------------
        rep_len = 0
        if pos > rep0 and limit >= MIN_MATCH:
            rep_len = _longest(data, pos - rep0 - 1, pos, limit)

        # --- candidate: normal match via hash chain --------------------------
        best_len, best_dist = 0, 0
        if limit >= 3 and pos + 2 < n:
            h = data[pos] | (data[pos + 1] << 8) | (data[pos + 2] << 16)
            cand = head.get(h, -1)
            tries = 48
            while cand >= 0 and tries and pos - cand <= DICT_SIZE:
                ln = _longest(data, cand, pos, limit)
                if ln > best_len:
                    best_len, best_dist = ln, pos - cand - 1
                    if ln == limit:
                        break
                cand = chain[cand]
                tries -= 1

        if rep_len >= MIN_MATCH and rep_len + 1 >= best_len:
            # rep0 long match
            rc.bit(is_match, (state << PB) + pos_state, 1)
            rc.bit(is_rep, state, 1)
            rc.bit(is_rep_g0, state, 0)
            rc.bit(is_rep0_long, (state << PB) + pos_state, 1)
            rep_len_enc.encode(rc, rep_len, pos_state)
            state = 8 if state < 7 else 11
            for p in range(pos, pos + rep_len):
                insert(p)
            pos += rep_len
            continue

        if best_len >= 3:
            rc.bit(is_match, (state << PB) + pos_state, 1)
            rc.bit(is_rep, state, 0)
            len_enc.encode(rc, best_len, pos_state)
            len_state = min(best_len - MIN_MATCH, 3)
            slot = _pos_slot(best_dist)
            _tree(rc, pos_slot[len_state], 0, 6, slot)
            if slot >= 4:
                footer = (slot >> 1) - 1
                base = (2 | (slot & 1)) << footer
                reduced = best_dist - base
                if slot < _END_POS_MODEL:
                    _tree_rev(rc, spec_pos, base - slot, footer, reduced)
                else:
                    rc.direct(reduced >> _ALIGN_BITS, footer - _ALIGN_BITS)
                    _tree_rev(rc, align, 0, _ALIGN_BITS, reduced & 0xF)
            rep0 = best_dist
            state = 7 if state < 7 else 10
            for p in range(pos, pos + best_len):
                insert(p)
            pos += best_len
            continue

        # --- literal ----------------------------------------------------------
        rc.bit(is_match, (state << PB) + pos_state, 0)
        prev = data[pos - 1] if pos else 0
        base = 0x300 * (((pos & ((1 << LP) - 1)) << LC) + (prev >> (8 - LC)))
        sym = data[pos]
        if state < 7:
            _tree(rc, literal, base, 8, sym)
        else:
            match_byte = data[pos - rep0 - 1]
            offs = 0x100
            s = sym | 0x100
            while s < 0x10000:
                match_byte <<= 1
                rc.bit(literal, base + offs + (match_byte & offs) + (s >> 8), (s >> 7) & 1)
                s <<= 1
                offs &= ~(match_byte ^ s)
        state = 0 if state < 4 else (state - 3 if state < 10 else state - 6)
        insert(pos)
        pos += 1

    rc.flush()
    return bytes(rc.out)


def compress_alone(data: bytes) -> bytes:
    """LZMA-alone stream (13-byte header + payload) without end marker."""
    props = (PB * 5 + LP) * 9 + LC  # 0x5D
    header = bytes([props]) + struct.pack("<I", DICT_SIZE) + struct.pack("<Q", len(data))
    return header + compress_raw(data)
