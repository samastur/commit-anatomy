"""Two-level category taxonomy. It is data: config can extend it and remap categories."""

from __future__ import annotations

from dataclasses import dataclass, replace

UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class TopCategory:
    name: str
    subcategories: tuple[str, ...]
    split_lines: bool


DEFAULT_TOPS: tuple[TopCategory, ...] = (
    TopCategory("docs", ("user", "dev", "api", "llm", "changelog"), False),
    TopCategory("tests", ("unit", "integration", "e2e", "support"), True),
    TopCategory("code", ("app", "tooling"), True),
    TopCategory(
        "supporting",
        ("deps", "migrations", "translations", "build", "ci", "generated", "assets"),
        False,
    ),
    TopCategory(UNCLASSIFIED, (), False),
)


class TaxonomyError(ValueError):
    pass


class Taxonomy:
    def __init__(
        self,
        tops: tuple[TopCategory, ...] = DEFAULT_TOPS,
        remap: dict[str, str] | None = None,
    ):
        self._tops = {t.name: t for t in tops}
        self._order = tuple(t.name for t in tops)
        self._remap = dict(remap or {})

    @property
    def tops(self) -> tuple[str, ...]:
        return self._order

    def is_valid(self, category: str) -> bool:
        top, _, sub = category.partition("/")
        t = self._tops.get(top)
        if t is None:
            return False
        if sub:
            return sub in t.subcategories
        return not t.subcategories

    def split_lines(self, category: str) -> bool:
        t = self._tops.get(category.partition("/")[0])
        return bool(t and t.split_lines)

    def remap(self, category: str) -> str:
        return self._remap.get(category, category)

    def order_key(self, category: str) -> tuple[int, int]:
        top, _, sub = category.partition("/")
        t = self._tops.get(top)
        if t is None:
            return (len(self._order), 0)
        sub_index = t.subcategories.index(sub) if sub in t.subcategories else -1
        return (self._order.index(top), sub_index)

    def extended(self, table: dict) -> Taxonomy:
        """Return a copy with `[taxonomy]` config applied (see the spec, section 3.3)."""
        tops = dict(self._tops)
        order = list(self._order)
        remap = dict(self._remap)
        for name, value in table.items():
            if name == "remap":
                if not isinstance(value, dict):
                    raise TaxonomyError("remap: must be a table")
                remap.update(value)
                continue
            if name == UNCLASSIFIED:
                raise TaxonomyError(f"{UNCLASSIFIED}: cannot be changed")
            if not isinstance(value, dict):
                raise TaxonomyError(f"{name}: must be a table")
            unknown = set(value) - {"subcategories", "split_lines"}
            if unknown:
                raise TaxonomyError(f"{name}.{sorted(unknown)[0]}: unknown field")
            subs = value.get("subcategories", [])
            if not isinstance(subs, list) or not all(
                isinstance(s, str) and s and "/" not in s for s in subs
            ):
                raise TaxonomyError(f"{name}.subcategories: must be a list of names without '/'")
            split = value.get("split_lines")
            if split is not None and not isinstance(split, bool):
                raise TaxonomyError(f"{name}.split_lines: must be true or false")
            old = tops.get(name)
            if old is None:
                tops[name] = TopCategory(name, tuple(subs), bool(split))
                order.insert(order.index(UNCLASSIFIED), name)
            else:
                merged = old.subcategories + tuple(s for s in subs if s not in old.subcategories)
                tops[name] = replace(
                    old,
                    subcategories=merged,
                    split_lines=old.split_lines if split is None else split,
                )
        result = Taxonomy(tuple(tops[n] for n in order), remap)
        for source, target in remap.items():
            if not result.is_valid(source):
                raise TaxonomyError(f"remap.{source}: unknown category")
            if not isinstance(target, str) or not result.is_valid(target):
                raise TaxonomyError(f"remap.{source}: unknown target category {target!r}")
        return result
