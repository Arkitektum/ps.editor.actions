"""Read the table structure out of a PostgreSQL DDL script.

This is not a SQL parser. It understands the statements that describe a schema --
``CREATE TABLE``, ``ALTER TABLE … ADD``, ``COMMENT ON``, ``INSERT``/``COPY`` of
code-list rows, and PostGIS's ``AddGeometryColumn`` -- and skips everything else
(functions, indexes, grants, ``SET``). That covers three kinds of file:

* scripts written by :mod:`postgis.writer`;
* ``pg_dump --schema-only`` (and full dumps, whose ``COPY`` data is read too);
* scripts from the Gistools PostGIS generator, which leave geometry columns
  untyped and put the SRID in ``CHECK (st_srid(col) = …)`` constraints.

Statements are split on ``;`` outside strings, quoted names, comments and
dollar-quoted bodies, then tokenised, so nothing is matched on raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SqlColumn", "SqlForeignKey", "SqlSchema", "SqlTable", "SqlSyntaxError", "Token", "parse_sql"]


class SqlSyntaxError(ValueError):
    """A statement the reader recognised but could not make sense of."""


@dataclass(frozen=True)
class Token:
    kind: str  # word | ident | string | number | op | dollar
    value: str

    def is_word(self, *words: str) -> bool:
        return self.kind == "word" and self.value in words

    def is_op(self, *ops: str) -> bool:
        return self.kind == "op" and self.value in ops


@dataclass
class SqlColumn:
    name: str
    type_name: str  # lowercase, schema qualifier dropped: "geometry", "double precision"
    type_args: list[str] = field(default_factory=list)  # "MultiPolygon", "25833"
    array: bool = False
    not_null: bool = False
    identity: bool = False
    default: list[Token] = field(default_factory=list)


@dataclass
class SqlForeignKey:
    columns: list[str]
    ref_schema: str | None
    ref_table: str
    ref_columns: list[str]
    on_delete: str = "NO ACTION"


@dataclass
class SqlTable:
    schema: str | None
    name: str
    columns: dict[str, SqlColumn] = field(default_factory=dict)
    primary_key: list[str] = field(default_factory=list)
    unique: list[list[str]] = field(default_factory=list)
    foreign_keys: list[SqlForeignKey] = field(default_factory=list)
    checks: list[list[Token]] = field(default_factory=list)
    comment: str | None = None
    column_comments: dict[str, str] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    inherits: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.schema, self.name)


@dataclass
class SqlSchema:
    tables: dict[tuple[str | None, str], SqlTable] = field(default_factory=dict)
    # (schema, table, column) -> (geometry type, srid, dimensions) from
    # AddGeometryColumn, for columns that do not declare them in their type.
    geometry_columns: dict[tuple[str | None, str, str], tuple[str, int | None, int | None]] = field(
        default_factory=dict
    )

    def find(self, schema: str | None, name: str) -> SqlTable | None:
        """Resolve a table reference; an unqualified one may live in any schema."""
        table = self.tables.get((schema, name))
        if table is not None or schema is not None:
            return table
        matches = [t for (s, n), t in self.tables.items() if n == name]
        return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------- #
# Lexer
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[A-Za-z_\u0080-\uffff][A-Za-z0-9_$\u0080-\uffff]*")
_NUMBER = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_CONTINUATION = re.compile(r"[ \t\r\f]*\n\s*'")
_OPERATORS = ("::", "<=", ">=", "<>", "!=", "||", "->>", "->")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


class _Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def _error(self, message: str) -> SqlSyntaxError:
        line = self.text.count("\n", 0, self.pos) + 1
        return SqlSyntaxError(f"{message} (line {line})")

    def _skip_space_and_comments(self) -> None:
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char.isspace():
                self.pos += 1
            elif text.startswith("--", self.pos):
                end = text.find("\n", self.pos)
                self.pos = len(text) if end < 0 else end + 1
            elif text.startswith("/*", self.pos):
                depth, self.pos = 1, self.pos + 2
                while depth and self.pos < len(text):
                    if text.startswith("/*", self.pos):
                        depth, self.pos = depth + 1, self.pos + 2
                    elif text.startswith("*/", self.pos):
                        depth, self.pos = depth - 1, self.pos + 2
                    else:
                        self.pos += 1
                if depth:
                    raise self._error("Unterminated /* comment")
            else:
                return

    def _quoted(self, quote: str, *, backslash: bool = False) -> str:
        """Body of a quoted string or name starting at ``self.pos`` (the quote);
        a doubled quote stands for one quote."""
        text = self.text
        out: list[str] = []
        self.pos += 1
        while True:
            if self.pos >= len(text):
                raise self._error(f"Unterminated {quote}-quoted text")
            char = text[self.pos]
            if backslash and char == "\\" and self.pos + 1 < len(text):
                escaped = text[self.pos + 1]
                out.append(_ESCAPES.get(escaped, escaped))
                self.pos += 2
            elif char == quote:
                if text.startswith(quote * 2, self.pos):
                    out.append(quote)
                    self.pos += 2
                else:
                    self.pos += 1
                    return "".join(out)
            else:
                out.append(char)
                self.pos += 1

    def next(self) -> Token | None:
        self._skip_space_and_comments()
        text = self.text
        if self.pos >= len(text):
            return None
        char = text[self.pos]

        if char in "eE" and text.startswith("'", self.pos + 1):
            self.pos += 1
            return Token("string", self._quoted("'", backslash=True))
        if char == "'":
            value = self._quoted("'")
            # Adjacent literals separated by a newline are one literal.
            while True:
                gap = _CONTINUATION.match(text, self.pos)
                if not gap:
                    break
                self.pos = gap.end() - 1
                value += self._quoted("'")
            return Token("string", value)
        if char == '"':
            return Token("ident", self._quoted('"'))
        if char == "$":
            tag = _DOLLAR_TAG.match(text, self.pos)
            if tag:
                end = text.find(tag.group(), tag.end())
                if end < 0:
                    raise self._error("Unterminated dollar-quoted text")
                self.pos = end + len(tag.group())
                return Token("dollar", text[tag.end() : end])
        number = _NUMBER.match(text, self.pos)
        if number and (char.isdigit() or char == "."):
            self.pos = number.end()
            return Token("number", number.group())
        word = _WORD.match(text, self.pos)
        if word:
            self.pos = word.end()
            return Token("word", word.group().lower())
        for operator in _OPERATORS:
            if text.startswith(operator, self.pos):
                self.pos += len(operator)
                return Token("op", operator)
        self.pos += 1
        return Token("op", char)

    def rest_of_line(self) -> None:
        end = self.text.find("\n", self.pos)
        self.pos = len(self.text) if end < 0 else end + 1

    def copy_data(self) -> list[str]:
        """Lines of a ``COPY … FROM stdin`` block, up to the ``\\.`` terminator."""
        self.rest_of_line()
        lines: list[str] = []
        while self.pos < len(self.text):
            end = self.text.find("\n", self.pos)
            line = self.text[self.pos : len(self.text) if end < 0 else end]
            self.pos = len(self.text) if end < 0 else end + 1
            if line.rstrip("\r") == "\\.":
                return lines
            lines.append(line.rstrip("\r"))
        return lines


def _statements(text: str) -> list[tuple[list[Token], list[str] | None]]:
    """Split a script into token lists, one per statement. A ``COPY … FROM
    stdin`` statement comes with its data lines."""
    lexer = _Lexer(text)
    statements: list[tuple[list[Token], list[str] | None]] = []
    current: list[Token] = []
    while True:
        token = lexer.next()
        if token is None:
            break
        if token.is_op(";"):
            if current:
                data = None
                if current[0].is_word("copy") and any(t.is_word("stdin") for t in current):
                    data = lexer.copy_data()
                statements.append((current, data))
            current = []
        elif token.is_op("\\"):
            # psql meta-commands (\connect, \restrict) run to the end of the line.
            lexer.rest_of_line()
        else:
            current.append(token)
    if current:
        statements.append((current, None))
    return statements


# --------------------------------------------------------------------------- #
# Statement parsing
# --------------------------------------------------------------------------- #

_COLUMN_CONSTRAINT_START = {
    "constraint", "not", "null", "default", "primary", "unique", "references",
    "check", "generated", "collate",
}
_TABLE_CONSTRAINT_START = {"constraint", "primary", "unique", "foreign", "check", "exclude", "like"}
_ACTIONS = ("NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT")


class _Cursor:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self, offset: int = 0) -> Token | None:
        index = self.i + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def take(self) -> Token | None:
        token = self.peek()
        if token is not None:
            self.i += 1
        return token

    def accept(self, *words: str) -> bool:
        """Consume the words if they come next, in order."""
        for offset, word in enumerate(words):
            token = self.peek(offset)
            if token is None or not token.is_word(word):
                return False
        self.i += len(words)
        return True

    def accept_op(self, op: str) -> bool:
        token = self.peek()
        if token is not None and token.is_op(op):
            self.i += 1
            return True
        return False

    def at_end(self) -> bool:
        return self.i >= len(self.tokens)

    def name(self) -> str:
        token = self.take()
        if token is None or token.kind not in ("word", "ident"):
            raise SqlSyntaxError(f"Expected a name, found {token.value if token else 'end of statement'!r}")
        return token.value

    def qualified_name(self) -> list[str]:
        parts = [self.name()]
        while self.accept_op("."):
            parts.append(self.name())
        return parts

    def group(self) -> list[Token]:
        """The tokens inside the parenthesis that comes next, which is consumed."""
        if not self.accept_op("("):
            raise SqlSyntaxError("Expected '('")
        start, depth = self.i, 1
        while depth:
            token = self.take()
            if token is None:
                raise SqlSyntaxError("Unbalanced parentheses")
            if token.is_op("("):
                depth += 1
            elif token.is_op(")"):
                depth -= 1
        return self.tokens[start : self.i - 1]

    def name_list(self) -> list[str]:
        return [name for name in _split_commas(self.group()) for name in _names(name)]


def _split_commas(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in tokens:
        if token.is_op("(", "["):
            depth += 1
        elif token.is_op(")", "]"):
            depth -= 1
        if depth == 0 and token.is_op(","):
            parts.append([])
        else:
            parts[-1].append(token)
    return [part for part in parts if part]


def _names(tokens: list[Token]) -> list[str]:
    return [tokens[0].value] if tokens and tokens[0].kind in ("word", "ident") else []


def _split_qualified(parts: list[str], search_path: str | None) -> tuple[str | None, str]:
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return search_path, parts[-1]


def literal(tokens: list[Token]) -> Any:
    """The value of a literal expression: ``'x'``, ``'x'::text``, ``-1``,
    ``NULL``, ``TRUE``. Anything else comes back as its source text."""
    core = list(tokens)
    while len(core) >= 2 and core[0].is_op("(") and core[-1].is_op(")"):
        core = core[1:-1]
    if "::" in [t.value for t in core if t.kind == "op"]:
        core = core[: [t.value for t in core].index("::")]
    if len(core) == 1:
        token = core[0]
        if token.kind in ("string", "dollar"):
            return token.value
        if token.kind == "number":
            return float(token.value) if any(c in token.value for c in ".eE") else int(token.value)
        if token.is_word("null"):
            return None
        if token.is_word("true", "false"):
            return token.value == "true"
    if len(core) == 2 and core[0].is_op("-") and core[1].kind == "number":
        value = literal([core[1]])
        return -value if isinstance(value, (int, float)) else value
    return " ".join(t.value for t in tokens)


class _Parser:
    def __init__(self) -> None:
        self.schema = SqlSchema()
        self.search_path: str | None = None

    def table(self, parts: list[str], *, create: bool = False) -> SqlTable | None:
        schema, name = _split_qualified(parts, self.search_path)
        table = self.schema.tables.get((schema, name))
        if table is None and not create:
            table = self.schema.find(schema, name) if len(parts) == 1 else None
        if table is None and create:
            table = SqlTable(schema, name)
            self.schema.tables[(schema, name)] = table
        return table

    def parse(self, text: str) -> SqlSchema:
        for tokens, data in _statements(text):
            cursor = _Cursor(tokens)
            try:
                self.statement(cursor, data)
            except SqlSyntaxError as error:
                preview = " ".join(t.value for t in tokens[:8])
                raise SqlSyntaxError(f"{error} in statement: {preview} …") from None
        return self.schema

    def statement(self, cursor: _Cursor, data: list[str] | None) -> None:
        if cursor.accept("create"):
            cursor.accept("or", "replace")
            for modifier in ("global", "local", "temporary", "temp", "unlogged", "foreign"):
                cursor.accept(modifier)
            if cursor.accept("table"):
                self.create_table(cursor)
        elif cursor.accept("alter", "table"):
            self.alter_table(cursor)
        elif cursor.accept("comment", "on"):
            self.comment(cursor)
        elif cursor.accept("insert", "into"):
            self.insert(cursor)
        elif cursor.accept("copy"):
            self.copy(cursor, data)
        elif cursor.accept("set"):
            self.set(cursor)
        elif cursor.accept("select"):
            self.select(cursor)

    # -- CREATE TABLE ------------------------------------------------------ #

    def create_table(self, cursor: _Cursor) -> None:
        cursor.accept("if", "not", "exists")
        parts = cursor.qualified_name()
        token = cursor.peek()
        if token is None or not token.is_op("("):
            return  # CREATE TABLE … AS / PARTITION OF / OF type: no column list
        table = self.table(parts, create=True)
        assert table is not None
        for element in _split_commas(cursor.group()):
            first = element[0]
            if first.kind == "word" and first.value in _TABLE_CONSTRAINT_START:
                self.table_constraint(table, _Cursor(element))
            else:
                self.column(table, _Cursor(element))
        if cursor.accept("inherits"):
            for element in _split_commas(cursor.group()):
                names = [t.value for t in element if t.kind in ("word", "ident")]
                table.inherits.append(_split_qualified(names, self.search_path))

    def column(self, table: SqlTable, cursor: _Cursor) -> None:
        name = cursor.name()
        type_tokens: list[Token] = []
        depth = 0
        while not cursor.at_end():
            token = cursor.peek()
            assert token is not None
            if depth == 0 and token.kind == "word" and token.value in _COLUMN_CONSTRAINT_START:
                break
            depth += token.is_op("(", "[") - token.is_op(")", "]")
            type_tokens.append(cursor.take())  # type: ignore[arg-type]
        column = _column_type(name, type_tokens)
        table.columns[name] = column
        self.column_constraints(table, column, cursor)

    def column_constraints(self, table: SqlTable, column: SqlColumn, cursor: _Cursor) -> None:
        while not cursor.at_end():
            if cursor.accept("constraint"):
                cursor.name()
            elif cursor.accept("not", "null"):
                column.not_null = True
            elif cursor.accept("null"):
                column.not_null = False
            elif cursor.accept("default"):
                column.default = self.expression(cursor)
                if any(t.is_word("nextval") for t in column.default):
                    column.identity = True
            elif cursor.accept("primary", "key"):
                table.primary_key = [column.name]
                column.not_null = True
            elif cursor.accept("unique"):
                table.unique.append([column.name])
            elif cursor.accept("references"):
                table.foreign_keys.append(self.references(cursor, [column.name]))
            elif cursor.accept("check"):
                table.checks.append(cursor.group())
            elif cursor.accept("generated"):
                is_identity = False
                while not cursor.at_end() and not cursor.peek().is_op("("):  # type: ignore[union-attr]
                    is_identity = is_identity or cursor.take().is_word("identity")  # type: ignore[union-attr]
                    if is_identity:
                        break
                column.identity = column.identity or is_identity
                if not cursor.at_end() and cursor.peek().is_op("("):  # type: ignore[union-attr]
                    cursor.group()
            elif cursor.accept("collate"):
                cursor.qualified_name()
            else:
                cursor.take()

    def expression(self, cursor: _Cursor) -> list[Token]:
        """Tokens up to the next column constraint, at parenthesis depth 0."""
        tokens: list[Token] = []
        depth = 0
        while not cursor.at_end():
            token = cursor.peek()
            assert token is not None
            if depth == 0 and token.kind == "word" and token.value in _COLUMN_CONSTRAINT_START - {"null"}:
                break
            depth += token.is_op("(") - token.is_op(")")
            tokens.append(cursor.take())  # type: ignore[arg-type]
        return tokens

    def references(self, cursor: _Cursor, columns: list[str]) -> SqlForeignKey:
        ref_schema, ref_table = _split_qualified(cursor.qualified_name(), self.search_path)
        ref_columns = cursor.name_list() if cursor.peek() and cursor.peek().is_op("(") else []  # type: ignore[union-attr]
        on_delete = "NO ACTION"
        while not cursor.at_end():
            if cursor.accept("on", "delete"):
                on_delete = next(
                    (action for action in _ACTIONS if cursor.accept(*action.lower().split())), "NO ACTION"
                )
            elif cursor.accept("on", "update"):
                next((action for action in _ACTIONS if cursor.accept(*action.lower().split())), None)
            elif cursor.accept("match"):
                cursor.take()
            else:
                token = cursor.peek()
                if token is not None and token.kind == "word" and token.value in _COLUMN_CONSTRAINT_START:
                    break
                cursor.take()
        return SqlForeignKey(columns, ref_schema, ref_table, ref_columns, on_delete)

    def table_constraint(self, table: SqlTable, cursor: _Cursor) -> None:
        if cursor.accept("constraint"):
            cursor.name()
        if cursor.accept("primary", "key"):
            table.primary_key = cursor.name_list()
            for name in table.primary_key:
                if name in table.columns:
                    table.columns[name].not_null = True
        elif cursor.accept("unique"):
            cursor.accept("nulls", "not", "distinct") or cursor.accept("nulls", "distinct")
            table.unique.append(cursor.name_list())
        elif cursor.accept("foreign", "key"):
            columns = cursor.name_list()
            if cursor.accept("references"):
                table.foreign_keys.append(self.references(cursor, columns))
        elif cursor.accept("check"):
            table.checks.append(cursor.group())

    # -- ALTER TABLE -------------------------------------------------------- #

    def alter_table(self, cursor: _Cursor) -> None:
        cursor.accept("if", "exists")
        cursor.accept("only")
        table = self.table(cursor.qualified_name())
        cursor.accept_op("*")
        if table is None:
            return
        for action in _split_commas(cursor.tokens[cursor.i :]):
            sub = _Cursor(action)
            if sub.accept("add"):
                first = sub.peek()
                if first is not None and first.kind == "word" and first.value in _TABLE_CONSTRAINT_START:
                    self.table_constraint(table, sub)
                else:
                    sub.accept("column")
                    sub.accept("if", "not", "exists")
                    self.column(table, sub)
            elif sub.accept("alter"):
                sub.accept("column")
                name = sub.name()
                column = table.columns.get(name)
                if column is None:
                    continue
                if sub.accept("set", "not", "null"):
                    column.not_null = True
                elif sub.accept("drop", "not", "null"):
                    column.not_null = False
                elif sub.accept("set", "default"):
                    column.default = sub.tokens[sub.i :]
                    if any(t.is_word("nextval") for t in column.default):
                        column.identity = True
                elif sub.accept("add", "generated"):
                    column.identity = True

    # -- COMMENT ON --------------------------------------------------------- #

    def comment(self, cursor: _Cursor) -> None:
        if cursor.accept("table"):
            kind = "table"
        elif cursor.accept("column"):
            kind = "column"
        else:
            return
        parts = cursor.qualified_name()
        if not cursor.accept("is"):
            return
        value = literal(cursor.tokens[cursor.i :])
        text = value if isinstance(value, str) else None
        if kind == "table":
            table = self.table(parts)
            if table is not None:
                table.comment = text
            return
        if len(parts) < 2:
            return
        table = self.table(parts[:-1])
        if table is not None:
            if text is None:
                table.column_comments.pop(parts[-1], None)
            else:
                table.column_comments[parts[-1]] = text

    # -- rows --------------------------------------------------------------- #

    def insert(self, cursor: _Cursor) -> None:
        table = self.table(cursor.qualified_name())
        if cursor.accept("as"):
            cursor.name()
        columns = cursor.name_list() if cursor.peek() and cursor.peek().is_op("(") else None  # type: ignore[union-attr]
        if cursor.accept("overriding"):
            cursor.take()
            cursor.accept("value")
        if table is None or not cursor.accept("values"):
            return
        names = columns or list(table.columns)
        while cursor.peek() is not None and cursor.peek().is_op("("):  # type: ignore[union-attr]
            values = [literal(part) for part in _split_commas(cursor.group())]
            table.rows.append(dict(zip(names, values)))
            if not cursor.accept_op(","):
                break

    def copy(self, cursor: _Cursor, data: list[str] | None) -> None:
        table = self.table(cursor.qualified_name())
        columns = cursor.name_list() if cursor.peek() and cursor.peek().is_op("(") else None  # type: ignore[union-attr]
        if table is None or data is None:
            return
        names = columns or list(table.columns)
        for line in data:
            values = [_copy_value(field) for field in line.split("\t")]
            table.rows.append(dict(zip(names, values)))

    # -- other statements --------------------------------------------------- #

    def set(self, cursor: _Cursor) -> None:
        cursor.accept("session") or cursor.accept("local")
        if cursor.accept("search_path"):
            cursor.accept("to") or cursor.accept_op("=")
            first = cursor.take()
            if first is not None and first.kind in ("word", "ident", "string"):
                self.search_path = first.value if first.value not in ("", "public") else None

    def select(self, cursor: _Cursor) -> None:
        """``SELECT AddGeometryColumn(…)``: the old way of declaring a geometry."""
        tokens = cursor.tokens
        for index, token in enumerate(tokens):
            if token.is_word("addgeometrycolumn") and index + 1 < len(tokens) and tokens[index + 1].is_op("("):
                args = [literal(part) for part in _split_commas(_Cursor(tokens[index + 1 :]).group())]
                numbers = [i for i, arg in enumerate(args) if isinstance(arg, int) and not isinstance(arg, bool)]
                if not numbers:
                    return
                srid_at = numbers[0]
                names = [arg for arg in args[:srid_at] if isinstance(arg, str)]
                if len(names) < 2:
                    return
                schema = names[-3] if len(names) >= 3 else self.search_path
                rest = args[srid_at + 1 :]
                geometry_type = str(rest[0]) if rest else "GEOMETRY"
                dims = rest[1] if len(rest) > 1 and isinstance(rest[1], int) else None
                self.schema.geometry_columns[(schema or None, names[-2], names[-1])] = (
                    geometry_type,
                    args[srid_at],
                    dims,
                )
                return


def _column_type(name: str, tokens: list[Token]) -> SqlColumn:
    words: list[str] = []
    args: list[str] = []
    array = False
    cursor = _Cursor(tokens)
    while not cursor.at_end():
        token = cursor.peek()
        assert token is not None
        if token.is_op("("):
            args.extend(" ".join(t.value for t in part) for part in _split_commas(cursor.group()))
        elif token.is_op("["):
            array = True
            cursor.take()
        elif token.is_op("."):
            words.clear()  # schema qualifier: public.geometry
            cursor.take()
        elif token.kind in ("word", "ident"):
            words.append(token.value.lower())
            cursor.take()
        else:
            cursor.take()
    return SqlColumn(name, " ".join(words), args, array)


def _copy_value(field: str) -> Any:
    """A value in ``COPY`` text format: ``\\N`` is NULL, backslash escapes."""
    if field == "\\N":
        return None
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), field)


def parse_sql(text: str) -> SqlSchema:
    """Read the tables of a DDL script. Raises :class:`SqlSyntaxError` for a
    recognised statement that does not parse."""
    return _Parser().parse(text)
