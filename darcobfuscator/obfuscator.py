from __future__ import annotations

import os
import random
import re
import string as _string

from . import __version__
from .parser import parse
from .compiler import (compile_source_to_proto, apply_fusions,
                       shuffle_constants, scramble_cfg)
from .serialize_binary import encode_program, ascii85_encode
from . import vmgen

HEADER = f"--[[ obfuscated with fine v{__version__} ]]"
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

def _read_template(name: str) -> str:
    with open(os.path.join(TEMPLATES, name)) as f:
        return f.read()

def bytes_to_lua_string(bs: bytes) -> str:
    out = ['"']
    for b in bs:
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append("\\\\")
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append("\\%03d" % b)
    out.append('"')
    return "".join(out)

def quote_base85(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)

def strip_comments(src: str) -> str:
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in ("'", '"'):
            q = ch
            out.append(ch); i += 1
            while i < n:
                c = src[i]
                out.append(c)
                if c == "\\" and i + 1 < n:
                    out.append(src[i + 1]); i += 2; continue
                i += 1
                if c == q:
                    break
            continue
        if ch == "[" and i + 1 < n and src[i + 1] in "[=":
            m = re.match(r"\[(=*)\[", src[i:])
            if m:
                close = "]" + m.group(1) + "]"
                end = src.find(close, i + m.end())
                seg = src[i:] if end < 0 else src[i:end + len(close)]
                out.append(seg)
                i += len(seg)
                continue
        if ch == "-" and i + 1 < n and src[i + 1] == "-":
            j = i + 2
            m = re.match(r"\[(=*)\[", src[j:])
            if m:
                close = "]" + m.group(1) + "]"
                end = src.find(close, j + m.end())
                i = n if end < 0 else end + len(close)
            else:
                nl = src.find("\n", j)
                i = n if nl < 0 else nl
            continue
        out.append(ch); i += 1
    return "".join(out)

def minify_ws(src: str) -> str:
    lines = []
    for line in src.splitlines():
        s = line.strip()
        if s:
            lines.append(s)
    return " ".join(lines)

class NameGen:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.used = set()

    def new(self) -> str:
        alpha = "IlO0ocQ"
        while True:
            name = self.rng.choice("Il") + "".join(
                self.rng.choice(alpha) for _ in range(self.rng.randint(6, 12)))
            if name not in self.used:
                self.used.add(name)
                return name

def rename_identifiers(src: str, names: list[str], gen: NameGen) -> str:
    mapping = {name: gen.new() for name in names}
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in
                         sorted(names, key=len, reverse=True)) + r")\b")

    def sub_code(seg: str) -> str:
        return pattern.sub(lambda m: mapping[m.group(1)], seg)

    out = []
    i, n = 0, len(src)
    seg_start = 0
    while i < n:
        ch = src[i]
        if ch == "'" or ch == '"':
            out.append(sub_code(src[seg_start:i]))
            q = ch
            j = i + 1
            while j < n:
                c = src[j]
                if c == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
                if c == q:
                    break
            out.append(src[i:j])
            i = j
            seg_start = i
        else:
            i += 1
    out.append(sub_code(src[seg_start:]))
    return "".join(out)

_LEGIT_GLOBALS = [
    "string", "table", "math", "os", "coroutine", "utf8", "bit32", "buffer",
    "debug", "task", "pairs", "ipairs", "next", "print", "warn", "tostring",
    "tonumber", "type", "typeof", "select", "pcall", "xpcall", "error", "assert",
    "setmetatable", "getmetatable", "rawget", "rawset", "rawequal", "rawlen",
    "unpack", "require", "tick", "wait", "spawn", "delay", "game", "workspace",
    "script", "Instance", "Vector3", "Vector2", "CFrame", "Color3", "UDim",
    "UDim2", "Enum", "Ray", "Region3", "TweenInfo", "BrickColor", "Random",
    "DateTime", "NumberRange", "NumberSequence", "ColorSequence", "Rect", "Faces",
    "_G", "_VERSION", "shared", "newproxy", "collectgarbage", "gcinfo", "loadstring",
    "load", "getfenv", "setfenv", "getconstants", "getupvalue", "getinfo", "sethook",
    "traceback", "profilebegin", "profileend", "concat", "insert", "remove", "sort",
    "find", "create", "freeze", "clear", "byte", "char", "sub", "gsub", "match",
    "format", "rep", "len", "upper", "lower", "floor", "ceil", "abs", "max", "min",
    "huge", "pi", "random", "wait", "spawn", "defer", "delay", "cancel",
]

