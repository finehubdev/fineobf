"""fine env-logger — a RED-TEAM test harness for your own `fine` builds.

It runs a protected `.lua` inside a *fake but high-fidelity Roblox environment*
(userdata-typed instances, real datatype behaviour, `IsA`/`tostring`, invalid-
class errors, locked metatables, the Luau library surface) and logs every API
interaction the script's VM makes. It then writes `env_log_output.lua`, a linear
reconstruction of the observed API calls.

Outcomes:
  * **BLOCKED** — a guard (e.g. `__GENUINE__` anti-emulation) refused the fake
    environment. Your protection worked. Build the sample with `--diagnose` to
    see which guard fired.
  * **RAN** — the emulator was convincing enough to pass the guards; the trace +
    `env_log_output.lua` are what an attacker recovers. Note it recovers the
    *API-call trace*, not your control-flow/logic (that stays inside the VM).

Usage:
    ./.venv/bin/python tools/env_logger.py path/to/protected.lua
    ./.venv/bin/python tools/env_logger.py path/to/protected.lua --naive   # table stubs (blocked)
    ./.venv/bin/python tools/env_logger.py path/to/protected.lua -o out.lua # reconstruction path
"""
from __future__ import annotations

import argparse
import os
import select
import signal
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lupa import LuaRuntime


# ------------------------------------------------------------- luau backend
# `fine` builds are pure Lua and run under lupa (Lua 5.5). Luau builds (Luraph
# etc.) need a real Luau runtime — they use loadstring/buffer/bit32/getfenv and
# Luau syntax that will not even compile under Lua 5.5. If a `luau` binary is on
# PATH, run those through it inside a permissive fake-Roblox environment.
_LUAU_PRELUDE = r'''
local realG = getfenv()
local __out = {}
local function log(s) __out[#__out+1]=s; print("\1EV\1"..s) end
local function fmt(v)
  local t=type(v)
  if t=="string" then return '"'..v..'"' end
  if t=="userdata" or (t=="table" and getmetatable(v)=="LOCK") then return tostring(v) end
  if t=="table" then return "{}" end
  return tostring(v)
end
local mkobj
mkobj=function(name,class)
  return setmetatable({},{
    __index=function(_,k)
      if k=="ClassName" then return class or "Instance" end
      if k=="Name" then return class or name end
      if k=="Parent" then return nil end
      if k=="GetService" then return function(_,s) log("SVC\t"..tostring(s)); return mkobj("svc",tostring(s)) end end
      if k=="IsA" then return function(_,c) return c==(class or name) end end
      return function(...) log("CALL\t"..tostring(name)..":"..tostring(k)); return mkobj(tostring(name).."."..tostring(k)) end
    end,
    __newindex=function(_,k,v) log("SET\t"..tostring(name).."\t"..tostring(k).."\t"..fmt(v)) end,
    __call=function() return mkobj(tostring(name).."()") end,
    __tostring=function() return class or name end,
    __concat=function(a,b) return tostring(a)..tostring(b) end,
    __metatable="LOCK",
  })
end
local FAKE
FAKE=setmetatable({
  game=mkobj("game","DataModel"), workspace=mkobj("workspace","Workspace"),
  task={wait=function() end,spawn=function(f) if type(f)=="function" then pcall(f) end end,defer=function(f) if type(f)=="function" then pcall(f) end end,delay=function() end,cancel=function() end},
  Instance={new=function(c) log("NEW\t"..tostring(c)); return mkobj(tostring(c),tostring(c)) end},
  Vector3=mkobj("Vector3"),Vector2=mkobj("Vector2"),CFrame=mkobj("CFrame"),
  Color3=mkobj("Color3"),UDim2=mkobj("UDim2"),UDim=mkobj("UDim"),Enum=mkobj("Enum"),
  TweenInfo=mkobj("TweenInfo"),BrickColor=mkobj("BrickColor"),Random=mkobj("Random"),
  ColorSequence=mkobj("ColorSequence"),ColorSequenceKeypoint=mkobj("ColorSequenceKeypoint"),
  NumberSequence=mkobj("NumberSequence"),NumberRange=mkobj("NumberRange"),Ray=mkobj("Ray"),
  typeof=function(x) if type(x)=="table" and getmetatable(x)=="LOCK" then return "Instance" end return type(x) end,
  unpack=table.unpack, warn=function() end, tick=function() return 0 end, wait=function() end,
  spawn=function(f) if type(f)=="function" then pcall(f) end end, delay=function() end,
}, {__index=realG})
FAKE._G=FAKE
FAKE.getfenv=function() return FAKE end
FAKE.setfenv=function(_,e) return e or FAKE end
'''


