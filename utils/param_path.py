import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional


ACTION_SEGMENTS = {
    "add", "create", "new", "save", "insert", "register",
    "update", "edit", "modify", "patch",
    "delete", "remove", "del",
    "list", "search", "query", "page", "pages", "index",
    "detail", "details", "get", "set",
}

IDENTIFIER_TOKENS = {"id", "uuid", "guid", "identifier"}


@dataclass(frozen=True)
class ParameterOccurrence:
    name: str
    location: str
    canonical_path: str
    structural_level: int
    resource_level: int
    endpoint: str
    method: str
    api_type: str


def split_endpoint(endpoint: str) -> tuple[str, str]:
    parts = str(endpoint or "").strip().split(None, 1)
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]+", parts[0]):
        return parts[0].upper(), parts[1]
    return "", str(endpoint or "").strip()


def canonical_param_path(name: Any) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[(\d+)\]", "[]", text)
    text = text.replace("[].", "[].")
    parts = [p for p in text.split(".") if p]
    return ".".join(parts)


def path_segments(name: Any) -> List[str]:
    canonical = canonical_param_path(name)
    if not canonical:
        return []
    return [p for p in canonical.split(".") if p]


def base_param_name(name: Any) -> str:
    segments = path_segments(name)
    if not segments:
        return ""
    return segments[-1].replace("[]", "")


def _segment_base(segment: Any) -> str:
    return str(segment or "").replace("[]", "").strip("{}")


def structural_level(name: Any, location: str = "") -> int:
    segments = path_segments(name)
    if not segments:
        return 1
    return max(1, len(segments))


def to_snake_case(name: Any) -> str:
    base = base_param_name(name)
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", base)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.replace("-", "_").lower()


def compact_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", to_snake_case(name))


def param_variants(name: Any) -> set[str]:
    base = base_param_name(name)
    snake = to_snake_case(base)
    compact = snake.replace("_", "")
    parts = [p for p in snake.split("_") if p]
    camel = parts[0] + "".join(p.capitalize() for p in parts[1:]) if parts else snake
    return {v for v in {base, snake, compact, camel} if v}


def params_equivalent(left: Any, right: Any) -> bool:
    return bool(param_variants(left) & param_variants(right))


def is_identifier_name(name: Any) -> bool:
    base = base_param_name(name)
    snake = to_snake_case(base)
    compact = snake.replace("_", "")
    if snake in IDENTIFIER_TOKENS or compact in IDENTIFIER_TOKENS:
        return True
    return (
        snake.endswith("_id")
        or snake.endswith("_uuid")
        or snake.endswith("_guid")
        or compact.endswith("id")
        or compact.endswith("uuid")
        or compact.endswith("guid")
    )


def identifier_names_in_path(name: Any) -> list[str]:
    """Return identifier-bearing names from every segment of a canonical path.

    Nested OpenAPI schemas sometimes expose identifiers as an intermediate
    container segment, for example ``authority.dataAuthorityId[].value``.
    Looking only at the leaf would see ``value`` and miss ``dataAuthorityId``.
    """
    found: list[str] = []
    seen: set[str] = set()
    for segment in path_segments(name):
        candidate = _segment_base(segment)
        if candidate and is_identifier_name(candidate) and candidate not in seen:
            found.append(candidate)
            seen.add(candidate)
    return found


def _normalize_anchor(token: str) -> str:
    token = (token or "").lower()
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _identifier_anchor(name: Any) -> str:
    snake = to_snake_case(name)
    for suffix in ("_identifier", "_uuid", "_guid", "_id"):
        if snake.endswith(suffix):
            return _normalize_anchor(snake[: -len(suffix)])
    compact = snake.replace("_", "")
    for suffix in ("identifier", "uuid", "guid", "id"):
        if compact.endswith(suffix) and len(compact) > len(suffix):
            return _normalize_anchor(compact[: -len(suffix)])
    return ""


def param_matches(candidate: Any, field: Any) -> bool:
    if str(candidate) == str(field):
        return True
    if params_equivalent(candidate, field):
        return True

    cand_base = base_param_name(candidate)
    field_segments = path_segments(field)
    field_bases = [seg.replace("[]", "") for seg in field_segments]
    if cand_base and cand_base.lower() in {seg.lower() for seg in field_bases}:
        return True

    anchor = _identifier_anchor(candidate)
    if anchor and field_bases:
        last = field_bases[-1].lower()
        if last in IDENTIFIER_TOKENS or last in {"code", "name"}:
            for seg in field_bases[:-1]:
                if _normalize_anchor(seg) == anchor:
                    return True
    return False