def _fnv1a(s: str, basis: int) -> int:
    h = basis
    for ch in s.encode("utf-8"):
        x = h % 256
        h = h - x + (x ^ ch)
        lo = h % 65536
        hi = h // 65536
        h = ((hi * 16777619 % 65536) * 65536 + lo * 16777619) % 4294967296
    return h

def _enc_str_lua(s: str, k: int) -> str:
    return bytes_to_lua_string(bytes((ord(c) + k) & 0xFF for c in s))

def build_guards(opts: dict, rng) -> tuple[str, str]:
    defs = []
    conds = []

    k = rng.randrange(1, 256)
    dec_helper = f"""
local function __DARC_DEC_STR__(s)
    local t = {{}}
    for i = 1, #s do t[i] = __schar((__sbyte(s, i) - {k}) % 256) end
    return __tconc(t)
end"""
    defs.append(dec_helper)

    def enc(s: str) -> str:
        return _enc_str_lua(s, k)

    blocks = []

    if opts.get("roblox_check", True):
        roblox_def = f"""
local function __ROBLOX_OK__(E)
    local g = __rawget(E, __DARC_DEC_STR__({enc("game")})) or E.game
    local tg = E.typeof
    if not tg then return false end
    if tg(g) ~= __DARC_DEC_STR__({enc("Instance")}) then return false end
    if not E.workspace then return false end
    if not E.task then return false end
    local inst = E.Instance
    if not inst or not inst.new then return false end
    local ok, cls = __pcall(function() return g.ClassName end)
    if not ok or cls ~= __DARC_DEC_STR__({enc("DataModel")}) then return false end
    return true
end"""
        blocks.append((roblox_def, "__ROBLOX_OK__(ENV)",
                       "not a Roblox DataModel environment"))

        def d(s: str) -> str:
            return f"__DARC_DEC_STR__({enc(s)})"
        genuine_def = f"""
local function __GENUINE__(E)
    local g = __rawget(E, {d("game")}) or E.game
    if __type(g) ~= {d("userdata")} then return false end
    if __type(E[{d("workspace")}]) ~= {d("userdata")} then return false end
    if __type(__gmt(g)) == {d("table")} then return false end
    local T = E[{d("table")}]
    if __type(T) ~= {d("table")} then return false end
    if __type(T[{d("create")}]) ~= {d("function")} then return false end
    if __type(T[{d("freeze")}]) ~= {d("function")} then return false end
    if __type(E[{d("task")}]) ~= {d("table")} then return false end
    local ok, r = __pcall(function()
        local tf = E[{d("typeof")}]
        local In = E[{d("Instance")}]
        local v = E[{d("Vector3")}][{d("new")}](1, 2, 3)
        if tf(v) ~= {d("Vector3")} or v[{d("X")}] ~= 1 then return false end
        local inst = In[{d("new")}]({d("Part")})
        if __type(inst) ~= {d("userdata")} then return false end
        if tf(inst) ~= {d("Instance")} or inst[{d("ClassName")}] ~= {d("Part")} then return false end
        -- a real instance stringifies to its Name (defaults to the ClassName);
        -- a stub returns an object address / handle.
        if E[{d("tostring")}](inst) ~= {d("Part")} then return false end
        -- IsA resolves the class hierarchy and returns real booleans.
        local isa = inst[{d("IsA")}]
        if isa(inst, {d("Part")}) ~= true then return false end
        if isa(inst, {d("__no_such_class__")}) ~= false then return false end
        -- creating a bogus class errors on real Roblox; a stub happily returns one.
        if __pcall(In[{d("new")}], {d(chr(1) + chr(2) + "invalidclass" + chr(3))}) then return false end
        -- an instance metatable is protected: setmetatable must throw.
        if __pcall(__smt, inst, {{}}) then return false end
        return true
    end)
    return ok and r == true
end"""
        blocks.append((genuine_def, "__GENUINE__(ENV)",
                       "emulated / fake Roblox environment (not genuine userdata)"))

    if opts.get("anti_tamper", True):
        env_def = f"""
local function __ENV_OK__(E)
    if __gmt(E) ~= nil then return false end
    local g = __rawget(E, "_G")
    if g ~= nil and __gmt(g) ~= nil then return false end
    local gm = __rawget(E, __DARC_DEC_STR__({enc("game")})) or E.game
    if gm ~= nil and __type(__gmt(gm)) == __DARC_DEC_STR__({enc("table")}) then return false end
    if __type(__type) ~= __DARC_DEC_STR__({enc("function")}) then return false end
    if __tostr(1) ~= "1" or __tostr(true) ~= __DARC_DEC_STR__({enc("true")}) then return false end
    return true
end"""
        blocks.append((env_def, "__ENV_OK__(ENV)",
                       "environment proxy/metatable detected (env-logging?)"))

        suspects = [
            "getgenv", "getrenv", "getreg", "getgc", "getrawmetatable",
            "setrawmetatable", "hookfunction", "hookmetamethod", "newcclosure",
            "islclosure", "checkcaller", "getnamecallmethod", "getcallingscript",
            "saveinstance", "decompile", "getscriptbytecode", "dumpstring",
            "setreadonly", "make_writeable", "getfunctionhash", "iscclosure",
            "getsenv", "getinstances", "firesignal", "getconnections",
        ]
        dbg_names = ["getconstants", "getupvalues", "getproto",
                     "setconstant", "getstack", "getprotos", "setstack",
                     "getprotos", "getcode"]
        basis = rng.randrange(1, 4294967296)
        for _ in range(64):
            block = {_fnv1a(s, basis) for s in suspects + dbg_names}
            if not any(_fnv1a(g, basis) in block for g in _LEGIT_GLOBALS):
                break
            basis = rng.randrange(1, 4294967296)
        env_hashes = ", ".join(f"[{_fnv1a(s, basis)}]=1" for s in suspects)
        dbg_hashes = ", ".join(f"[{_fnv1a(s, basis)}]=1" for s in dbg_names)
        untampered_def = f"""
local function __UNTAMPERED__(E)
    local B = {{ {env_hashes} }}
    local D = {{ {dbg_hashes} }}
    local function H(s)
        local h = {basis}
        for i = 1, #s do
            local c = __sbyte(s, i)
            local x = h % 256
            local r, p, a, b = 0, 1, x, c
            for _ = 1, 8 do
                local aa, bb = a % 2, b % 2
                if aa ~= bb then r = r + p end
                a = (a - aa) / 2; b = (b - bb) / 2; p = p * 2
            end
            h = h - x + r
            local lo = h % 65536
            local hi = (h - lo) / 65536
            h = ((hi * 16777619 % 65536) * 65536 + lo * 16777619) % 4294967296
        end
        return h
    end
    local k = __next(E)
    while k ~= nil do
        if __type(k) == "string" and B[H(k)] then return false end
        k = __next(E, k)
    end
    local dbg = E.debug
    if __type(dbg) == "table" then
        local dk = __next(dbg)
        while dk ~= nil do
            if __type(dk) == "string" and D[H(dk)] then return false end
            dk = __next(dbg, dk)
        end
    end
    return true
end"""
        blocks.append((untampered_def, "__UNTAMPERED__(ENV)",
                       "executor/introspection global detected"))

        m1, m2, m3 = (rng.randint(1, 250), rng.randint(1, 250), rng.randint(1, 250))
        marker = "".join(rng.choice("abcdefghijklmnop") for _ in range(6))
        trap_def = f"""
local function __TRAP_OK__()
    local M = __DARC_DEC_STR__({enc(marker)})
    local mt = {{
        __add = function() return {m1} end,
        __index = function() return {m2} end,
        __call = function() return {m3} end,
        __concat = function() return M end,
        __eq = function() return true end,
    }}
    local t = __smt({{}}, mt)
    local u = __smt({{}}, mt)
    if t + 1 ~= {m1} then return false end
    if t.zzz ~= {m2} then return false end
    if t() ~= {m3} then return false end
    if (t .. "") ~= M then return false end
    if not (t == u) then return false end
    return true
end"""
        blocks.append((trap_def, "__TRAP_OK__()",
                       "metamethod hook / metatable tamper detected"))

        line_def = r"""
local function __LINE_OK__()
    local ok, err = __pcall(function() error("e") end)
    if ok then return false end
    local ln = __smatch(__tostr(err), ":(%d+):")
    if ln ~= nil and ln ~= "1" then return false end
    return true
end"""
        blocks.append((line_def, "__LINE_OK__()",
                       "code was reformatted / beautified"))

        probe_def = r"""
local function __DARC_PROBE__()
    local sb, sc, tc = string.byte, string.char, table.concat
    if sb("AZ", 1) ~= 65 or sb("AZ", 2) ~= 90 then return false end
    if sc(72, 105) ~= "Hi" then return false end
    if tc({ "a", "b", "c" }, "") ~= "abc" then return false end
    if ("darc"):sub(2, 3) ~= "ar" then return false end
    return true
end"""
        blocks.append((probe_def, "__DARC_PROBE__()",
                       "core library function hook detected"))

    rng.shuffle(blocks)
    for d, c, lbl in blocks:
        defs.append(d)
        conds.append((c, lbl))

    return "\n".join(defs), conds

