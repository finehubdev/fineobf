"""End-to-end correctness harness: compile Lua -> bytecode -> VM, run under lupa,
compare against expectations. Uses the reference (unobfuscated) VM build."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lupa import LuaRuntime

from darcobfuscator.parser import parse
from darcobfuscator.bytecode import OpcodeMap
from darcobfuscator.compiler import compile_source_to_proto
from darcobfuscator.serialize import proto_to_lua
from darcobfuscator import vmgen

HERE = os.path.dirname(os.path.abspath(__file__))
VM_PATH = os.path.join(HERE, "..", "darcobfuscator", "templates", "vm.lua")


def build_reference(source: str) -> str:
    with open(VM_PATH) as f:
        vm_template = f.read()
    # canonical opcode numbering, deterministic dispatch order, no fusion:
    # exactly the base interpreter, so this exercises the shipped handler bodies.
    opmap = OpcodeMap.canonical()
    vm = vmgen.render_vm(vm_template, opmap, None)
    proto = compile_source_to_proto(parse(source))
    program = proto_to_lua(proto, opmap)
    return f"{vm}\nreturn function(ENV) return __DARC_VM__({program}, ENV) end"


def run(source: str):
    lua = LuaRuntime(unpack_returned_tuples=True)
    code = build_reference(source)
    factory = lua.execute(code)
    top = factory(lua.globals())
    return top()


def run_native(source: str, lua):
    return lua.execute(source)


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("arithmetic & precedence")
def _(assert_eq):
    assert_eq("return 2 + 3 * 4 - 1", 13)
    assert_eq("return 2 ^ 3 ^ 2", 512)          # right assoc
    assert_eq("return 10 % 3", 1)
    assert_eq("return -2 ^ 2", -4)              # unary binds looser than ^
    assert_eq("return 7 // 2", 3)


@case("strings & concat")
def _(assert_eq):
    assert_eq('return "a" .. "b" .. "c"', "abc")
    assert_eq('return #"hello"', 5)
    assert_eq('return 1 .. 2', "12")


@case("locals & scope")
def _(assert_eq):
    assert_eq("local x = 5; local y = x * 2; return y", 10)
    assert_eq("local x = 1; do local x = 2 end; return x", 1)
    assert_eq("local a, b = 1, 2; a, b = b, a; return a, b", (2, 1))


@case("conditionals")
def _(assert_eq):
    assert_eq("if 1 < 2 then return 'y' else return 'n' end", "y")
    assert_eq("local x = 5; if x > 10 then return 'a' elseif x > 3 then return 'b' else return 'c' end", "b")
    assert_eq("return 1 and 2", 2)
    assert_eq("return nil and 2", None)
    assert_eq("return false or 'x'", "x")
    assert_eq("return nil or false or 7", 7)


@case("while / numeric for / break / continue")
def _(assert_eq):
    assert_eq("local s = 0; local i = 1; while i <= 5 do s = s + i; i = i + 1 end; return s", 15)
    assert_eq("local s = 0; for i = 1, 10 do s = s + i end; return s", 55)
    assert_eq("local s = 0; for i = 10, 1, -1 do s = s + i end; return s", 55)
    assert_eq("local s = 0; for i = 1, 10 do if i > 5 then break end; s = s + i end; return s", 15)
    assert_eq("local s = 0; for i = 1, 10 do if i % 2 == 0 then continue end; s = s + i end; return s", 25)


@case("repeat")
def _(assert_eq):
    assert_eq("local i = 0; repeat i = i + 1 until i >= 5; return i", 5)


@case("tables")
def _(assert_eq):
    assert_eq("local t = {10, 20, 30}; return t[1] + t[2] + t[3]", 60)
    assert_eq("local t = {}; t.x = 5; t['y'] = 7; return t.x + t.y", 12)
    assert_eq("local t = {a = 1, 100, 200, b = 2}; return t[1] + t[2] + t.a + t.b", 303)
    assert_eq("local t = {1,2,3}; return #t", 3)


@case("functions & closures")
def _(assert_eq):
    assert_eq("local function f(a, b) return a + b end; return f(3, 4)", 7)
    assert_eq("local function fib(n) if n < 2 then return n end return fib(n-1)+fib(n-2) end; return fib(10)", 55)
    assert_eq("local function counter() local c = 0; return function() c = c + 1; return c end end local n = counter(); n(); n(); return n()", 3)
    assert_eq("local fns = {}; for i=1,3 do fns[i] = function() return i end end; return fns[1]()+fns[2]()+fns[3]()", 6)


@case("varargs & multiple returns")
def _(assert_eq):
    assert_eq("local function f(...) return ... end; return f(1,2,3)", (1, 2, 3))
    assert_eq("local function f(...) local a,b = ...; return a+b end; return f(10,20,30)", 30)
    assert_eq("local function two() return 1, 2 end; local a, b = two(); return a, b", (1, 2))
    assert_eq("local function two() return 1, 2 end; local t = {two(), two()}; return #t", 3)
    assert_eq("local function sum(...) local s=0; for _,v in ipairs({...}) do s=s+v end; return s end; return sum(1,2,3,4)", 10)


@case("methods & generic for")
def _(assert_eq):
    assert_eq("local o = {v = 10}; function o:get() return self.v end; return o:get()", 10)
    assert_eq("local t = {a=1, b=2, c=3}; local s = 0; for k, v in pairs(t) do s = s + v end; return s", 6)
    assert_eq("local t = {5,6,7}; local s=0; for i,v in ipairs(t) do s = s + v end; return s", 18)


@case("stdlib calls")
def _(assert_eq):
    assert_eq('return string.upper("hi")', "HI")
    assert_eq('return string.format("%d-%s", 5, "x")', "5-x")
    assert_eq('return math.max(3, 9, 2)', 9)
    assert_eq('return tostring(42)', "42")
    assert_eq('return tonumber("15") + 5', 20)
    assert_eq('local t = {3,1,2}; table.sort(t); return t[1], t[2], t[3]', (1, 2, 3))


def main():
    lua = LuaRuntime(unpack_returned_tuples=True)
    passed = 0
    failed = 0
    for name, fn in CASES:
        def assert_eq(src, expected):
            nonlocal passed, failed
            try:
                got = run(src)
            except Exception as e:
                failed += 1
                print(f"  [ERR] {name}: {src[:60]!r}\n        raised {e}")
                return
            # normalize tuples
            if isinstance(expected, tuple):
                got_t = tuple(got) if not isinstance(got, tuple) else got
                ok = got_t == expected
            else:
                ok = got == expected
            if ok:
                passed += 1
            else:
                failed += 1
                print(f"  [FAIL] {name}: {src[:60]!r}\n         expected {expected!r}, got {got!r}")
        fn(assert_eq)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
