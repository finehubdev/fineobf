"""Run the FULL obfuscated output (encrypted blob + decoder + VM + guards)
through lupa, using a mock Roblox environment so the guards pass."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lupa import LuaRuntime
from darcobfuscator.obfuscator import obfuscate


class _UD:
    """A Python object -> lupa exposes it to Lua as *userdata*, the way genuine
    Roblox instances/datatypes appear. Carries a Roblox type tag + fields and
    reproduces the instance behaviours the anti-emulation guard checks."""
    def __init__(self, robloxtype, **kw):
        self.robloxtype = robloxtype
        for k, v in kw.items():
            setattr(self, k, v)

    def __str__(self):                       # tostring(inst) -> its Name (== ClassName)
        return getattr(self, "Name", None) or getattr(self, "ClassName", self.robloxtype)

    def IsA(self, *args):                    # inst:IsA(class) -> real boolean
        return args[-1] == getattr(self, "ClassName", None)


def _mk_instance(class_name):
    cn = str(class_name)
    # real Roblox errors on an unknown class; our fixture rejects clearly-bogus
    # names (control characters) the way a genuine environment would.
    if not cn or any(ord(c) < 32 for c in cn):
        raise ValueError("Unable to create an Instance of type " + repr(cn))
    return _UD("Instance", ClassName=cn)


# A GENUINE Roblox environment: game/workspace/instances/datatypes are userdata,
# instance metatables read back locked, and the Luau library surface is present.
# This is what the anti-emulation guard (__GENUINE__) must accept. A hand-built
# fake environment (plain Lua tables) cannot reproduce it — see FAKE_ENV.
_GENUINE_LUA = r"""
local realmt = getmetatable
getmetatable = function(x)      -- real Roblox locks instance metatables
    if type(x) == "userdata" then return "The metatable is locked" end
    return realmt(x)
end
local E = {}
for k, v in pairs(_G) do E[k] = v end
E.game = __UD_game
E.workspace = __UD_ws
E.task = { wait = function() end, spawn = function() end,
           defer = function() end, delay = function() end }
E.typeof = function(x)
    if type(x) == "userdata" then return x.robloxtype end
    return type(x)
end
E.Vector3 = { new = function(x, y, z) return __mkVec(x, y, z) end }
E.Instance = { new = function(cls) return __mkInst(cls) end }
E.table = { create = table.create, freeze = function(t) return t end,
            concat = table.concat, unpack = table.unpack, insert = table.insert,
            remove = table.remove, sort = table.sort, find = table.find }
return E
"""

# A FAKE Roblox environment: shallow table stubs that satisfy the naive checks
# (typeof(game)=='Instance', ClassName=='DataModel') but are plain tables, not
# userdata — exactly what an env-logging deobfuscator builds. Must be REFUSED.
FAKE_ENV = r"""
local E = {}
for k, v in pairs(_G) do E[k] = v end
local gameObj = setmetatable({}, {
    __index = function(_, k)
        if k == "ClassName" then return "DataModel" end
        if k == "GetService" then return function() return {} end end
    end,
    __metatable = "The metatable is locked",
})
E.game = gameObj
E.workspace = { Name = "Workspace" }
E.task = { wait = function() end, spawn = function() end }
E.Instance = { new = function() return {} end }
E.typeof = function(x) if x == gameObj then return "Instance" end return type(x) end
return E
"""


def make_genuine(lua):
    lua.globals()["__UD_game"] = _UD("Instance", ClassName="DataModel", Name="Game")
    lua.globals()["__UD_ws"] = _UD("Instance", ClassName="Workspace", Name="Workspace")
    lua.globals()["__mkVec"] = lambda x, y, z: _UD("Vector3", X=x, Y=y, Z=z)
    lua.globals()["__mkInst"] = _mk_instance
    return lua.execute(_GENUINE_LUA)


def run_obf(source, opts=None):
    """Run a build against a GENUINE (userdata) env with the full guard stack."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    env = make_genuine(lua)
    out = obfuscate(source, opts or {})
    lua.globals()["__DARC_TEST_ENV__"] = env
    patched = out.replace("(getfenv and getfenv(0)) or _G", "__DARC_TEST_ENV__")
    return lua.execute(patched)


def check(source, expected, opts=None):
    try:
        got = run_obf(source, opts)
    except Exception as e:
        print(f"  [ERR] {source[:50]!r}: {e}")
        return False
    if isinstance(expected, tuple):
        got = tuple(got) if not isinstance(got, tuple) else got
    ok = got == expected
    print(f"  [{'OK' if ok else 'FAIL'}] {source[:50]!r} -> {got!r}"
          + ("" if ok else f" (expected {expected!r})"))
    return ok