_DECOY_SERVICES = [
    "Players", "Lighting", "RunService", "UserInputService", "ReplicatedStorage",
    "TweenService", "HttpService", "StarterGui", "SoundService", "Debris",
    "CollectionService", "TextService", "Teams", "MarketplaceService",
    "LocalizationService", "ContextActionService", "GuiService",
]
_DECOY_READS = [
    "typeof(game)", "typeof(workspace)", "tostring(tick())", "tick()",
    "game.Name", "game.ClassName", "workspace.Name", "game.PlaceId",
    "game.JobId", "os.time()",
]

def decoy_prelude(rng) -> str:
    stmts = []
    for s in rng.sample(_DECOY_SERVICES, rng.randint(5, 10)):
        stmts.append(f'pcall(function() return game:GetService("{s}") end)')
    for r in rng.sample(_DECOY_READS, rng.randint(3, 7)):
        stmts.append(f'pcall(function() return {r} end)')
    rng.shuffle(stmts)
    return "do " + " ".join(stmts) + " end\n"

def obfuscate(source: str, opts: dict | None = None) -> str:
    opts = opts or {}

    if opts.get("target") == "executor":
        opts = {**opts, "anti_tamper": False}

    seed = opts.get("seed")
    gen = NameGen(seed)
    rng = gen.rng

    anti_log = opts.get("anti_log")
    if anti_log is None:
        anti_log = opts.get("target") == "executor"
    if anti_log:
        source = decoy_prelude(rng) + source

    fusion_specs = vmgen.select_fusions(rng)
    fusion_map = {pair: spec[0] for pair, spec in fusion_specs.items()}
    fused_names = [spec[0] for spec in fusion_specs.values()]
    opmap = vmgen.build_opmap(rng, fused_names)

    proto = compile_source_to_proto(parse(source))
    shuffle_constants(proto, rng)
    apply_fusions(proto, fusion_map)
    scramble_cfg(proto, rng)
    enc = encode_program(proto, opmap, rng)

    decoder = _read_template("decoder.lua")
    vm = vmgen.render_vm(_read_template("vm.lua"), opmap, rng, fusion_specs)

    blob85 = quote_base85(ascii85_encode(enc["cipher"]))
    cipher_len = len(enc["cipher"])

    guard_defs, guard_conds = build_guards(opts, rng)
    on_fail = opts.get("on_fail", "error")
    if on_fail == "silent":
        fail_stmt = "return"
    else:
        fail_stmt = "return error(nil, 0)"

    diagnostic = opts.get("diagnostic", False)

    def guard_check() -> str:
        if not guard_conds:
            return ""
        if diagnostic:
            return "\n".join(
                f'if not ({c}) then return error("darc: guard failed - {lbl}", 0) end'
                for c, lbl in guard_conds)
        combined = " and ".join(c for c, _ in guard_conds)
        return f"if not ({combined}) then {fail_stmt} end"

    gcheck = guard_check()

    body = f"""
{decoder}
{vm}
return (function(...)
    local __ssub, __sbyte, __spack, __sgsub = string.sub, string.byte, string.pack, string.gsub
    local __schar, __gmt, __rawget, __type, __pcall, __tconc, __tostr = string.char, getmetatable, rawget, type, pcall, table.concat, tostring
    local __smt, __next, __smatch = setmetatable, next, string.match
    local function __DARC_B85__(s)
        s = __sgsub(s, "z", "!!!!!")
        return (__sgsub(s, ".....", function(g)
            local a, b, c, d, e = __sbyte(g, 1, 5)
            return __spack("<I4", (a-33)*52200625 + (b-33)*614125 + (c-33)*7225 + (d-33)*85 + (e-33))
        end))
    end
    local ENV = (getfenv and getfenv(0)) or _G
{guard_defs}
    {gcheck}
    local B = __ssub(__DARC_B85__({blob85}), 1, {cipher_len})
    local PROGRAM = __DARC_DECODE__(B, {enc['oseed']}, {enc['cseed']}, {enc['iv']}, {enc['checksum']}, {enc['plain_len']})
    {gcheck}
    local TOP = __DARC_VM__(PROGRAM, ENV)
    return TOP(...)
end)(...)
"""

    src = strip_comments(body)
    src = minify_ws(src)

    if opts.get("rename", True):
        rename_targets = [
            "__DARC_VM__", "__DARC_DECODE__", "__ROBLOX_OK__", "__UNTAMPERED__",
            "__GENUINE__", "__TRAP_OK__", "__LINE_OK__",
            "__ENV_OK__", "__DARC_DEC_STR__", "__DARC_PROBE__", "__DARC_B85__",
            "__ssub", "__sbyte", "__spack", "__sgsub",
            "__schar", "__gmt", "__rawget", "__type", "__pcall", "__tconc", "__tostr",
            "__smt", "__next", "__smatch",
            "makeClosure", "readproto",
        ]
        src = rename_identifiers(src, rename_targets, gen)

    return HEADER + " " + src + "\n"
