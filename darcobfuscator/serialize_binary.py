from __future__ import annotations

import struct

from .bytecode import OpcodeMap, Proto

KLEN = 16
CLEN = 8

def derive_stream(seed: int, count: int) -> list[int]:
    state = seed % 16777216
    out = []
    for _ in range(count):
        state = (state * 214013 + 2531011) % 16777216
        out.append((state // 65536) % 256)
    return out

def _layer_params(cseed: int):
    s = derive_stream(cseed, CLEN + 2)
    ckey = s[:CLEN]
    opxor = s[CLEN]
    opstep = s[CLEN + 1] | 1
    return ckey, opxor, opstep

def _uvarint(out: bytearray, n: int):
    if n < 0:
        raise ValueError("uvarint negative")
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break

class _Ctx:

    def __init__(self, opmap: OpcodeMap, ckey: list[int], opxor: int, opstep: int):
        self.opmap = opmap
        self.ckey = ckey
        self.opxor = opxor
        self.opstep = opstep
        self.cc = 0

    def const_byte(self, b: int) -> int:
        cc = self.cc
        k = (self.ckey[cc % CLEN] + cc * 7) & 0xFF
        self.cc = cc + 1
        return b ^ k

def _write_proto(out: bytearray, p: Proto, ctx: _Ctx):
    _uvarint(out, p.numparams)
    out.append(1 if p.is_vararg else 0)
    _uvarint(out, len(p.consts))
    for c in p.consts:
        if isinstance(c, str):
            raw = c.encode("utf-8")
            out.append(1)
            _uvarint(out, len(raw))
            for b in raw:
                out.append(ctx.const_byte(b))
        elif float(c).is_integer() and abs(c) < 1e15:
            out.append(2)
            for b in struct.pack("<q", int(c)):
                out.append(ctx.const_byte(b))
        else:
            out.append(0)
            for b in struct.pack("<d", float(c)):
                out.append(ctx.const_byte(b))
    _uvarint(out, len(p.code))
    for idx, ins in enumerate(p.code):
        raw_op = ctx.opmap[ins.op]
        opkey = (ctx.opxor + idx * ctx.opstep) & 0xFF
        _uvarint(out, raw_op ^ opkey)
        _uvarint(out, ins.a & 0xFFFFFFFF)
    _uvarint(out, len(p.protos))
    for sub in p.protos:
        _write_proto(out, sub, ctx)
    _uvarint(out, len(p.upvals))
    for kind, idx in p.upvals:
        out.append(kind)
        _uvarint(out, idx)

def serialize(proto: Proto, opmap: OpcodeMap, cseed: int) -> bytes:
    ckey, opxor, opstep = _layer_params(cseed)
    ctx = _Ctx(opmap, ckey, opxor, opstep)
    out = bytearray()
    _write_proto(out, proto, ctx)
    return bytes(out)

def encrypt(data: bytes, key: list[int], iv: int) -> tuple[bytes, int]:
    kl = len(key)
    out = bytearray(len(data))
    prev = iv & 0xFF
    h = 0
    for i, b in enumerate(data):
        ks = (key[i % kl] + i * 31) & 0xFF
        ks ^= prev
        c = b ^ ks
        prev = c
        out[i] = c
        h = (h * 131 + b) % 16777216
    return bytes(out), h

_LZ_WINDOW = 4095
_LZ_MINLEN = 3
_LZ_MAXLEN = 18
_LZ_MAXCHAIN = 64

def lz_compress(data: bytes) -> bytes:
    n = len(data)
    out = bytearray()
    head: dict[bytes, int] = {}
    prev = [-1] * n
    i = 0
    while i < n:
        flags = 0
        tokens = bytearray()
        for bit in range(8):
            if i >= n:
                break
            best_len, best_off = 0, 0
            if i + _LZ_MINLEN <= n:
                key = data[i:i + 3]
                j = head.get(key, -1)
                chain = 0
                lo = i - _LZ_WINDOW
                while j >= 0 and j >= lo and chain < _LZ_MAXCHAIN:
                    l = 0
                    maxl = min(_LZ_MAXLEN, n - i)
                    while l < maxl and data[j + l] == data[i + l]:
                        l += 1
                    if l > best_len:
                        best_len, best_off = l, i - j
                        if l == _LZ_MAXLEN:
                            break
                    j = prev[j]
                    chain += 1
            if best_len >= _LZ_MINLEN:
                b2 = ((best_off >> 8) << 4) | (best_len - _LZ_MINLEN)
                tokens.append(best_off & 0xFF)
                tokens.append(b2)
                for k in range(best_len):
                    p = i + k
                    if p + 3 <= n:
                        key = data[p:p + 3]
                        prev[p] = head.get(key, -1)
                        head[key] = p
                i += best_len
            else:
                flags |= (1 << bit)
                tokens.append(data[i])
                if i + 3 <= n:
                    key = data[i:i + 3]
                    prev[i] = head.get(key, -1)
                    head[key] = i
                i += 1
        out.append(flags)
        out.extend(tokens)
    return bytes(out)

def lz_decompress(comp: bytes, outlen: int) -> bytes:
    out = bytearray()
    pos = 0
    while len(out) < outlen:
        ctrl = comp[pos]
        pos += 1
        for _ in range(8):
            if len(out) >= outlen:
                break
            flag = ctrl & 1
            ctrl >>= 1
            if flag == 1:
                out.append(comp[pos])
                pos += 1
            else:
                b1, b2 = comp[pos], comp[pos + 1]
                pos += 2
                length = (b2 & 0x0F) + _LZ_MINLEN
                offset = b1 + (b2 >> 4) * 256
                start = len(out) - offset
                for k in range(length):
                    out.append(out[start + k])
    return bytes(out)

def ascii85_encode(data: bytes) -> str:
    pad = (-len(data)) % 4
    padded = data + b"\x00" * pad
    out = []
    for i in range(0, len(padded), 4):
        k = padded[i] | (padded[i + 1] << 8) | (padded[i + 2] << 16) | (padded[i + 3] << 24)
        if k == 0:
            out.append("z")
        else:
            out.append(
                chr(33 + k // 52200625 % 85)
                + chr(33 + k // 614125 % 85)
                + chr(33 + k // 7225 % 85)
                + chr(33 + k // 85 % 85)
                + chr(33 + k % 85)
            )
    return "".join(out)

def encode_program(proto: Proto, opmap: OpcodeMap, rng) -> dict:
    oseed = rng.randrange(1, 16777216)
    cseed = rng.randrange(1, 16777216)
    iv = rng.randrange(0, 256)
    plain = serialize(proto, opmap, cseed)
    comp = lz_compress(plain)
    if len(comp) < len(plain):
        payload = b"\x01" + comp
    else:
        payload = b"\x00" + plain
    key = derive_stream(oseed, KLEN)
    cipher, checksum = encrypt(payload, key, iv)
    return {
        "cipher": cipher,
        "oseed": oseed,
        "cseed": cseed,
        "iv": iv,
        "checksum": checksum,
        "plain_len": len(plain),
    }
