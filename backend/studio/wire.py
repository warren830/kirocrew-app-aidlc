"""The one serializer for Studio's records: dataclass fields in declaration order, which is the wire order.

A leaf module (no Studio imports), so the store and the readers can share it without an import edge.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, ClassVar, Mapping


def wire_value(value: Any) -> Any:
    """JSON-native data: records serialise themselves, tuples become lists, mappings are copied."""
    if hasattr(value, "to_json"):
        return value.to_json()
    if isinstance(value, (list, tuple)):
        return [wire_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: wire_value(item) for key, item in value.items()}
    return value


class WireRecord:
    """``to_json`` over the dataclass fields, or ``_wire_keys`` (properties allowed; ``name?`` drops ``None``)."""

    __slots__ = ()
    _wire_keys: ClassVar[tuple[str, ...]] = ()

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in self._wire_keys or [f.name for f in fields(self)]:
            name = key.rstrip("?")
            value = getattr(self, name)
            if value is not None or name == key:
                out[name] = wire_value(value)
        return out
