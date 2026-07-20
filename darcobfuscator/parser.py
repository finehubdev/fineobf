from __future__ import annotations

from . import ast_nodes as A
from .lexer import Token, tokenize

class ParseError(SyntaxError):
    pass

BINPRI = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "|": (4, 4), "~": (5, 5), "&": (6, 6),
    "..": (9, 8),
    "+": (10, 10), "-": (10, 10),
    "*": (11, 11), "/": (11, 11), "//": (11, 11), "%": (11, 11),
    "^": (14, 13),
}
UNARY_PRI = 12
COMPOUND = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "//=": "//",
            "%=": "%", "^=": "^", "..=": ".."}

class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def _next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _check(self, ttype: str, value: str | None = None) -> bool:
        t = self.cur
        if t.type != ttype:
            return False
        return value is None or t.value == value

    def _accept(self, ttype: str, value: str | None = None) -> Token | None:
        if self._check(ttype, value):
            return self._next()
        return None

    def _expect(self, ttype: str, value: str | None = None) -> Token:
        if not self._check(ttype, value):
            want = value or ttype
            raise ParseError(f"[parse] line {self.cur.line}: expected {want!r}, "
                             f"got {self.cur.value!r}")
        return self._next()

    def _is_kw(self, *words) -> bool:
        return self.cur.type == "KEYWORD" and self.cur.value in words

    def _is_sym(self, *syms) -> bool:
        return self.cur.type == "SYMBOL" and self.cur.value in syms

    def parse_chunk(self) -> A.Chunk:
        body = self._block()
        self._expect("EOF")
        return A.Chunk(body)

    def _block(self) -> list:
        stmts = []
        while not self._block_end():
            if self._is_kw("return"):
                stmts.append(self._return_stat())
                break
            s = self._statement()
            if s is not None:
                stmts.append(s)
        return stmts

    def _block_end(self) -> bool:
        if self.cur.type == "EOF":
            return True
        return self._is_kw("end", "else", "elseif", "until")

    def _skip_type(self):
        depth = 0
        while True:
            t = self.cur
            if t.type == "EOF":
                return
            if self._is_sym("(", "{", "<", "["):
                depth += 1; self._next(); continue
            if self._is_sym(")", "}", ">", "]"):
                if depth == 0:
                    return
                depth -= 1; self._next(); continue
            if depth == 0:
                if self._is_sym(",", "=", ";"):
                    return
                if self._is_kw("end", "then", "do", "else", "elseif"):
                    return
                if self.cur.type == "NAME" and self._peek_ends_type():
                    return
            self._next()

    def _peek_ends_type(self) -> bool:
        return False

    def _statement(self):
        if self._accept("SYMBOL", ";"):
            return None
        if self._is_kw("local"):
            return self._local_stat()
        if self._is_kw("if"):
            return self._if_stat()
        if self._is_kw("while"):
            return self._while_stat()
        if self._is_kw("repeat"):
            return self._repeat_stat()
        if self._is_kw("for"):
            return self._for_stat()
        if self._is_kw("do"):
            self._next()
            body = self._block()
            self._expect("KEYWORD", "end")
            return A.Do(body)
        if self._is_kw("function"):
            return self._function_stat()
        if self._is_kw("break"):
            self._next()
            return A.Break()
        if self.cur.type == "NAME" and self.cur.value == "continue" and \
                self._looks_like_continue():
            self._next()
            return A.Continue()
        if self._is_sym("::"):
            self._next(); self._expect("NAME"); self._expect("SYMBOL", "::")
            return None
        return self._expr_stat()

    def _looks_like_continue(self) -> bool:
        nxt = self.toks[self.i + 1]
        if nxt.type == "KEYWORD" and nxt.value in ("end", "until"):
            return True
        if nxt.type == "EOF":
            return True
        return not (nxt.type == "SYMBOL" and nxt.value in ("=", ".", ":", "(", "[", ","))

    def _return_stat(self):
        self._next()
        values = []
        if not self._block_end() and not self._is_sym(";"):
            values = self._expr_list()
        self._accept("SYMBOL", ";")
        return A.Return(values)

    def _local_stat(self):
        self._next()
        if self._is_kw("function"):
            self._next()
            name = self._expect("NAME").value
            func = self._function_body(is_method=False)
            func.name = name
            return A.FunctionStat(A.Name(name), func, is_local=True)
        names = [self._expect("NAME").value]
        if self._is_sym(":"):
            self._next(); self._skip_type()
        while self._accept("SYMBOL", ","):
            names.append(self._expect("NAME").value)
            if self._is_sym(":"):
                self._next(); self._skip_type()
        values = []
        if self._accept("SYMBOL", "="):
            values = self._expr_list()
        return A.LocalAssign(names, values)

    def _if_stat(self):
        self._next()
        clauses = []
        cond = self._expression()
        self._expect("KEYWORD", "then")
        body = self._block()
        clauses.append((cond, body))
        while self._is_kw("elseif"):
            self._next()
            c = self._expression()
            self._expect("KEYWORD", "then")
            b = self._block()
            clauses.append((c, b))
        if self._accept("KEYWORD", "else"):
            clauses.append((None, self._block()))
        self._expect("KEYWORD", "end")
        return A.If(clauses)

    def _while_stat(self):
        self._next()
        cond = self._expression()
        self._expect("KEYWORD", "do")
        body = self._block()
        self._expect("KEYWORD", "end")
        return A.While(cond, body)

    def _repeat_stat(self):
        self._next()
        body = self._block()
        self._expect("KEYWORD", "until")
        cond = self._expression()
        return A.Repeat(body, cond)

    def _for_stat(self):
        self._next()
        first = self._expect("NAME").value
        if self._is_sym(":"):
            self._next(); self._skip_type()
        if self._is_sym("="):
            self._next()
            start = self._expression()
            self._expect("SYMBOL", ",")
            stop = self._expression()
            step = None
            if self._accept("SYMBOL", ","):
                step = self._expression()
            self._expect("KEYWORD", "do")
            body = self._block()
            self._expect("KEYWORD", "end")
            return A.NumericFor(first, start, stop, step, body)
        names = [first]
        while self._accept("SYMBOL", ","):
            names.append(self._expect("NAME").value)
            if self._is_sym(":"):
                self._next(); self._skip_type()
        self._expect("KEYWORD", "in")
        exprs = self._expr_list()
        self._expect("KEYWORD", "do")
        body = self._block()
        self._expect("KEYWORD", "end")
        return A.GenericFor(names, exprs, body)

    def _function_stat(self):
        self._next()
        target = A.Name(self._expect("NAME").value)
        is_method = False
        while self._is_sym(".", ":"):
            sym = self._next().value
            key = self._expect("NAME").value
            target = A.Index(target, A.Str(key))
            if sym == ":":
                is_method = True
                break
        func = self._function_body(is_method=is_method)
        return A.FunctionStat(target, func, is_local=False, is_method=is_method)

    def _function_body(self, is_method: bool) -> A.Function:
        self._expect("SYMBOL", "(")
        params = []
        is_vararg = False
        if is_method:
            params.append("self")
        if not self._is_sym(")"):
            while True:
                if self._is_sym("..."):
                    self._next()
                    is_vararg = True
                    break
                params.append(self._expect("NAME").value)
                if self._is_sym(":"):
                    self._next(); self._skip_type()
                if not self._accept("SYMBOL", ","):
                    break
        self._expect("SYMBOL", ")")
        if self._is_sym(":"):
            self._next(); self._skip_type()
        body = self._block()
        self._expect("KEYWORD", "end")
        return A.Function(params, is_vararg, body)

    def _expr_stat(self):
        expr = self._suffixed_expr()
        if self._is_sym("=", ",") or self.cur.value in COMPOUND:
            if self.cur.value in COMPOUND:
                op = COMPOUND[self.cur.value]
                self._next()
                value = self._expression()
                return A.Assign([expr], [A.BinOp(op, expr, value)])
            targets = [expr]
            while self._accept("SYMBOL", ","):
                targets.append(self._suffixed_expr())
            self._expect("SYMBOL", "=")
            values = self._expr_list()
            return A.Assign(targets, values)
        if isinstance(expr, (A.Call, A.MethodCall)):
            return A.CallStat(expr)
        raise ParseError(f"[parse] line {self.cur.line}: unexpected expression statement")

    def _expr_list(self) -> list:
        exprs = [self._expression()]
        while self._accept("SYMBOL", ","):
            exprs.append(self._expression())
        return exprs

    def _expression(self, limit: int = 0):
        if self._is_kw("not") or self._is_sym("-", "#", "~"):
            op = self._next().value
            operand = self._expression(UNARY_PRI)
            left = A.UnOp(op, operand)
        else:
            left = self._simple_expr()
        while True:
            t = self.cur
            op = t.value
            if t.type == "KEYWORD" and op in ("and", "or"):
                pass
            elif t.type == "SYMBOL" and op in BINPRI:
                pass
            else:
                break
            lp, rp = BINPRI[op]
            if lp <= limit:
                break
            self._next()
            right = self._expression(rp)
            left = A.BinOp(op, left, right)
        return left

    def _simple_expr(self):
        t = self.cur
        if t.type == "NUMBER":
            self._next()
            return self._parse_number(t.value)
        if t.type == "STRING":
            self._next()
            return A.Str(t.value)
        if self._is_kw("nil"):
            self._next(); return A.Nil()
        if self._is_kw("true"):
            self._next(); return A.TrueLit()
        if self._is_kw("false"):
            self._next(); return A.FalseLit()
        if self._is_sym("..."):
            self._next(); return A.Vararg()
        if self._is_sym("{"):
            return self._table()
        if self._is_kw("function"):
            self._next()
            return self._function_body(is_method=False)
        return self._suffixed_expr()

    def _primary_expr(self):
        if self._is_sym("("):
            self._next()
            e = self._expression()
            self._expect("SYMBOL", ")")
            return A.UnOp("paren", e)
        if self.cur.type == "NAME":
            return A.Name(self._next().value)
        raise ParseError(f"[parse] line {self.cur.line}: unexpected {self.cur.value!r}")

    def _suffixed_expr(self):
        e = self._primary_expr()
        while True:
            if self._is_sym("."):
                self._next()
                key = self._expect("NAME").value
                e = A.Index(e, A.Str(key))
            elif self._is_sym("["):
                self._next()
                key = self._expression()
                self._expect("SYMBOL", "]")
                e = A.Index(e, key)
            elif self._is_sym(":"):
                self._next()
                method = self._expect("NAME").value
                args = self._call_args()
                e = A.MethodCall(e, method, args)
            elif self._is_sym("(") or self._is_sym("{") or self.cur.type == "STRING":
                args = self._call_args()
                e = A.Call(e, args)
            else:
                break
        return e

    def _call_args(self) -> list:
        if self.cur.type == "STRING":
            return [A.Str(self._next().value)]
        if self._is_sym("{"):
            return [self._table()]
        self._expect("SYMBOL", "(")
        args = []
        if not self._is_sym(")"):
            args = self._expr_list()
        self._expect("SYMBOL", ")")
        return args

    def _table(self) -> A.Table:
        self._expect("SYMBOL", "{")
        tbl = A.Table()
        while not self._is_sym("}"):
            if self._is_sym("["):
                self._next()
                key = self._expression()
                self._expect("SYMBOL", "]")
                self._expect("SYMBOL", "=")
                val = self._expression()
                tbl.hash_items.append((key, val))
            elif self.cur.type == "NAME" and self.toks[self.i + 1].type == "SYMBOL" \
                    and self.toks[self.i + 1].value == "=":
                key = A.Str(self._next().value)
                self._expect("SYMBOL", "=")
                val = self._expression()
                tbl.hash_items.append((key, val))
            else:
                tbl.array_items.append(self._expression())
            if not (self._accept("SYMBOL", ",") or self._accept("SYMBOL", ";")):
                break
        self._expect("SYMBOL", "}")
        return tbl

    def _parse_number(self, text: str) -> A.Number:
        s = text.replace("_", "")
        low = s.lower()
        try:
            if low.startswith("0x"):
                if any(c in low for c in ".p"):
                    return A.Number(float.fromhex(s), is_int=False)
                return A.Number(float(int(s, 16)), is_int=True)
            if low.startswith("0b"):
                return A.Number(float(int(s[2:], 2)), is_int=True)
            if any(c in low for c in ".e"):
                return A.Number(float(s), is_int=False)
            return A.Number(float(int(s)), is_int=True)
        except ValueError:
            return A.Number(float(s), is_int=False)

def parse(source: str) -> A.Chunk:
    return Parser(tokenize(source)).parse_chunk()