def _pty_run(cmd, timeout):
    """Run cmd under a pty (line-buffered) and return (output, timed_out)."""
    import pty
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(cmd[0], cmd)
        except Exception:
            pass
        os._exit(127)
    buf, start, done = b"", time.time(), False
    while time.time() - start < timeout:
        try:
            r, _, _ = select.select([fd], [], [], 0.2)
        except OSError:
            break
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                done = True
                break
            buf += d
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                done = True
                break
        except OSError:
            break
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
    return buf.decode("utf-8", "replace"), (not done)


_METHODS = frozenset({
    "GetService", "FindFirstChild", "FindFirstChildOfClass", "FindFirstChildWhichIsA",
    "FindFirstAncestor", "WaitForChild", "GetChildren", "GetDescendants",
    "Destroy", "Clone", "Remove", "ClearAllChildren", "GetFullName", "Kick",
    "Connect", "ConnectParallel", "Once", "Wait", "Fire", "FireServer",
    "FireClient", "FireAllClients", "InvokeServer", "InvokeClient", "Invoke",
    "GetPropertyChangedSignal", "GetAttribute", "SetAttribute", "GetAttributes",
    "Play", "Stop", "Pause", "Resume", "Create", "TweenSize", "TweenPosition",
    "GetMouse", "GetPlayers", "IsDescendantOf",
})


class Datatype:
    """A Roblox datatype value (Vector3, Color3, …): userdata, carries a type tag,
    real fields (for anti-emulation checks) and a Lua constructor expression."""
    def __init__(self, robloxtype, expr, **fields):
        object.__setattr__(self, "robloxtype", robloxtype)
        object.__setattr__(self, "_expr", expr)
        object.__setattr__(self, "_fields", fields)

    def _get(self, k):
        if k == "robloxtype":
            return object.__getattribute__(self, "robloxtype")
        f = object.__getattribute__(self, "_fields")
        if k in f:
            return f[k]
        return Datatype("Instance", object.__getattribute__(self, "_expr") + "." + k)

    def __getattr__(self, k):
        if k.startswith("_") or k == "robloxtype":
            raise AttributeError(k)
        return object.__getattribute__(self, "_get")(k)

    def __getitem__(self, k):
        return object.__getattribute__(self, "_get")(str(k))

    def __setattr__(self, k, v):
        object.__getattribute__(self, "_fields")[k] = v

    def __str__(self):
        return object.__getattribute__(self, "_expr")

    def __call__(self, *a):
        return self


