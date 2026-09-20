"""Read a .vmf back into the same `Block` tree the writer renders.

The writer's `Block` is already the right shape for this: a name, an ordered
list of key-value pairs and a list of children. Ordered pairs matter, and so
does allowing a key twice - `dispinfo` rows, `connections` outputs and a
`side`'s own fields all repeat, and a dict would quietly drop them.

Deliberately not a semantic model of a map. Everything above this - what a
solid means, which entities carry coordinates - lives in `splice`, so that
reading a 15 MB decompile stays a parse and nothing more.
"""
from __future__ import annotations

import re
from pathlib import Path

from .kv import Block

PAIR = re.compile(r'^"([^"]*)" "(.*)"$')
NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


def loads(text: str) -> list[Block]:
    """Parse a whole .vmf into its top-level blocks.

    VMF is line-oriented - a block name, a brace, one `"k" "v"` per line - and
    every one of the 637,142 lines BSPSource writes for ministry matches that,
    so a line parser is not a shortcut here, it is the grammar.
    """
    top: list[Block] = []
    stack: list[Block] = []
    pending: str | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line == "{":
            if pending is None:
                raise ValueError(f"line {lineno}: '{{' with no block name before it")
            block = Block(pending)
            (stack[-1].children if stack else top).append(block)
            stack.append(block)
            pending = None
        elif line == "}":
            if not stack:
                raise ValueError(f"line {lineno}: '}}' closes nothing")
            stack.pop()
        elif m := PAIR.match(line):
            if not stack:
                raise ValueError(f"line {lineno}: pair outside any block: {line}")
            stack[-1].pairs.append((m.group(1), m.group(2)))
        elif NAME.match(line):
            pending = line
        else:
            raise ValueError(f"line {lineno}: cannot read {line!r}")
    if stack:
        raise ValueError(f"unclosed block {stack[-1].name!r} at end of file")
    return top


def load(path) -> list[Block]:
    return loads(Path(path).read_text(encoding="latin-1"))


def dumps(blocks) -> str:
    return "".join(b.render() for b in blocks)


def save(blocks, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(blocks), encoding="latin-1")
    return path


# ------------------------------------------------------------------ accessors
def get(block: Block, key: str, default: str | None = None) -> str | None:
    """The first value for `key`, which is what Hammer's readers take."""
    for k, v in block.pairs:
        if k == key:
            return v
    return default


def put(block: Block, key: str, value: str) -> None:
    """Replace every value for `key` in place, keeping its position."""
    block.pairs[:] = [(k, value if k == key else v) for k, v in block.pairs]


def has(block: Block, key: str) -> bool:
    return any(k == key for k, _ in block.pairs)


def walk(block: Block):
    """The block and every descendant, depth first."""
    yield block
    for child in block.children:
        yield from walk(child)


def children(block: Block, name: str) -> list[Block]:
    return [c for c in block.children if c.name == name]


def world(blocks) -> Block:
    for b in blocks:
        if b.name == "world":
            return b
    raise ValueError("no world block - is this a .vmf?")


def entities(blocks) -> list[Block]:
    return [b for b in blocks if b.name == "entity"]


def max_id(blocks) -> int:
    """The highest id in the file, so new geometry can be numbered above it."""
    top = 0
    for b in blocks:
        for node in walk(b):
            for k, v in node.pairs:
                if k == "id":
                    try:
                        top = max(top, int(v))
                    except ValueError:
                        pass
    return top
