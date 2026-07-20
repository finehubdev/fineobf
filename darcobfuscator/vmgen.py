from __future__ import annotations

from .bytecode import OPCODES, OpcodeMap

HANDLERS: list[tuple[str, str]] = [
    ("LOADK", "sp = sp + 1; S[sp] = K[ins[2]]"),
    ("LOADNIL", "sp = sp + 1; S[sp] = nil"),
    ("LOADTRUE", "sp = sp + 1; S[sp] = true"),
    ("LOADFALSE", "sp = sp + 1; S[sp] = false"),
    ("GETLOCAL", "sp = sp + 1; S[sp] = L[ins[2]][1]"),
    ("SETLOCAL", "L[ins[2]][1] = S[sp]; S[sp] = nil; sp = sp - 1"),
    ("NEWLOCAL", "L[ins[2]] = { S[sp] }; S[sp] = nil; sp = sp - 1"),
    ("GETUPVAL", "sp = sp + 1; S[sp] = upvals[ins[2]][1]"),
    ("SETUPVAL", "upvals[ins[2]][1] = S[sp]; S[sp] = nil; sp = sp - 1"),
    ("GETGLOBAL", "sp = sp + 1; S[sp] = ENV[K[ins[2]]]"),
    ("SETGLOBAL", "ENV[K[ins[2]]] = S[sp]; S[sp] = nil; sp = sp - 1"),
    ("NEWTABLE", "sp = sp + 1; S[sp] = {}"),
    ("GETINDEX",
     "local k = S[sp]; local t = S[sp - 1]\n"
     "S[sp - 1] = t[k]; S[sp] = nil; sp = sp - 1"),
    ("SETINDEX",
     "local v = S[sp]; local k = S[sp - 1]; local t = S[sp - 2]\n"
     "t[k] = v; S[sp] = nil; S[sp - 1] = nil; S[sp - 2] = nil; sp = sp - 3"),
    ("SETFIELD",
     "local v = S[sp]; local k = S[sp - 1]; local t = S[sp - 2]\n"
     "t[k] = v; S[sp] = nil; S[sp - 1] = nil; sp = sp - 2"),
    ("SETARRAYIDX",
     "local v = S[sp]; local t = S[sp - 1]\n"
     "t[ins[2]] = v; S[sp] = nil; sp = sp - 1"),
    ("APPENDLIST",
     "local arr = S[sp]; local t = S[sp - 1]; local base = ins[2]\n"
     "for i = 1, arr.n do t[base + i - 1] = arr[i] end\n"
     "S[sp] = nil; sp = sp - 1"),
    ("ADD", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]+b"),
    ("SUB", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]-b"),
    ("MUL", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]*b"),
    ("DIV", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]/b"),
    ("IDIV", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=floor(S[sp]/b)"),
    ("MOD", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]%b"),
    ("POW", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]^b"),
    ("CONCAT", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=S[sp]..b"),
    ("EQ", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]==b)"),
    ("NE", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]~=b)"),
    ("LT", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]<b)"),
    ("LE", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]<=b)"),
    ("GT", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]>b)"),
    ("GE", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=(S[sp]>=b)"),
    ("BAND", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=band(S[sp],b)"),
    ("BOR", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=bor(S[sp],b)"),
    ("BXOR", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=bxor(S[sp],b)"),
    ("SHL", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=lshift(S[sp],b)"),
    ("SHR", "local b=S[sp]; S[sp]=nil; sp=sp-1; S[sp]=rshift(S[sp],b)"),
    ("UNM", "S[sp] = -S[sp]"),
    ("NOT", "S[sp] = not S[sp]"),
    ("LEN", "S[sp] = #S[sp]"),
    ("BNOT", "S[sp] = bnot(S[sp])"),
    ("DUP", "sp = sp + 1; S[sp] = S[sp - 1]"),
    ("POP", "S[sp] = nil; sp = sp - 1"),
    ("SWAP", "local x = S[sp]; S[sp] = S[sp - 1]; S[sp - 1] = x"),
    ("ROT3",
     "local a = S[sp - 2]; S[sp - 2] = S[sp - 1]; S[sp - 1] = S[sp]; S[sp] = a"),
    ("JMP", "pc = ins[2] + 1"),
    ("JMPIFFALSE",
     "local v = S[sp]; S[sp] = nil; sp = sp - 1\n"
     "if not v then pc = ins[2] + 1 end"),
    ("JMPIFTRUE",
     "local v = S[sp]; S[sp] = nil; sp = sp - 1\n"
     "if v then pc = ins[2] + 1 end"),
    ("JMPIFNIL",
     "local v = S[sp]; S[sp] = nil; sp = sp - 1\n"
     "if v == nil then pc = ins[2] + 1 end"),
    ("NEWARR", "sp = sp + 1; S[sp] = { n = 0 }"),
    ("ARRPUSH",
     "local v = S[sp]; local arr = S[sp - 1]\n"
     "arr.n = arr.n + 1; arr[arr.n] = v; S[sp] = nil; sp = sp - 1"),
    ("ARREXTEND",
     "local src = S[sp]; local arr = S[sp - 1]\n"
     "for i = 1, src.n do arr.n = arr.n + 1; arr[arr.n] = src[i] end\n"
     "S[sp] = nil; sp = sp - 1"),
    ("EXPAND",
     "local arr = S[sp]; S[sp] = nil; sp = sp - 1\n"
     "local want = ins[2]\n"
     "for i = 1, want do sp = sp + 1; S[sp] = arr[i] end"),
    ("CALL",
     "local arr = S[sp]; local fn = S[sp - 1]\n"
     "S[sp] = nil; S[sp - 1] = nil; sp = sp - 2\n"
     "local r = pack(fn(unpack(arr, 1, arr.n)))\n"
     "sp = sp + 1; S[sp] = r[1]"),
    ("CALLM",
     "local arr = S[sp]; local fn = S[sp - 1]\n"
     "S[sp] = nil; S[sp - 1] = nil; sp = sp - 2\n"
     "local r = pack(fn(unpack(arr, 1, arr.n)))\n"
     "sp = sp + 1; S[sp] = r"),
    ("CALLVOID",
     "local arr = S[sp]; local fn = S[sp - 1]\n"
     "S[sp] = nil; S[sp - 1] = nil; sp = sp - 2\n"
     "fn(unpack(arr, 1, arr.n))"),
    ("RETURN", "return S[sp]"),
    ("CLOSURE",
     "local child = protos[ins[2]]\n"
     "local u = {}\n"
     "local descs = child.u\n"
     "for i = 1, #descs do\n"
     "    local d = descs[i]\n"
     "    if d[1] == 1 then u[i] = L[d[2]] else u[i] = upvals[d[2]] end\n"
     "end\n"
     "sp = sp + 1; S[sp] = makeClosure(child, u)"),
    ("VARARG1", "sp = sp + 1; S[sp] = varargs and varargs[1] or nil"),
    ("VARARGM", "sp = sp + 1; S[sp] = varargs or { n = 0 }"),
    ("FORTEST",
     "local step = S[sp]; local limit = S[sp - 1]; local i = S[sp - 2]\n"
     "S[sp] = nil; S[sp - 1] = nil; sp = sp - 2\n"
     "if step > 0 then S[sp] = (i <= limit) else S[sp] = (i >= limit) end"),
    ("ISNIL", "S[sp] = (S[sp] == nil)"),
]

assert [n for n, _ in HANDLERS] == list(OPCODES), "vmgen/HANDLERS drift vs OPCODES"

FUSIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("LOADK", "ADD"):      ("F_KADD", "S[sp] = S[sp] + K[ins[2]]"),
    ("LOADK", "SUB"):      ("F_KSUB", "S[sp] = S[sp] - K[ins[2]]"),
    ("LOADK", "MUL"):      ("F_KMUL", "S[sp] = S[sp] * K[ins[2]]"),
    ("LOADK", "CONCAT"):   ("F_KCONCAT", "S[sp] = S[sp] .. K[ins[2]]"),
    ("LOADK", "EQ"):       ("F_KEQ", "S[sp] = (S[sp] == K[ins[2]])"),
    ("LOADK", "GETINDEX"): ("F_KINDEX", "S[sp] = S[sp][K[ins[2]]]"),
    ("GETLOCAL", "ADD"):   ("F_LADD", "S[sp] = S[sp] + L[ins[2]][1]"),
    ("GETLOCAL", "SUB"):   ("F_LSUB", "S[sp] = S[sp] - L[ins[2]][1]"),
    ("GETLOCAL", "MUL"):   ("F_LMUL", "S[sp] = S[sp] * L[ins[2]][1]"),
    ("GETLOCAL", "LT"):    ("F_LLT", "S[sp] = (S[sp] < L[ins[2]][1])"),
    ("GETLOCAL", "LE"):    ("F_LLE", "S[sp] = (S[sp] <= L[ins[2]][1])"),
    ("GETLOCAL", "GETINDEX"): ("F_LINDEX", "S[sp] = S[sp][L[ins[2]][1]]"),
    ("GETUPVAL", "GETINDEX"): ("F_UINDEX", "S[sp] = S[sp][upvals[ins[2]][1]]"),
    ("GETGLOBAL", "GETINDEX"): ("F_GINDEX", "S[sp] = S[sp][ENV[K[ins[2]]]]"),
}

_DECOY_BODIES = [
    "sp = sp + 1; S[sp] = ins[2]",
    "S[sp] = S[sp]; sp = sp - 0",
    "local x = S[sp]; S[sp] = x",
    "sp = sp + 1; S[sp] = -ins[2]",
    "S[sp - 1] = S[sp]; S[sp] = nil; sp = sp - 1",
    "local t = S[sp]; S[sp] = t and t or ins[2]",
    "sp = sp + 1; S[sp] = { ins[2] }",
]

def select_fusions(rng) -> dict[tuple[str, str], tuple[str, str]]:
    items = list(FUSIONS.items())
    rng.shuffle(items)
    k = rng.randint(max(1, len(items) // 2), len(items))
    return {pair: spec for pair, spec in items[:k]}

def build_opmap(rng, fused_names: list[str]) -> OpcodeMap:
    return OpcodeMap.randomized(rng, extra_names=fused_names)

def _clause(keyword: str, num: int, body: str) -> str:
    return f"{keyword} op == {num} then\n{body}\n"

def render_dispatch(opmap: OpcodeMap, rng, fused: dict, n_decoys: int = 6) -> str:
    entries = []
    for name, body in HANDLERS:
        entries.append((opmap[name], body))
    for (_p1, _p2), (fname, body) in fused.items():
        entries.append((opmap[fname], body))

    used = {num for num, _ in entries}
    if rng is not None:
        top = max(used)
        decoy_nums = list(range(top + 1, top + 1 + n_decoys))
        for dn in decoy_nums:
            entries.append((dn, _DECOY_BODIES[rng.randrange(len(_DECOY_BODIES))]))
        rng.shuffle(entries)

    parts = []
    for i, (num, body) in enumerate(entries):
        parts.append(_clause("if" if i == 0 else "elseif", num, body))
    parts.append("else\nerror(nil)\nend")
    return "".join(parts)

def render_vm(vm_template: str, opmap: OpcodeMap, rng,
              fused: dict | None = None) -> str:
    dispatch = render_dispatch(opmap, rng, fused or {})
    return vm_template.replace("--@DISPATCH@", dispatch)