class LoggedObject:
    """A fake Roblox Instance. lupa exposes it as *userdata*. Logs property
    reads/writes and method calls, and reproduces the behaviours the anti-
    emulation guard checks (IsA booleans, tostring == Name, …)."""
    def __init__(self, logger, expr, class_name="Instance", varname=None, **attrs):
        object.__setattr__(self, "_lg", logger)
        object.__setattr__(self, "_expr", expr)
        object.__setattr__(self, "robloxtype", "Instance")
        object.__setattr__(self, "_class", class_name)
        object.__setattr__(self, "_varname", varname)
        a = {"ClassName": class_name, "Name": class_name}
        a.update(attrs)
        object.__setattr__(self, "_attrs", a)
        object.__setattr__(self, "_children", {})

    def __str__(self):
        a = object.__getattribute__(self, "_attrs")
        return str(a.get("Name") or a.get("ClassName") or "Instance")

    def IsA(self, *args):
        cls = str(args[-1])
        mine = object.__getattribute__(self, "_class")
        # lite class hierarchy so inst:IsA("BasePart"/"Instance"/"GuiObject") work
        anc = {mine, "Instance"}
        if mine in ("Part", "MeshPart", "WedgePart"):
            anc |= {"BasePart", "PVInstance"}
        if mine in ("Frame", "TextLabel", "TextButton", "TextBox", "ImageLabel", "ScrollingFrame"):
            anc |= {"GuiObject", "GuiBase2d"}
        return cls in anc

    def _get(self, key, log=True):
        if key == "robloxtype":
            return object.__getattribute__(self, "robloxtype")
        if key == "IsA":       # real-boolean method (lupa routes '.' via __getitem__)
            return object.__getattribute__(self, "IsA")
        attrs = object.__getattribute__(self, "_attrs")
        if key in attrs:
            return attrs[key]
        if key in _METHODS:
            return object.__getattribute__(self, "_mk_method")(key)
        lg = object.__getattribute__(self, "_lg")
        expr = object.__getattribute__(self, "_expr")
        children = object.__getattribute__(self, "_children")
        if key not in children:
            children[key] = LoggedObject(lg, f"{expr}.{key}")
        if log:
            lg.read(self, key)
        return children[key]

    def _mk_method(self, key):
        lg = object.__getattribute__(self, "_lg")

        def method(*args):
            real = args[1:] if args and args[0] is self else args
            if key == "GetService" and real:
                return lg.service(str(real[0]))
            lg.call(self, key, real)
            if key in ("Connect", "ConnectParallel", "Once"):
                return LoggedObject(lg, "connection", "RBXScriptConnection")
            return LoggedObject(lg, "result")
        return method

    def __getattr__(self, key):
        if key.startswith("_") or key == "robloxtype":
            raise AttributeError(key)
        return object.__getattribute__(self, "_get")(key)

    def __getitem__(self, key):
        return object.__getattribute__(self, "_get")(str(key))

    def __setattr__(self, key, val):
        lg = object.__getattribute__(self, "_lg")
        object.__getattribute__(self, "_attrs")[key] = val
        lg.setprop(self, key, val)

    def __setitem__(self, key, val):
        object.__getattribute__(self, "__setattr__")(str(key), val)

    def __call__(self, *args):
        lg = object.__getattribute__(self, "_lg")
        lg.call(self, None, args)
        return LoggedObject(lg, "result")


