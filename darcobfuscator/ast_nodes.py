from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Nil: pass

@dataclass
class TrueLit: pass

@dataclass
class FalseLit: pass

@dataclass
class Vararg: pass

@dataclass
class Number:
    value: float
    is_int: bool = False

@dataclass
class Str:
    value: str

@dataclass
class Name:
    name: str

@dataclass
class Index:
    obj: object
    key: object

@dataclass
class Call:
    func: object
    args: list

@dataclass
class MethodCall:
    obj: object
    method: str
    args: list

@dataclass
class Function:
    params: list
    is_vararg: bool
    body: list
    name: Optional[str] = None

@dataclass
class Table:
    array_items: list = field(default_factory=list)
    hash_items: list = field(default_factory=list)

@dataclass
class BinOp:
    op: str
    left: object
    right: object

@dataclass
class UnOp:
    op: str
    operand: object

@dataclass
class LocalAssign:
    names: list
    values: list

@dataclass
class Assign:
    targets: list
    values: list

@dataclass
class CallStat:
    call: object

@dataclass
class Do:
    body: list

@dataclass
class While:
    cond: object
    body: list

@dataclass
class Repeat:
    body: list
    cond: object

@dataclass
class If:
    clauses: list

@dataclass
class NumericFor:
    var: str
    start: object
    stop: object
    step: Optional[object]
    body: list

@dataclass
class GenericFor:
    names: list
    exprs: list
    body: list

@dataclass
class FunctionStat:
    target: object
    func: Function
    is_local: bool = False
    is_method: bool = False

@dataclass
class Return:
    values: list

@dataclass
class Break: pass

@dataclass
class Continue: pass

@dataclass
class Chunk:
    body: list
