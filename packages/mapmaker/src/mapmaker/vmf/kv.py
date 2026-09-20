"""VMF is nested tab-indented key-value text; this renders it.

Order is preserved rather than sorted: Hammer's own output has a conventional
key order, and the cheapest check on this writer is diffing what it produces
against a file Hammer (or a careful hand) wrote.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def num(v) -> str:
    """Hammer writes whole numbers without a decimal point; match that.

    Not cosmetic. Integral coordinates are what keep brushes meeting exactly,
    and a value that prints as `64` rather than `64.0000001` is a value that
    has not silently drifted off the grid.
    """
    f = float(v)
    r = round(f)
    return str(int(r)) if abs(f - r) < 1e-6 else f"{f:g}"


def fmt(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return num(v)
    if isinstance(v, (tuple, list)):
        return " ".join(fmt(x) for x in v)
    raise TypeError(f"cannot write {type(v).__name__} into a vmf")


@dataclass
class Block:
    """One `name { "k" "v" ... }` block, with children nested inside it."""

    name: str
    pairs: list[tuple[str, str]] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)

    def set(self, key: str, value) -> Block:
        self.pairs.append((key, fmt(value)))
        return self

    def update(self, pairs) -> Block:
        for k, v in dict(pairs).items():
            self.set(k, v)
        return self

    def child(self, name: str) -> Block:
        b = Block(name)
        self.children.append(b)
        return b

    def add(self, block: Block) -> Block:
        self.children.append(block)
        return block

    def lines(self, depth: int = 0):
        pad = "\t" * depth
        yield pad + self.name
        yield pad + "{"
        for k, v in self.pairs:
            yield f'{pad}\t"{k}" "{v}"'
        for c in self.children:
            yield from c.lines(depth + 1)
        yield pad + "}"

    def render(self, depth: int = 0) -> str:
        return "\n".join(self.lines(depth)) + "\n"


EDITOR_SOLID = {"color": "0 180 0", "visgroupshown": 1, "visgroupautoshown": 1}
EDITOR_ENTITY = {"color": "220 30 220", "visgroupshown": 1, "visgroupautoshown": 1}


def editor(colors=EDITOR_SOLID) -> Block:
    return Block("editor").update(colors)