def _lit(v, logger):
    """Format a value as a Lua expression for the reconstruction."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (int, float)):
        n = float(v)
        return str(int(n)) if n.is_integer() else repr(v)
    if isinstance(v, LoggedObject):
        return logger.varname(v)
    if isinstance(v, Datatype):
        return object.__getattribute__(v, "_expr")
    return "nil --[[?]]"


class EnvLogger:
    def __init__(self, naive=False, timeout=8):
        self.naive = naive
        self.timeout = timeout
        self.backend = "lupa"
        self.events = []          # (kind, obj, a, b)   [lupa backend]
        self.luau_events = []     # (kind, str, str, str) [luau backend]
        self.script_prints = []   # stdout the script emitted
        self.services = {}
        self._vars = {}           # id(obj) -> varname
        self._counts = {}
        self.lua = LuaRuntime(unpack_returned_tuples=True)

    # -- event sinks --
    def read(self, obj, key):
        self.events.append(("read", obj, key, None))

    def setprop(self, obj, key, val):
        self.events.append(("set", obj, key, val))

    def call(self, obj, method, args):
        self.events.append(("call", obj, method, list(args)))

    def service(self, name):
        if name not in self.services:
            svc = LoggedObject(self, f'game:GetService("{name}")', name, varname=name)
            self.services[name] = svc
            self.events.append(("service", svc, name, None))
        return self.services[name]

    # -- variable naming for the reconstruction --
    def varname(self, obj):
        vid = id(obj)
        if vid in self._vars:
            return self._vars[vid]
        base = object.__getattribute__(obj, "_varname") \
            or object.__getattribute__(obj, "_class") or "obj"
        base = "".join(c if (c.isalnum() or c == "_") else "_" for c in base)
        self._counts[base] = self._counts.get(base, 0) + 1
        name = base if self._counts[base] == 1 else f"{base}_{self._counts[base]}"
        self._vars[vid] = name
        return name

    # -- Python factories --
    def _mk_instance(self, class_name=None, parent=None):
        cn = str(class_name) if class_name is not None else "Instance"
        if not cn or any(ord(c) < 32 for c in cn):     # real Roblox errors here
            raise ValueError("Unable to create an Instance of type " + repr(cn))
        inst = LoggedObject(self, f'Instance.new("{cn}")', cn, varname=cn)
        self.events.append(("new", inst, cn, None))
        if parent is not None:
            inst.Parent = parent
        return inst

    def _mk_vector3(self, x=0, y=0, z=0):
        return Datatype("Vector3", f"Vector3.new({_lit(x, self)}, {_lit(y, self)}, {_lit(z, self)})",
                        X=float(x or 0), Y=float(y or 0), Z=float(z or 0))

    def _mk_color3_rgb(self, r=0, g=0, b=0):
        return Datatype("Color3", f"Color3.fromRGB({_lit(r, self)}, {_lit(g, self)}, {_lit(b, self)})",
                        R=(r or 0) / 255.0, G=(g or 0) / 255.0, B=(b or 0) / 255.0)

    def _generic(self, name):
        def ctor(*a):
            return Datatype(name, f"{name}.new({', '.join(_lit(x, self) for x in a)})")
        return ctor

    def build_env(self):
        lua = self.lua
        g = lua.globals()
        if self.naive:
            return lua.execute(r"""
                local E = {}
                for k, v in pairs(_G) do E[k] = v end
                E.game = setmetatable({}, { __index = function(_, k)
                    if k == "ClassName" then return "DataModel" end
                    if k == "GetService" then return function() return {} end end
                end, __metatable = "The metatable is locked" })
                E.workspace = { Name = "Workspace" }
                E.task = { wait=function() end, spawn=function() end }
                E.Instance = { new = function() return {} end }
                E.typeof = function(x) return type(x) end
                return E
            """)
        g["__UD_game"] = LoggedObject(self, "game", "DataModel", varname="game", Name="Game",
                                      PlaceId=0, JobId="")
        g["__UD_ws"] = LoggedObject(self, "workspace", "Workspace", varname="workspace",
                                    Name="Workspace")
        g["__mkInstance"] = self._mk_instance
        g["__mkVector3"] = self._mk_vector3
        g["__mkColor3rgb"] = self._mk_color3_rgb
        g["__mkUDim2"] = self._generic("UDim2")
        g["__mkUDim"] = self._generic("UDim")
        g["__mkCFrame"] = self._generic("CFrame")
        g["__mkV2"] = self._generic("Vector2")
        return lua.execute(r"""
            local realmt = getmetatable
            getmetatable = function(x)
                if type(x) == "userdata" then return "The metatable is locked" end
                return realmt(x)
            end
            local E = {}
            for k, v in pairs(_G) do E[k] = v end
            E.game = __UD_game
            E.workspace = __UD_ws
            E.task = { wait=function() end, spawn=function(f) if f then pcall(f) end end,
                       defer=function(f) if f then pcall(f) end end, delay=function() end }
            E.typeof = function(x)
                if type(x) == "userdata" then return x.robloxtype end
                return type(x)
            end
            E.Instance = { new = function(c, p) return __mkInstance(c, p) end }
            E.Vector3 = { new = function(x,y,z) return __mkVector3(x,y,z) end }
            E.Vector2 = { new = function(...) return __mkV2(...) end }
            E.Color3 = { fromRGB = function(r,g,b) return __mkColor3rgb(r,g,b) end,
                         new = function(r,g,b) return __mkColor3rgb((r or 0)*255,(g or 0)*255,(b or 0)*255) end }
            E.UDim2 = { new = function(...) return __mkUDim2(...) end }
            E.UDim  = { new = function(...) return __mkUDim(...) end }
            E.CFrame = { new = function(...) return __mkCFrame(...) end }
            E.table = { create = table.create, freeze = function(t) return t end,
                        clear = function(t) for k in pairs(t) do t[k]=nil end end,
                        concat = table.concat, unpack = table.unpack, insert = table.insert,
                        remove = table.remove, sort = table.sort, find = table.find }
            E.getfenv = function() return E end
            E.setfenv = function() return E end
            return E
        """)

    def run(self, path):
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        # Luau builds (Luraph, …) won't compile under Lua 5.5 — use the luau CLI.
        compiles = self.lua.eval("function(s) return (load(s)) ~= nil end")(src)
        if not compiles:
            self.backend = "luau"
            return self._run_luau(src)
        self.backend = "lupa"
        env = self.build_env()
        self.lua.globals()["__FINE_ENV__"] = env
        self.lua.execute("getfenv = function() return __FINE_ENV__ end")
        try:
            self.lua.execute(src)
            return {"err": None, "timed_out": False}
        except Exception as e:
            return {"err": str(e), "timed_out": False}

    def _run_luau(self, src):
        import tempfile
        luau = shutil.which("luau")
        if not luau:
            return {"err": "LUAU_MISSING", "timed_out": False}
        body = src.split("\n", 1)[1].lstrip() if "\n" in src else src
        if body.startswith("return "):
            body = body[len("return "):]
        n = 1
        while ("]" + "=" * n + "]") in body:
            n += 1
        lb, rb = "[" + "=" * n + "[", "]" + "=" * n + "]"
        runner = (_LUAU_PRELUDE + f"\nlocal BODY = {lb}\n{body}\n{rb}\n"
                  'local f,e=loadstring(BODY,"sample")\n'
                  'if not f then print("\1CE\1"..tostring(e)) else\n'
                  '  setfenv(f, FAKE)\n'
                  '  local ok,err=pcall(f)\n'
                  '  if not ok then print("\1RE\1"..tostring(err)) end\n'
                  'end\n')
        tf = tempfile.NamedTemporaryFile("w", suffix=".luau", delete=False)
        tf.write(runner)
        tf.close()
        try:
            out, timed_out = _pty_run([luau, tf.name], self.timeout)
        finally:
            os.unlink(tf.name)
        err = None
        for line in out.replace("\r", "").split("\n"):
            if "\1EV\1" in line:
                self._parse_luau_event(line.split("\1EV\1", 1)[1])
            elif "\1CE\1" in line:
                err = "decoded payload failed to compile: " + line.split("\1CE\1", 1)[1][:120]
            elif "\1RE\1" in line:
                err = "runtime error: " + line.split("\1RE\1", 1)[1][:120]
            elif line.strip():
                self.script_prints.append(line)
        return {"err": err, "timed_out": timed_out}

    def _parse_luau_event(self, s):
        parts = s.split("\t")
        k = parts[0]
        if k == "SVC":
            self.luau_events.append(("service", parts[1]))
            self.services[parts[1]] = True
        elif k == "NEW":
            self.luau_events.append(("new", parts[1]))
        elif k == "SET" and len(parts) >= 4:
            self.luau_events.append(("set", parts[1], parts[2], parts[3]))
        elif k == "CALL":
            self.luau_events.append(("call", parts[1]))

    def reconstruct_luau(self):
        lines = ["-- Reconstructed by fine env-logger (luau backend).", ""]
        for e in self.luau_events:
            if e[0] == "service":
                lines.append(f'local {e[1]} = game:GetService("{e[1]}")')
            elif e[0] == "new":
                lines.append(f'Instance.new("{e[1]}")')
            elif e[0] == "set":
                lines.append(f"{e[1]}.{e[2]} = {e[3]}")
            elif e[0] == "call":
                lines.append(f"{e[1]}()")
        return "\n".join(lines) + "\n"

    # -- reconstruction --
    def reconstruct(self):
        lines = [
            "-- Reconstructed by fine env-logger from the observed API trace.",
            "-- NOTE: this is a LINEAR REPLAY of API calls, not the original",
            "--       control flow/logic (that never leaves the VM).",
            "",
        ]
        emitted_service = set()
        for kind, obj, a, b in self.events:
            if kind == "service":
                if a not in emitted_service:
                    emitted_service.add(a)
                    lines.append(f'local {self.varname(obj)} = game:GetService("{a}")')
            elif kind == "new":
                lines.append(f'local {self.varname(obj)} = Instance.new("{a}")')
            elif kind == "set":
                lines.append(f"{self.varname(obj)}.{a} = {_lit(b, self)}")
            elif kind == "call" and a is not None:
                args = ", ".join(_lit(x, self) for x in b)
                lines.append(f"{self.varname(obj)}:{a}({args})")
        return "\n".join(lines) + "\n"


def _is_guard_probe(kind, obj, a, b):
    """Events made by the anti-emulation guard's own probing, not the script."""
    if kind == "new":
        return a == "Part" or any(ord(c) < 32 for c in str(a))
    if kind == "call" and a in ("IsA",):
        return True
    if kind == "read" and a == "robloxtype":
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="env_logger",
                                 description="Red-team env logger for fine builds.")
    ap.add_argument("file", help="protected .lua file to run")
    ap.add_argument("--naive", action="store_true",
                    help="use plain-table stubs (should be blocked by anti-emulation)")
    ap.add_argument("-o", "--output", default="env_log_output.lua",
                    help="reconstruction output path (default: env_log_output.lua)")
    args = ap.parse_args(argv)

    lg = EnvLogger(naive=args.naive)
    res = lg.run(args.file)
    err, timed_out = res["err"], res["timed_out"]

    print(f"== fine env-logger : {os.path.basename(args.file)} "
          f"[{lg.backend} backend] ==\n")

    # ---- Luau backend (Luraph etc.) ----
    if lg.backend == "luau":
        if err == "LUAU_MISSING":
            print("  This is a Luau build (does not compile under Lua 5.5) and no")
            print("  `luau` binary is on PATH. Install one:  brew install luau")
            return 0
        if lg.script_prints:
            print("  -- script stdout --")
            for p in lg.script_prints[:15]:
                print("    " + p)
            print()
        print(f"  captured {len(lg.luau_events)} API interaction(s) via the luau runtime.")
        for e in lg.luau_events[:40]:
            print("    " + (f'game:GetService("{e[1]}")' if e[0] == "service" else
                            f'Instance.new("{e[1]}")' if e[0] == "new" else
                            f"{e[1]}.{e[2]} = {e[3]}" if e[0] == "set" else f"{e[1]}()"))
        if lg.luau_events:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(lg.reconstruct_luau())
            print(f"\n  reconstruction written to: {args.output}")
        if timed_out:
            print("\n  RESULT: HUNG — the payload ran but did not terminate in "
                  f"{lg.timeout}s.\n          Its VM/anti-tamper resists the fake "
                  "environment (event loop or crash-loop). This build resists\n"
                  "          env-logging more than a plain fake env can handle.")
        elif err:
            print(f"\n  RESULT: STOPPED — {err}")
            print("          (it reached a real Roblox API the fake env doesn't fully model.)")
        else:
            print("\n  RESULT: RAN to completion.")
        return 0

    # ---- lupa backend (fine builds) ----
    real = [e for e in lg.events if not _is_guard_probe(*e)]

    if err and not real:
        detail = next((l for l in err.splitlines() if "darc:" in l), None)
        print("  RESULT: BLOCKED by anti-tamper — the environment was refused.")
        if detail:
            print(f"          reason: {detail.split('darc:')[-1].strip()}")
        else:
            print("          (no message — rebuild the sample with --diagnose for the reason)")
        return 0

    lg.events = real
    print(f"  RESULT: RAN — anti-tamper bypassed; captured {len(real)} API interaction(s).")
    if err:
        print(f"          (script raised afterwards: {err.strip().splitlines()[-1][:70]})")
    print("\n  -- API trace --")
    for kind, obj, a, bb in real:
        if kind == "service":
            print(f'    [CALL] game:GetService("{a}")')
        elif kind == "new":
            print(f'    [NEW ] Instance.new("{a}")')
        elif kind == "set":
            print(f"    [SET ] {lg.varname(obj)}.{a} = {_lit(bb, lg)}")
        elif kind == "call":
            print(f"    [CALL] {lg.varname(obj)}:{a}({', '.join(_lit(x, lg) for x in bb)})")
        elif kind == "read":
            print(f"    [READ] {lg.varname(obj)}.{a}")

    recon = lg.reconstruct()
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(recon)
    services = sorted(lg.services)
    if services:
        print(f"\n  services used: {', '.join(services)}")
    print(f"\n  reconstruction written to: {args.output}")
    print("  (linear API replay — the control-flow/logic stays inside the VM.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