def main():
    results = []
    results.append(check("return 2 + 3 * 4", 14))
    results.append(check("local function f(n) if n<2 then return n end return f(n-1)+f(n-2) end return f(12)", 144))
    results.append(check('return string.rep("ab", 3)', "ababab"))
    results.append(check("local t={}; for i=1,5 do t[i]=i*i end; local s=0; for _,v in ipairs(t) do s=s+v end; return s", 55))
    results.append(check("local c=0; local f=function() c=c+1; return c end; f(); f(); return f()", 3))
    # fusion / superoperator coverage (KADD/KINDEX/LADD/LINDEX/LLT + int-const)
    results.append(check("local x=10 return x + 5", 15))
    results.append(check('local t={field=42} return t.field', 42))
    results.append(check("local a,b,c=1,2,3 return a + b + c", 6))
    results.append(check("local t={11,22,33} local i=2 return t[i]", 22))
    results.append(check("local s=0 for i=1,10 do if i>5 then break end s=s+i end return s", 15))
    results.append(check('return 1 .. 2', "12"))   # integer-const stringification
    # control-flow scrambling stress: deeply nested loops + break/continue/goto-like flow
    results.append(check("local s=0 for i=1,4 do for j=1,4 do if j==2 then continue end if i*j>9 then break end s=s+i*j end end return s", 40))
    results.append(check("local function f(n) local s=0 local i=0 while true do i=i+1 if i>n then break end if i%3==0 then continue end s=s+i end return s end return f(10)", 37))
    # compression stress: highly repetitive constants/strings (#str=21 -> 21*10+10)
    results.append(check('local t={} for i=1,10 do t[i]="repeated_string_value" end return #t[1]*10 + #t', 220))
    # constant-pool shuffle: many distinct constants
    results.append(check('return 1+2+3+4+5+6+7+8+9+10 .. "-" .. "abc"', "55-abc"))

    # ---- guard tests: each hostile environment must be REFUSED ----
    print("\n  -- guard rejection tests --")

    def expect_refused(label, env_factory, opts=None):
        lua = LuaRuntime(unpack_returned_tuples=True)
        env = env_factory(lua)
        lua.globals()["__DARC_TEST_ENV__"] = env
        out = obfuscate("return 42", opts or {})
        patched = out.replace("(getfenv and getfenv(0)) or _G", "__DARC_TEST_ENV__")
        try:
            r = lua.execute(patched)
            print(f"  [FAIL] {label} was NOT refused, got {r!r}")
            results.append(False)
        except Exception:
            print(f"  [OK] {label} refused")
            results.append(True)

    # fake/emulated Roblox env (plain-table stubs) -> __GENUINE__ anti-emulation
    expect_refused("fake/emulated Roblox env (env-logging sandbox)",
                   lambda lua: lua.execute(FAKE_ENV))
    # executor global present -> __UNTAMPERED__
    def genuine_with_getgenv(lua):
        env = make_genuine(lua)
        env["getgenv"] = lua.eval("function() end")
        return env
    expect_refused("executor global (getgenv) present", genuine_with_getgenv)
    # env-logging proxy over a genuine env -> __ENV_OK__ (metatable trap)
    def logging_proxy(lua):
        base = make_genuine(lua)
        lua.globals()["__DARC_BASE_ENV__"] = base
        return lua.execute(r"""
            local base, log = __DARC_BASE_ENV__, {}
            return setmetatable({}, {
                __index = function(_, k) log[#log+1] = k; return base[k] end,
                __newindex = function(_, k, v) base[k] = v end,
            })""")
    expect_refused("env-logging proxy (metatable trap)", logging_proxy)
    # non-Roblox env -> __ROBLOX_OK__ / __GENUINE__
    expect_refused("non-Roblox env", lambda lua: lua.globals())
    # debug introspection extension present -> __UNTAMPERED__ hash scan of debug
    def genuine_with_dbg(lua):
        env = make_genuine(lua)
        env["debug"] = lua.eval('{ getconstants = function() end, traceback = function() end }')
        return env
    expect_refused("debug.getconstants (introspection) present", genuine_with_dbg)

    # anti-beautify: a reformatted (multi-line) build must refuse on a genuine env
    print("\n  -- anti-beautify --")
    lua = LuaRuntime(unpack_returned_tuples=True)
    env = make_genuine(lua)
    lua.globals()["__DARC_TEST_ENV__"] = env
    out = obfuscate("return 42", {}).replace("(getfenv and getfenv(0)) or _G", "__DARC_TEST_ENV__")
    beaut = out.replace("; ", ";\n").replace(" end ", " end\n").replace(") ", ")\n")
    try:
        r = lua.execute(beaut)
        print(f"  [FAIL] beautified build was NOT refused, got {r!r}")
        results.append(False)
    except Exception:
        print("  [OK] beautified build refused")
        results.append(True)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} obfuscated checks passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
