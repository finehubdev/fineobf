from __future__ import annotations

from dataclasses import dataclass, field

OPCODES = [
    "LOADK",
    "LOADNIL",
    "LOADTRUE",
    "LOADFALSE",
    "GETLOCAL",
    "SETLOCAL",
    "NEWLOCAL",
    "GETUPVAL",
    "SETUPVAL",
    "GETGLOBAL",
    "SETGLOBAL",
    "NEWTABLE",
    "GETINDEX",
    "SETINDEX",
    "SETFIELD",
    "SETARRAYIDX",
    "APPENDLIST",
    "ADD", "SUB", "MUL", "DIV", "IDIV", "MOD", "POW", "CONCAT",
    "EQ", "NE", "LT", "LE", "GT", "GE",
    "BAND", "BOR", "BXOR", "SHL", "SHR",
    "UNM", "NOT", "LEN", "BNOT",
    "DUP", "POP", "SWAP", "ROT3",
    "JMP", "JMPIFFALSE", "JMPIFTRUE", "JMPIFNIL",
    "NEWARR", "ARRPUSH", "ARREXTEND", "EXPAND",
    "CALL", "CALLM", "CALLVOID", "RETURN", "CLOSURE",
    "VARARG1", "VARARGM",
    "FORTEST", "ISNIL",
]

OP = {name: i + 1 for i, name in enumerate(OPCODES)}

class OpcodeMap:

    def __init__(self, name_to_code: dict[str, int]):
        self.name_to_code = dict(name_to_code)

    def __getitem__(self, name: str) -> int:
        return self.name_to_code[name]

    @classmethod
    def canonical(cls) -> "OpcodeMap":
        return cls(OP)

    @classmethod
    def randomized(cls, rng, extra_names=()) -> "OpcodeMap":
        names = list(OPCODES) + list(extra_names)
        codes = list(range(1, len(names) + 1))
        rng.shuffle(codes)
        return cls({name: codes[i] for i, name in enumerate(names)})

BINOP_TO_OP = {
    "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "//": "IDIV",
    "%": "MOD", "^": "POW", "..": "CONCAT",
    "==": "EQ", "~=": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE",
    "&": "BAND", "|": "BOR", "~": "BXOR",
}

@dataclass
class Instr:
    op: str
    a: int = 0
    b: int = 0
    nargs: int = 0
    label: str | None = None

    def code(self) -> int:
        return OP[self.op]

@dataclass
class Proto:
    numparams: int = 0
    is_vararg: bool = False
    consts: list = field(default_factory=list)
    code: list[Instr] = field(default_factory=list)
    protos: list["Proto"] = field(default_factory=list)
    upvals: list = field(default_factory=list)
    name: str = "?"

    def const_index(self, value) -> int:
        for i, c in enumerate(self.consts):
            if type(c) is type(value) and c == value:
                return i + 1
        self.consts.append(value)
        return len(self.consts)