def any_param_matches(candidate: Any, fields: Iterable[Any]) -> bool:
    return any(param_matches(candidate, field) for field in fields or [])


def parse_path_identifier_order(endpoint: str) -> list[tuple[str, int]]:
    _, route = split_endpoint(endpoint)
    route_only = route.split("?", 1)[0]
    raw_segments = [s for s in route_only.split("/") if s]
    resource_level = 0
    result: list[tuple[str, int]] = []
    for seg in raw_segments:
        stripped = seg.strip("{}")
        lowered = stripped.lower()
        if lowered in ACTION_SEGMENTS:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            result.append((stripped, max(resource_level, 1)))
            continue
        if not re.fullmatch(r"v\d+(\.\d+)?", lowered) and lowered not in {"api", "apis"}:
            resource_level += 1
    return result


def path_container_params(endpoint: str) -> set[str]:
    ids = parse_path_identifier_order(endpoint)
    containers = set()
    for idx, (name, _) in enumerate(ids):
        if idx < len(ids) - 1:
            containers.add(name)
    return containers


def occurrence_for(
    name: Any,
    location: str,
    endpoint: str,
    api_type: str = "",
    occurrence_name: Optional[str] = None,
) -> ParameterOccurrence:
    method, _ = split_endpoint(endpoint)
    canonical = canonical_param_path(name)
    resource_level = 1
    if location == "path":
        for p_name, level in parse_path_identifier_order(endpoint):
            if params_equivalent(name, p_name):
                resource_level = level
                break
    return ParameterOccurrence(
        name=occurrence_name or base_param_name(name) or str(name),
        location=location,
        canonical_path=canonical,
        structural_level=structural_level(name, location),
        resource_level=resource_level,
        endpoint=endpoint,
        method=method,
        api_type=api_type or "",
    )


def _iter_child_matches(container: Any, key: str):
    if isinstance(container, dict):
        if key in container:
            yield container, key
    elif isinstance(container, list):
        for item in container:
            yield from _iter_child_matches(item, key)


def nested_get(container: Any, param_path: Any, default: Any = None) -> Any:
    segments = path_segments(param_path)
    if not segments:
        return default
    curs = [container]
    for raw in segments:
        key = raw.replace("[]", "")
        next_curs = []
        for cur in curs:
            for parent, child_key in _iter_child_matches(cur, key):
                value = parent[child_key]
                if raw.endswith("[]") and isinstance(value, list):
                    next_curs.extend(value)
                else:
                    next_curs.append(value)
        curs = next_curs
        if not curs:
            return default
    return curs[0] if len(curs) == 1 else curs


def nested_set(container: Any, param_path: Any, value: Any) -> bool:
    segments = path_segments(param_path)
    if not segments or not isinstance(container, (dict, list)):
        return False
    cur = container
    for idx, raw in enumerate(segments):
        key = raw.replace("[]", "")
        is_last = idx == len(segments) - 1
        is_array = raw.endswith("[]")
        if isinstance(cur, list):
            if not cur:
                cur.append({})
            cur = cur[0]
        if not isinstance(cur, dict):
            return False
        if is_last:
            cur[key] = [value] if is_array else value
            return True
        if key not in cur or cur[key] in (None, ""):
            cur[key] = [{}] if is_array else {}
        cur = cur[key][0] if is_array and isinstance(cur[key], list) else cur[key]
    return False


def nested_delete(container: Any, param_path: Any) -> bool:
    segments = path_segments(param_path)
    if not segments:
        return False
    cur = container
    for raw in segments[:-1]:
        key = raw.replace("[]", "")
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    if isinstance(cur, list):
        cur = cur[0] if cur else None
    if isinstance(cur, dict):
        key = segments[-1].replace("[]", "")
        if key in cur:
            del cur[key]
            return True
    return False


def find_matching_path(container: Any, aliases: Iterable[str]) -> Optional[str]:
    alias_set = set()
    for alias in aliases or []:
        alias_set.update(param_variants(alias))

    def walk(node: Any, prefix: list[str]) -> Optional[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                current = prefix + [str(key)]
                if param_matches(key, key) and (param_variants(key) & alias_set):
                    return ".".join(current)
                found = walk(value, current)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item, prefix[:-1] + [prefix[-1] + "[]"] if prefix else ["[]"])
                if found:
                    return found
        return None

    return walk(container, [])
