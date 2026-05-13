"""Normalization helpers for parser-specific layout data."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from recallforge.chunking.ir.models import BBox

_BBOX_KEYS = (
    "bbox",
    "box",
    "position",
    "layout_bbox",
    "poly",
    "polygon",
)


def coerce_bbox(value: Any) -> BBox | None:
    """Normalize common parser bbox shapes into ``BBox``.

    Supported inputs:
    - ``[x0, y0, x1, y1]``
    - ``[[x, y], [x, y], ...]`` polygons
    - ``{"x0": ..., "y0": ..., "x1": ..., "y1": ...}``
    - ``{"l": ..., "t": ..., "r": ..., "b": ...}``
    - objects with similarly named attributes
    """
    if value is None:
        return None
    if isinstance(value, BBox):
        return _ordered_bbox(value.x0, value.y0, value.x1, value.y1)
    if isinstance(value, dict):
        return _bbox_from_dict(value)
    if _is_sequence(value):
        return _bbox_from_sequence(value)
    return _bbox_from_object(value)


def extract_bbox(value: Any) -> BBox | None:
    """Find a bbox in a parser item or object."""
    if value is None:
        return None
    direct = coerce_bbox(value)
    if direct is not None:
        return direct
    if isinstance(value, dict):
        for key in _BBOX_KEYS:
            bbox = coerce_bbox(value.get(key))
            if bbox is not None:
                return bbox
    else:
        for key in _BBOX_KEYS:
            bbox = coerce_bbox(getattr(value, key, None))
            if bbox is not None:
                return bbox
    return None


def bbox_in_page(bbox: BBox, width: float | None, height: float | None) -> bool:
    if width is None or height is None:
        return True
    return (
        -1 <= bbox.x0 <= width + 1
        and -1 <= bbox.x1 <= width + 1
        and -1 <= bbox.y0 <= height + 1
        and -1 <= bbox.y1 <= height + 1
    )


def page_size_from_value(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if isinstance(value, dict):
        width = _first_number(value, ("width", "w", "page_width"))
        height = _first_number(value, ("height", "h", "page_height"))
        return width, height
    width = _first_attr_number(value, ("width", "w", "page_width"))
    height = _first_attr_number(value, ("height", "h", "page_height"))
    if width is None and hasattr(value, "size"):
        size = getattr(value, "size")
        if _is_sequence(size) and len(size) >= 2:
            width = _float_or_none(size[0])
            height = _float_or_none(size[1])
    return width, height


def _bbox_from_dict(value: dict[str, Any]) -> BBox | None:
    explicit = _bbox_from_keys(value, ("x0", "y0", "x1", "y1"))
    if explicit is not None:
        return explicit
    explicit = _bbox_from_keys(value, ("left", "top", "right", "bottom"))
    if explicit is not None:
        return explicit
    explicit = _bbox_from_keys(value, ("l", "t", "r", "b"))
    if explicit is not None:
        return explicit
    for key in _BBOX_KEYS:
        if key in value:
            nested = coerce_bbox(value[key])
            if nested is not None:
                return nested
    return None


def _bbox_from_keys(value: dict[str, Any], keys: tuple[str, str, str, str]) -> BBox | None:
    numbers = [_float_or_none(value.get(key)) for key in keys]
    if any(num is None for num in numbers):
        return None
    return _ordered_bbox(numbers[0], numbers[1], numbers[2], numbers[3])  # type: ignore[arg-type]


def _bbox_from_sequence(value: Sequence[Any]) -> BBox | None:
    if len(value) >= 4 and all(_is_number_like(item) for item in value[:4]):
        x0, y0, x1, y1 = (_float_or_none(item) for item in value[:4])
        if None not in (x0, y0, x1, y1):
            return _ordered_bbox(x0, y0, x1, y1)  # type: ignore[arg-type]

    points: list[tuple[float, float]] = []
    for point in value:
        if isinstance(point, dict):
            x = _float_or_none(point.get("x"))
            y = _float_or_none(point.get("y"))
        elif _is_sequence(point) and len(point) >= 2:
            x = _float_or_none(point[0])
            y = _float_or_none(point[1])
        else:
            continue
        if x is not None and y is not None:
            points.append((x, y))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return _ordered_bbox(min(xs), min(ys), max(xs), max(ys))


def _bbox_from_object(value: Any) -> BBox | None:
    attrs = {
        "x0": _first_attr_number(value, ("x0", "left", "l")),
        "y0": _first_attr_number(value, ("y0", "top", "t")),
        "x1": _first_attr_number(value, ("x1", "right", "r")),
        "y1": _first_attr_number(value, ("y1", "bottom", "b")),
    }
    if all(item is not None for item in attrs.values()):
        return _ordered_bbox(attrs["x0"], attrs["y0"], attrs["x1"], attrs["y1"])  # type: ignore[arg-type]
    for key in _BBOX_KEYS:
        bbox = coerce_bbox(getattr(value, key, None))
        if bbox is not None:
            return bbox
    return None


def _ordered_bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(
        x0=min(x0, x1),
        y0=min(y0, y1),
        x1=max(x0, x1),
        y1=max(y0, y1),
    )


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_number_like(value: Any) -> bool:
    return _float_or_none(value) is not None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(value: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _float_or_none(value.get(key))
        if number is not None:
            return number
    return None


def _first_attr_number(value: Any, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _float_or_none(getattr(value, key, None))
        if number is not None:
            return number
    return None
