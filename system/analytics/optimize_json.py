#!/usr/bin/env python3
"""Safely summarize large JSON tool responses from stdin.

This utility never fetches URLs, reads arbitrary paths, or makes network requests.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema(value: Any, depth: int = 0, max_depth: int = 5) -> Any:
    if depth >= max_depth:
        return _type_name(value)
    if isinstance(value, dict):
        return {str(k): _schema(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        first = value[0]
        return [_schema(first, depth + 1, max_depth)]
    return _type_name(value)


def _clean(value: Any, *, depth: int, max_depth: int, sample_size: int, threshold: int) -> Any:
    if depth >= max_depth:
        return f"<{_type_name(value)}>"
    if isinstance(value, dict):
        cleaned = {
            key: _clean(item, depth=depth + 1, max_depth=max_depth,
                        sample_size=sample_size, threshold=threshold)
            for key, item in value.items() if not _empty(item)
        }
        return cleaned
    if isinstance(value, list):
        if len(value) >= threshold:
            return {
                "_count": len(value),
                "_schema": _schema(value[0], max_depth=max_depth - depth),
                "_sample": [
                    _clean(item, depth=depth + 1, max_depth=max_depth,
                           sample_size=sample_size, threshold=threshold)
                    for item in value[:sample_size]
                ],
                "_note": "Large array summarized; select fields before expanding."
            }
        output = []
        seen = set()
        for item in value:
            if _empty(item):
                continue
            cleaned = _clean(item, depth=depth + 1, max_depth=max_depth,
                             sample_size=sample_size, threshold=threshold)
            fingerprint = json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)
            if fingerprint not in seen:
                seen.add(fingerprint)
                output.append(cleaned)
        return output
    return value


def _select(value: Any, expression: str | None) -> Any:
    """Support a small safe JSONPath subset: $.a.b, $[*].name, and numeric indexes."""
    if not expression:
        return value
    if not expression.startswith("$"):
        raise ValueError("Path must start with '$'.")
    tokens: list[str] = []
    rest = expression[1:]
    while rest:
        if rest.startswith(".."):
            raise ValueError("Recursive JSONPath is not supported.")
        if rest.startswith("."):
            rest = rest[1:]
            token = rest.split(".", 1)[0].split("[", 1)[0]
            if not token or any(c in token for c in "?*"):
                raise ValueError("Unsupported JSONPath expression.")
            tokens.append(token)
            rest = rest[len(token):]
        elif rest.startswith("[*]"):
            tokens.append("*")
            rest = rest[3:]
        elif rest.startswith("[") and "]" in rest:
            token, rest = rest[1:].split("]", 1)
            if not token.isdigit():
                raise ValueError("Only numeric array indexes are supported in brackets.")
            tokens.append(token)
        else:
            raise ValueError("Unsupported JSONPath expression.")
    current = [value]
    for token in tokens:
        next_values = []
        for item in current:
            if token == "*":
                if isinstance(item, list):
                    next_values.extend(item)
            elif isinstance(item, dict) and token in item:
                next_values.append(item[token])
            elif isinstance(item, list) and token.isdigit() and int(token) < len(item):
                next_values.append(item[int(token)])
        current = next_values
    return current[0] if len(current) == 1 else current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", help="Optional safe JSONPath subset, e.g. '$[*].name'.")
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--sample-size", type=int, default=2)
    parser.add_argument("--array-threshold", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=20000)
    parser.add_argument("--stats", action="store_true", help="Print input/output character counts to stderr.")
    args = parser.parse_args()

    if args.max_depth < 1 or args.sample_size < 1 or args.array_threshold < 1 or args.max_chars < 1000:
        parser.error("Numeric limits must be positive; --max-chars must be at least 1000.")
    raw = sys.stdin.read()
    if not raw.strip():
        parser.error("No JSON received on stdin.")
    try:
        data = json.loads(raw)
        selected = _select(data, args.path)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    result = _clean(selected, depth=0, max_depth=args.max_depth,
                    sample_size=args.sample_size, threshold=args.array_threshold)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if len(rendered) > args.max_chars:
        rendered = json.dumps({
            "_schema": _schema(result, max_depth=args.max_depth),
            "_characters": len(json.dumps(result, ensure_ascii=False, default=str)),
            "_note": "Output exceeds --max-chars; rerun with --path to select only required fields."
        }, ensure_ascii=False, indent=2)
    print(rendered)
    if args.stats:
        print(f"input_chars={len(raw)} output_chars={len(rendered)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
