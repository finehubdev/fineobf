from __future__ import annotations

from .bytecode import OPCODES, OP, OpcodeMap, Proto

def opcode_decl(opmap: OpcodeMap | None = None) -> str:
    om = opmap or OpcodeMap.canonical()
    names = ", ".join(OPCODES)
    nums = ", ".join(str(om[n]) for n in OPCODES)
    return f"local {names} = {nums}"

def lua_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%03d" % o)
        elif o > 127:
            for b in ch.encode("utf-8"):
                out.append("\\%03d" % b)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)

def lua_number(x: float) -> str:
    if x != x:
        return "(0/0)"
    if x == float("inf"):
        return "(1/0)"
    if x == float("-inf"):
        return "(-1/0)"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(x)

def _const(value) -> str:
    if isinstance(value, str):
        return lua_string(value)
    return lua_number(float(value))

def proto_to_lua(proto: Proto, opmap: OpcodeMap | None = None) -> str:
    om = opmap or OpcodeMap.canonical()
    parts = []
    parts.append("np=%d" % proto.numparams)
    parts.append("va=" + ("true" if proto.is_vararg else "false"))
    ks = ",".join(_const(c) for c in proto.consts)
    parts.append("k={%s}" % ks)
    ins_parts = []
    for ins in proto.code:
        if ins.nargs >= 1:
            ins_parts.append("{%d,%d}" % (om[ins.op], ins.a))
        else:
            ins_parts.append("{%d}" % om[ins.op])
    parts.append("c={%s}" % ",".join(ins_parts))
    ps = ",".join(proto_to_lua(p, om) for p in proto.protos)
    parts.append("p={%s}" % ps)
    us = ",".join("{%d,%d}" % (kind, idx) for (kind, idx) in proto.upvals)
    parts.append("u={%s}" % us)
    return "{" + ",".join(parts) + "}"
