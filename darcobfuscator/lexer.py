from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
}

SYMBOLS = [
    "...", "..=", "//=", "+=", "-=", "*=", "/=", "%=", "^=", "..",
    "==", "~=", "<=", ">=", "::", "//",
    "+", "-", "*", "/", "%", "^", "#", "<", ">", "=", "(", ")", "{", "}",
    "[", "]", ";", ":", ",", ".", "&", "|", "~", "?",
]

class LexError(SyntaxError):
    pass

@dataclass
class Token:
    type: str
    value: str
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, L{self.line})"

class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.n = len(source)

    def _peek(self, off: int = 0) -> str:
        i = self.pos + off
        return self.src[i] if i < self.n else ""

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _error(self, msg: str):
        raise LexError(f"[lex] line {self.line}: {msg}")

    def _long_bracket_level(self) -> int:
        if self._peek() != "[":
            return -1
        i = self.pos + 1
        level = 0
        while i < self.n and self.src[i] == "=":
            level += 1
            i += 1
        if i < self.n and self.src[i] == "[":
            return level
        return -1

    def _read_long_string(self, level: int) -> str:
        self._advance()
        for _ in range(level):
            self._advance()
        self._advance()
        if self._peek() == "\r":
            self._advance()
        if self._peek() == "\n":
            self._advance()
        close = "]" + "=" * level + "]"
        start = self.pos
        while True:
            if self.pos >= self.n:
                self._error("unterminated long string/comment")
            if self._peek() == "]" and self.src[self.pos:self.pos + len(close)] == close:
                text = self.src[start:self.pos]
                for _ in range(len(close)):
                    self._advance()
                return text
            self._advance()

    def _read_quoted_string(self) -> str:
        quote = self._advance()
        out = []
        while True:
            if self.pos >= self.n:
                self._error("unterminated string")
            ch = self._peek()
            if ch == quote:
                self._advance()
                break
            if ch == "\n":
                self._error("unterminated string (newline)")
            if ch == "\\":
                self._advance()
                esc = self._peek()
                mapping = {
                    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
                    "f": "\f", "v": "\v", "\\": "\\", '"': '"', "'": "'",
                    "\n": "\n",
                }
                if esc in mapping:
                    out.append(mapping[esc])
                    self._advance()
                elif esc == "x":
                    self._advance()
                    hexd = ""
                    for _ in range(2):
                        if self._peek() in "0123456789abcdefABCDEF":
                            hexd += self._advance()
                    out.append(chr(int(hexd, 16)))
                elif esc == "z":
                    self._advance()
                    while self._peek() and self._peek() in " \t\r\n":
                        self._advance()
                elif esc.isdigit():
                    dec = ""
                    for _ in range(3):
                        if self._peek().isdigit():
                            dec += self._advance()
                        else:
                            break
                    out.append(chr(int(dec)))
                else:
                    out.append(self._advance())
            else:
                out.append(self._advance())
        return "".join(out)

    def _read_number(self) -> str:
        start = self.pos
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance(); self._advance()
            while self._peek() and (self._peek() in "0123456789abcdefABCDEF_.pP" or
                                    (self._peek() in "+-" and self.src[self.pos - 1] in "pP")):
                self._advance()
        elif self._peek() == "0" and self._peek(1) in ("b", "B"):
            self._advance(); self._advance()
            while self._peek() and self._peek() in "01_":
                self._advance()
        else:
            while self._peek() and (self._peek().isdigit() or self._peek() in "._eE" or
                                    (self._peek() in "+-" and self.src[self.pos - 1] in "eE")):
                self._advance()
        return self.src[start:self.pos]

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.pos < self.n:
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            if ch == "-" and self._peek(1) == "-":
                self._advance(); self._advance()
                level = self._long_bracket_level()
                if level >= 0:
                    self._read_long_string(level)
                else:
                    while self.pos < self.n and self._peek() != "\n":
                        self._advance()
                continue
            line, col = self.line, self.col
            if ch == "[":
                level = self._long_bracket_level()
                if level >= 0:
                    s = self._read_long_string(level)
                    tokens.append(Token("STRING", s, line, col))
                    continue
            if ch in "'\"":
                s = self._read_quoted_string()
                tokens.append(Token("STRING", s, line, col))
                continue
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                num = self._read_number()
                tokens.append(Token("NUMBER", num, line, col))
                continue
            if ch.isalpha() or ch == "_":
                start = self.pos
                while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
                    self._advance()
                word = self.src[start:self.pos]
                ttype = "KEYWORD" if word in KEYWORDS else "NAME"
                tokens.append(Token(ttype, word, line, col))
                continue
            matched = None
            for sym in SYMBOLS:
                if self.src[self.pos:self.pos + len(sym)] == sym:
                    matched = sym
                    break
            if matched is None:
                self._error(f"unexpected character {ch!r}")
            for _ in range(len(matched)):
                self._advance()
            tokens.append(Token("SYMBOL", matched, line, col))
        tokens.append(Token("EOF", "", self.line, self.col))
        return tokens

def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
