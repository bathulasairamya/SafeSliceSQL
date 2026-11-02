"""Small SQL lexer for preserving statements, literals, and quoted identifiers.

This is a lexical boundary, not a full SQL grammar. MySQL backslash escapes and
whitespace-delimited -- comments are supported; executable comments are rejected.
"""

from typing import NamedTuple


class Token(NamedTuple):
    kind: str
    text: str


def tokenize(sql: str) -> list[Token]:
    tokens = []
    index = 0
    while index < len(sql):
        start = index
        char = sql[index]
        if char.isspace():
            index += 1
            while index < len(sql) and sql[index].isspace():
                index += 1
            tokens.append(Token("space", sql[start:index]))
        elif char in "'\"`":
            index += 1
            while index < len(sql):
                if sql[index] == "\\" and char != "`":
                    index += 2
                elif sql[index] == char:
                    if index + 1 < len(sql) and sql[index + 1] == char:
                        index += 2
                    else:
                        index += 1
                        break
                else:
                    index += 1
            else:
                raise ValueError("Unterminated SQL string or quoted identifier.")
            tokens.append(Token("literal" if char == "'" else "quoted", sql[start:index]))
        elif char == "#" or (
            sql.startswith("--", index) and (index + 2 == len(sql) or sql[index + 2].isspace())
        ):
            newline = sql.find("\n", index)
            index = len(sql) if newline == -1 else newline
            tokens.append(Token("space", " "))
        elif sql.startswith("/*", index):
            if sql.startswith("/*!", index):
                raise ValueError("Executable SQL comments are not supported in workloads.")
            end = sql.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated SQL block comment.")
            index = end + 2
            tokens.append(Token("space", " "))
        elif char.isalpha() or char == "_":
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] in "_$"):
                index += 1
            tokens.append(Token("word", sql[start:index]))
        else:
            tokens.append(Token("symbol", char))
            index += 1
    return tokens


def split_statements(sql: str) -> list[str]:
    statements, parts = [], []
    for token in tokenize(sql):
        if token == Token("symbol", ";"):
            statement = "".join(parts).strip()
            if statement:
                statements.append(statement)
            parts = []
        else:
            parts.append(token.text)
    statement = "".join(parts).strip()
    if statement:
        statements.append(statement)
    return statements


def syntax_only(sql: str) -> str:
    """Mask quoted text so regex keyword checks cannot match inside literals."""
    return "".join(
        " " * len(token.text) if token.kind in {"literal", "quoted"} else token.text
        for token in tokenize(sql)
    )


def identifier(token: Token) -> str | None:
    if token.kind == "word":
        return token.text
    if token.kind == "quoted":
        quote = token.text[0]
        return token.text[1:-1].replace(quote * 2, quote)
    return None
