#!/usr/bin/env python3
"""Validate and digest Collaborative Development Workflow ContractV2 records.

The helper intentionally uses only the Python standard library. The JSON file in
../references/contracts-v2.json is authoritative for enums, limits, and record
field names; this module contains only the small validation vocabulary needed to
interpret that declaration.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = SCRIPT_DIR.parent / "references" / "contracts-v2.json"
SENTINELS: set[str] = set()
TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
MONOTONIC_TIMESTAMP_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64


class ContractError(ValueError):
    """An invalid input or contract record."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ContractError(f"non-finite JSON number is not allowed: {value}")


def _input_limit() -> int:
    contract = globals().get("CONTRACT")
    return int(contract["limits"]["json_bytes"]) if contract is not None else MAX_INPUT_BYTES


def _check_json_depth(value: Any, *, max_depth: int | None = None) -> None:
    if max_depth is None:
        contract = globals().get("CONTRACT")
        max_depth = int(contract["limits"]["json_depth"]) if contract is not None else MAX_JSON_DEPTH
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > max_depth:
            raise ContractError(f"JSON nesting exceeds {max_depth} levels")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _reject_path_argument_traversal(value: str, *, field: str) -> None:
    candidate = value.replace("\\", "/")
    if any(piece in {".", ".."} for piece in candidate.split("/")):
        raise ContractError(f"{field} must not contain '.' or '..' path components")


def _require_descriptor_file_support() -> None:
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    if (
        os.name != "posix"
        or os.open not in supports_dir_fd
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
    ):
        raise ContractError("safe input loading requires POSIX descriptor-relative file support")


def _read_file_bytes(path: str) -> bytes:
    _reject_path_argument_traversal(path, field="input path")
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or len(absolute.parts) < 2:
        raise ContractError("input path must be a non-root absolute path")
    _require_descriptor_file_support()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_descriptor = os.open(os.path.sep, directory_flags)
    try:
        for component in absolute.parts[1:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        file_descriptor = os.open(
            absolute.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor
        )
        try:
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ContractError(f"{path}: input is not a regular file")
            limit = _input_limit()
            if before.st_size > limit:
                raise ContractError(f"{path}: input exceeds {limit} bytes")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(file_descriptor, min(1024 * 1024, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ContractError(f"{path}: input exceeds {limit} bytes")
            after = os.fstat(file_descriptor)
            data = b"".join(chunks)
            if before.st_size != len(data) or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ContractError(f"{path}: input changed while reading")
            return data
        finally:
            os.close(file_descriptor)
    except (ContractError, OSError, UnicodeError):
        raise
    except ValueError as exc:
        raise ContractError(f"{path}: input path is invalid") from exc
    finally:
        os.close(directory_descriptor)


def load_json_bytes(raw: bytes, *, source: str = "input") -> Any:
    if len(raw) > _input_limit():
        raise ContractError(f"{source}: input exceeds {_input_limit()} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{source}: input is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            raise ContractError(f"{source}: JSON nesting is too deep") from exc
        raise ContractError(f"{source}: invalid JSON: {exc.msg}") from exc
    _check_json_depth(value)
    return value


def load_json_file(path: str) -> Any:
    if path == "-":
        limit = _input_limit()
        return load_json_bytes(sys.stdin.buffer.read(limit + 1), source="stdin")
    try:
        return load_json_bytes(_read_file_bytes(path), source=path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractError(f"{path}: cannot read input: {exc}") from exc


def _load_contract() -> dict[str, Any]:
    value = load_json_file(str(CONTRACT_PATH))
    if not isinstance(value, dict) or value.get("version") != "contract-v2":
        raise ContractError("contracts-v2.json is not a ContractV2 definition")
    return _prepare_contract(value)


_FIELD_SPEC_TYPES = {
    "acceptance_list",
    "active_work_list",
    "artifact_list",
    "baseline",
    "boolean",
    "bounded_text_or_list",
    "capability_map",
    "check_list",
    "checkpoint_or_none",
    "const",
    "decision_list",
    "enum",
    "enum_ref",
    "enum_ref_or_none",
    "finding_list",
    "focused_check_list",
    "identifier",
    "identifier_or_none",
    "model_profile",
    "monotonic_timestamp",
    "monotonic_timestamp_or_none",
    "path",
    "path_list",
    "plan_list",
    "positive_integer",
    "record",
    "record_list",
    "reference_list",
    "risk_list",
    "role_payload",
    "role_status",
    "runtime_event_list",
    "sha256",
    "sha256_or_none",
    "text",
    "text_list",
    "text_or_none",
    "token",
    "token_list",
}


def _contract_ref(root: dict[str, Any], reference: Any, *, field: str) -> Any:
    if not isinstance(reference, str) or not reference:
        raise ContractError(f"{field} must be a non-empty dotted contract reference")
    current: Any = root
    for part in reference.split("."):
        if not part or not isinstance(current, dict) or part not in current:
            raise ContractError(f"{field} references an unknown contract path: {reference}")
        current = current[part]
    return current


def _validate_field_spec(root: dict[str, Any], spec: Any, field: str) -> None:
    if not isinstance(spec, dict):
        raise ContractError(f"{field} must be a field specification object")
    allowed_keys = {
        "type",
        "value",
        "ref",
        "kind",
        "values",
        "max_bytes",
        "max_bytes_ref",
        "max_items",
        "min_items",
        "set_like",
    }
    unknown = sorted(set(spec) - allowed_keys)
    if unknown:
        raise ContractError(f"{field} has unknown key(s): {', '.join(unknown)}")
    kind = spec.get("type")
    if kind not in _FIELD_SPEC_TYPES:
        raise ContractError(f"{field}.type is not a supported ContractV2 field type: {kind!r}")
    if "max_bytes_ref" in spec:
        target = _contract_ref(root, spec["max_bytes_ref"], field=f"{field}.max_bytes_ref")
        if isinstance(target, bool) or not isinstance(target, int) or target < 0:
            raise ContractError(f"{field}.max_bytes_ref must resolve to a non-negative integer")
    if "ref" in spec:
        target = _contract_ref(root, spec["ref"], field=f"{field}.ref")
        if not isinstance(target, list):
            raise ContractError(f"{field}.ref must resolve to a list")
    if kind in {"record", "record_list"}:
        target = spec.get("kind")
        if not isinstance(target, str) or target not in root.get("records", {}):
            raise ContractError(f"{field}.kind references an unknown record: {target!r}")
    if kind == "const" and "value" not in spec:
        raise ContractError(f"{field}.value is required for const fields")
    if kind == "enum" and not isinstance(spec.get("values"), list):
        raise ContractError(f"{field}.values is required for enum fields")
    if "max_items" in spec:
        maximum = spec["max_items"]
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ContractError(f"{field}.max_items must be a non-negative integer")
    if "min_items" in spec:
        minimum = spec["min_items"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ContractError(f"{field}.min_items must be a non-negative integer")


def _validate_declared_schema(root: dict[str, Any], schema: Any, field: str, *, allow_mixins: bool) -> None:
    if not isinstance(schema, dict):
        raise ContractError(f"{field} must be an object")
    allowed = {"required", "fields", "min_properties"}
    if allow_mixins:
        allowed.add("mixins")
    unknown = sorted(set(schema) - allowed)
    if unknown:
        raise ContractError(f"{field} has unknown declaration key(s): {', '.join(unknown)}")
    required = schema.get("required", [])
    fields = schema.get("fields", {})
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise ContractError(f"{field}.required must be a list of field names")
    if len(required) != len(set(required)):
        raise ContractError(f"{field}.required contains duplicate field names")
    if not isinstance(fields, dict):
        raise ContractError(f"{field}.fields must be an object")
    if len(fields) != len(set(fields)):
        raise ContractError(f"{field}.fields contains duplicate field names")
    for name, child_spec in fields.items():
        if not isinstance(name, str) or not name:
            raise ContractError(f"{field}.fields contains an invalid field name")
        _validate_field_spec(root, child_spec, f"{field}.fields.{name}")
    unknown_required = sorted(set(required) - set(fields))
    if unknown_required:
        raise ContractError(f"{field}.required references undeclared field(s): {', '.join(unknown_required)}")
    if "min_properties" in schema:
        minimum = schema["min_properties"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise ContractError(f"{field}.min_properties must be a non-negative integer")
    if allow_mixins:
        mixins = schema.get("mixins", [])
        if not isinstance(mixins, list) or any(not isinstance(name, str) for name in mixins):
            raise ContractError(f"{field}.mixins must be a list of names")
        if len(mixins) != len(set(mixins)):
            raise ContractError(f"{field}.mixins contains duplicate names")


def _resolve_mixins(root: dict[str, Any]) -> dict[str, dict[str, Any]]:
    field_sets = root.get("field_sets")
    raw_mixins = root.get("mixins")
    if not isinstance(field_sets, dict) or not isinstance(raw_mixins, dict):
        raise ContractError("field_sets and mixins must be objects")
    mixins: dict[str, dict[str, Any]] = {}
    for name, declaration in raw_mixins.items():
        if isinstance(declaration, list):
            declaration = {"field_sets": declaration}
        if not isinstance(declaration, dict):
            raise ContractError(f"mixins.{name} must be an object or field-set list")
        mixins[name] = declaration
    for name, schema in field_sets.items():
        _validate_declared_schema(root, schema, f"field_sets.{name}", allow_mixins=False)
    for name, declaration in mixins.items():
        unknown = sorted(set(declaration) - {"field_sets", "mixins"})
        if unknown:
            raise ContractError(f"mixins.{name} has unknown key(s): {', '.join(unknown)}")
        for key in ("field_sets", "mixins"):
            values = declaration.get(key, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ContractError(f"mixins.{name}.{key} must be a list of names")
            if len(values) != len(set(values)):
                raise ContractError(f"mixins.{name}.{key} contains duplicate names")

    resolved: dict[str, dict[str, Any]] = {}
    visiting: list[str] = []

    def merge(target: dict[str, Any], source: dict[str, Any], origin: str) -> None:
        for name, spec in source["fields"].items():
            if name in target["fields"]:
                raise ContractError(f"field conflict while expanding {origin}: {name}")
            target["fields"][name] = copy.deepcopy(spec)
        for name in source["required"]:
            if name in target["required"]:
                raise ContractError(f"required field conflict while expanding {origin}: {name}")
            target["required"].append(name)

    def resolve(name: str) -> dict[str, Any]:
        if name in resolved:
            return copy.deepcopy(resolved[name])
        if name in visiting:
            cycle = " -> ".join([*visiting, name])
            raise ContractError(f"mixin cycle detected: {cycle}")
        if name not in mixins:
            raise ContractError(f"unknown mixin reference: {name}")
        visiting.append(name)
        result = {"required": [], "fields": {}}
        declaration = mixins[name]
        for field_set_name in declaration.get("field_sets", []):
            if field_set_name not in field_sets:
                raise ContractError(f"mixin {name} references unknown field set: {field_set_name}")
            merge(result, field_sets[field_set_name], f"mixin {name}")
        for child_name in declaration.get("mixins", []):
            merge(result, resolve(child_name), f"mixin {name}")
        visiting.pop()
        resolved[name] = copy.deepcopy(result)
        return result

    for name in mixins:
        resolve(name)
    return resolved


def _expand_records(root: dict[str, Any], resolved_mixins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records = root.get("records")
    if not isinstance(records, dict):
        raise ContractError("records must be an object")
    expanded = copy.deepcopy(root)
    expanded_records: dict[str, Any] = {}
    for name, schema in records.items():
        _validate_declared_schema(root, schema, f"records.{name}", allow_mixins=True)
        record = copy.deepcopy(schema)
        record_mixins = record.pop("mixins", [])
        fields: dict[str, Any] = {}
        required: list[str] = []
        for mixin_name in record_mixins:
            if mixin_name not in resolved_mixins:
                raise ContractError(f"record {name} references unknown mixin: {mixin_name}")
            merge_target = resolved_mixins[mixin_name]
            for field_name, spec in merge_target["fields"].items():
                if field_name in fields:
                    raise ContractError(f"field conflict while expanding record {name}: {field_name}")
                fields[field_name] = copy.deepcopy(spec)
            for field_name in merge_target["required"]:
                if field_name in required:
                    raise ContractError(f"required field conflict while expanding record {name}: {field_name}")
                required.append(field_name)
        for field_name, spec in record.get("fields", {}).items():
            if field_name in fields:
                raise ContractError(f"field conflict while expanding record {name}: {field_name}")
            fields[field_name] = spec
        for field_name in record.get("required", []):
            if field_name in required:
                raise ContractError(f"required field conflict while expanding record {name}: {field_name}")
            required.append(field_name)
        record["fields"] = fields
        record["required"] = required
        _validate_declared_schema(expanded, record, f"records.{name}", allow_mixins=False)
        expanded_records[name] = record
    expanded["records"] = expanded_records
    return expanded


def _validate_contract_references(root: dict[str, Any]) -> None:
    required_top_level = {
        "contract", "version", "canonicalization", "sentinels", "limits", "statuses",
        "runtime_events", "modes", "artifact_contracts", "field_sets", "mixins",
        "nested_records", "records",
    }
    unknown = sorted(set(root) - required_top_level)
    if unknown:
        raise ContractError(f"contract has unknown top-level key(s): {', '.join(unknown)}")
    if root.get("contract") != "collaborative-development-workflow":
        raise ContractError("contract.contract has an unexpected value")
    if not isinstance(root.get("sentinels"), list) or len(root["sentinels"]) != len(set(root["sentinels"])):
        raise ContractError("contract.sentinels must be a unique list")
    if not isinstance(root.get("limits"), dict):
        raise ContractError("contract.limits must be an object")
    for name, limit in root["limits"].items():
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ContractError(f"limits.{name} must be a non-negative integer")
    modes = root.get("modes")
    if not isinstance(modes, dict) or set(modes) != {"values", "required_capabilities"}:
        raise ContractError("modes has an invalid closed shape")
    if modes["values"] != ["portable", "strict"]:
        raise ContractError("modes.values must declare portable and strict")
    capabilities = modes["required_capabilities"]
    if not isinstance(capabilities, dict) or set(capabilities) != {"portable", "strict_additional"}:
        raise ContractError("modes.required_capabilities has an invalid closed shape")
    for name, values in capabilities.items():
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ContractError(f"modes.required_capabilities.{name} must be a unique list")
        for capability in values:
            if not isinstance(capability, str) or not TOKEN_RE.fullmatch(capability):
                raise ContractError(f"invalid capability name: {capability!r}")
    portable = set(capabilities["portable"])
    if portable & set(capabilities["strict_additional"]):
        raise ContractError("strict_additional capabilities overlap portable capabilities")
    artifacts = root.get("artifact_contracts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"snapshot_manifest"}:
        raise ContractError("artifact_contracts has an invalid closed shape")
    manifest = artifacts["snapshot_manifest"]
    if not isinstance(manifest, dict):
        raise ContractError("artifact_contracts.snapshot_manifest must be an object")
    manifest_keys = {"version", "fields", "git_identity_fields", "entry", "baseline_types", "top_level", "limits"}
    if set(manifest) != manifest_keys:
        raise ContractError("artifact_contracts.snapshot_manifest has an invalid closed shape")
    for name in ("fields", "git_identity_fields", "baseline_types", "top_level"):
        values = manifest[name]
        if not isinstance(values, list) or len(values) != len(set(values)) or any(not isinstance(item, str) for item in values):
            raise ContractError(f"artifact_contracts.snapshot_manifest.{name} must be a unique string list")
    entry = manifest["entry"]
    if not isinstance(entry, dict) or set(entry) != {"fields", "statuses", "types", "artifact_prefixes"}:
        raise ContractError("artifact_contracts.snapshot_manifest.entry has an invalid closed shape")
    for name in ("fields", "statuses", "types"):
        values = entry[name]
        if not isinstance(values, list) or len(values) != len(set(values)) or any(not isinstance(item, str) for item in values):
            raise ContractError(f"artifact_contracts.snapshot_manifest.entry.{name} must be a unique string list")
    prefixes = entry["artifact_prefixes"]
    if not isinstance(prefixes, dict) or set(prefixes) != {"files", "baseline"} or any(
        not isinstance(item, str) or not item for item in prefixes.values()
    ) or len(set(prefixes.values())) != len(prefixes):
        raise ContractError("artifact_contracts.snapshot_manifest.entry.artifact_prefixes is invalid")
    if not isinstance(manifest["version"], str) or not manifest["version"]:
        raise ContractError("artifact_contracts.snapshot_manifest.version must be non-empty text")
    limits = manifest["limits"]
    expected_limit_keys = {
        "max_entries",
        "snapshot_depth_ref",
        "symlink_target_bytes_ref",
        "manifest_bytes_ref",
        "file_bytes_ref",
        "total_bytes_ref",
        "git_output_bytes_ref",
    }
    if not isinstance(limits, dict) or set(limits) != expected_limit_keys:
        raise ContractError("artifact_contracts.snapshot_manifest.limits has an invalid closed shape")
    max_entries = limits["max_entries"]
    if not isinstance(max_entries, dict) or set(max_entries) != {"limit_ref", "multiplier"}:
        raise ContractError("artifact_contracts.snapshot_manifest.limits.max_entries is invalid")
    _contract_ref(root, max_entries["limit_ref"], field="artifact manifest max_entries.limit_ref")
    if isinstance(max_entries["multiplier"], bool) or not isinstance(max_entries["multiplier"], int) or max_entries["multiplier"] < 1:
        raise ContractError("artifact manifest max_entries.multiplier must be positive")
    for name in (
        "snapshot_depth_ref",
        "symlink_target_bytes_ref",
        "manifest_bytes_ref",
        "file_bytes_ref",
        "total_bytes_ref",
        "git_output_bytes_ref",
    ):
        resolved_limit = _contract_ref(root, limits[name], field=f"artifact manifest {name}")
        if isinstance(resolved_limit, bool) or not isinstance(resolved_limit, int) or resolved_limit < 0:
            raise ContractError(f"artifact manifest {name} must resolve to a non-negative integer")
    runtime = root.get("runtime_events")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("record_kinds"), dict):
        raise ContractError("runtime_events.record_kinds must be an object")
    for event_type, kind in runtime["record_kinds"].items():
        if not isinstance(event_type, str) or not isinstance(kind, str) or kind not in root["records"]:
            raise ContractError(f"runtime event mapping has an invalid record kind: {event_type!r} -> {kind!r}")
    statuses = root.get("statuses")
    if not isinstance(statuses, dict):
        raise ContractError("statuses must be an object")
    roles = statuses.get("roles")
    role_success = statuses.get("role_success")
    if not isinstance(roles, list) or not isinstance(role_success, dict) or set(role_success) - set(roles):
        raise ContractError("statuses.role_success references an undeclared role")
    reasons = statuses.get("not_accepted_reasons", [])
    reason_rules = statuses.get("not_accepted_reason_rules", {})
    if not isinstance(reason_rules, dict) or set(reason_rules) - set(reasons):
        raise ContractError("not_accepted_reason_rules references an undeclared reason")
    nested = root.get("nested_records")
    if not isinstance(nested, dict):
        raise ContractError("nested_records must be an object")
    for name, schema in nested.items():
        _validate_declared_schema(root, schema, f"nested_records.{name}", allow_mixins=False)
    for collection_name in ("records",):
        if not isinstance(root.get(collection_name), dict):
            raise ContractError(f"{collection_name} must be an object")


def _prepare_contract(value: dict[str, Any]) -> dict[str, Any]:
    _validate_contract_references(value)
    resolved_mixins = _resolve_mixins(value)
    expanded = _expand_records(value, resolved_mixins)
    for event_type, kind in expanded["runtime_events"]["record_kinds"].items():
        event_spec = expanded["records"][kind]["fields"].get("event_type")
        if not isinstance(event_spec, dict) or event_spec.get("type") != "const" or event_spec.get("value") != event_type:
            raise ContractError(f"runtime event mapping does not match records.{kind}.event_type")
    return expanded


CONTRACT = _load_contract()
SENTINELS = set(CONTRACT["sentinels"])
DOMAINS = {
    key: value.encode("utf-8")
    for key, value in CONTRACT["canonicalization"]["domains"].items()
}
PORTABLE_CAPABILITIES = tuple(CONTRACT["modes"]["required_capabilities"]["portable"])
STRICT_ADDITIONAL_CAPABILITIES = tuple(CONTRACT["modes"]["required_capabilities"]["strict_additional"])
STRICT_CAPABILITIES = PORTABLE_CAPABILITIES + STRICT_ADDITIONAL_CAPABILITIES
ALL_CAPABILITIES = STRICT_CAPABILITIES
RUNTIME_EVENT_KINDS = dict(CONTRACT["runtime_events"]["record_kinds"])


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ContractError(f"value cannot be canonically encoded: {exc}") from exc


def _byte_length(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContractError("text contains an invalid Unicode surrogate") from exc


def normalize_repo_path(value: str) -> str:
    if not isinstance(value, str):
        raise ContractError("path must be a string")
    if not value or "\x00" in value:
        raise ContractError("path must be non-empty and contain no NUL")
    candidate = value.replace("\\", "/")
    if candidate.startswith("/") or DRIVE_RE.match(candidate):
        raise ContractError(f"absolute path is not allowed: {value!r}")
    pieces: list[str] = []
    for piece in candidate.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            raise ContractError(f"parent traversal is not allowed: {value!r}")
        pieces.append(piece)
    if not pieces:
        raise ContractError(f"path has no repository-relative components: {value!r}")
    normalized = "/".join(pieces)
    if _byte_length(normalized) > CONTRACT["limits"]["path_bytes"]:
        raise ContractError(f"path exceeds {CONTRACT['limits']['path_bytes']} UTF-8 bytes")
    return normalized


def normalize_scope_reference(value: str) -> str:
    """Normalize a reference when its shape is a repository path."""
    value = _text(
        value,
        max_bytes=CONTRACT["limits"]["path_bytes"],
        field="scope reference",
    )
    if value in SENTINELS:
        raise ContractError("scope reference cannot use a reserved sentinel")
    candidate = value.replace("\\", "/")
    if ".." in candidate.split("/"):
        raise ContractError("scope reference contains parent traversal")
    if candidate.startswith("/") or DRIVE_RE.match(candidate):
        raise ContractError("scope reference must not be absolute")
    if "/" in candidate and "://" not in candidate:
        return normalize_repo_path(candidate)
    return value


def _text(value: Any, *, max_bytes: int, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    if "\x00" in value:
        raise ContractError(f"{field} must not contain NUL")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ContractError(f"{field} contains an invalid Unicode surrogate")
    size = _byte_length(value)
    if not allow_empty and size == 0:
        raise ContractError(f"{field} must not be empty")
    if size > max_bytes:
        raise ContractError(f"{field} exceeds {max_bytes} UTF-8 bytes")
    return value


def _token(value: Any, *, max_bytes: int, field: str) -> str:
    value = _text(value, max_bytes=max_bytes, field=field)
    if value in SENTINELS or not TOKEN_RE.fullmatch(value):
        raise ContractError(f"{field} is not a permitted token")
    return value


def _identifier(value: Any, *, allow_none: bool, field: str) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ContractError(f"{field} must be an identifier")
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string or null")
    if value in SENTINELS:
        if allow_none:
            return value
        raise ContractError(f"{field} cannot use a reserved sentinel")
    return _text(value, max_bytes=CONTRACT["limits"]["token_bytes"], field=field)


def _enum_ref(name: str) -> list[str]:
    current: Any = CONTRACT
    for part in name.split("."):
        current = current[part]
    if not isinstance(current, list):
        raise ContractError(f"contract enum reference is not a list: {name}")
    return current


def _spec_limit(spec: dict[str, Any], key: str, default: int) -> int:
    direct = spec.get(key)
    if direct is not None:
        return int(direct)
    reference = spec.get(f"{key}_ref")
    if reference is None:
        return default
    current: Any = CONTRACT
    for part in reference.split("."):
        current = current[part]
    return int(current)


def _validate_list(
    value: Any,
    *,
    max_items: int,
    field: str,
    item_validator: Any,
    unique: bool = False,
) -> None:
    if not isinstance(value, list):
        raise ContractError(f"{field} must be a list")
    if len(value) > max_items:
        raise ContractError(f"{field} has more than {max_items} entries")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if unique:
            try:
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"))
            except TypeError as exc:
                raise ContractError(f"{field}[{index}] is not comparable") from exc
            if marker in seen:
                raise ContractError(f"{field} contains a duplicate entry at index {index}")
            seen.add(marker)
        item_validator(item, f"{field}[{index}]")


def _require_unique_item_key(value: list[Any], key: str, field: str) -> None:
    """Reject distinct list entries that reuse one semantic identifier."""
    seen: set[Any] = set()
    for index, item in enumerate(value):
        marker = item[key]
        if marker in seen:
            raise ContractError(f"{field} contains duplicate {key} at index {index}")
        seen.add(marker)


def _simple_text_list(value: Any, *, max_items: int, max_bytes: int, field: str) -> None:
    _validate_list(
        value,
        max_items=max_items,
        field=field,
        item_validator=lambda item, item_field: _text(
            item, max_bytes=max_bytes, field=item_field
        ),
    )


def _path_list(value: Any, *, max_items: int, field: str) -> None:
    normalized: list[str] = []

    def validate(item: Any, item_field: str) -> None:
        del item_field
        normalized.append(normalize_repo_path(item))

    _validate_list(value, max_items=max_items, field=field, item_validator=validate)
    if len(set(normalized)) != len(normalized):
        raise ContractError(f"{field} contains duplicate paths after normalization")


def _reference_list(value: Any, *, max_items: int, field: str) -> None:
    normalized: list[str] = []

    def validate(item: Any, item_field: str) -> None:
        try:
            normalized.append(normalize_scope_reference(item))
        except ContractError as exc:
            raise ContractError(f"{item_field}: {exc}") from exc

    _validate_list(value, max_items=max_items, field=field, item_validator=validate)
    if len(set(normalized)) != len(normalized):
        raise ContractError(f"{field} contains duplicate entries")


def _validate_object(value: Any, spec: dict[str, Any], field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object")
    fields = spec.get("fields", {})
    required = set(spec.get("required", []))
    unknown = sorted(set(value) - set(fields))
    if unknown:
        raise ContractError(f"{field} has unknown field(s): {', '.join(unknown)}")
    missing = sorted(required - set(value))
    if missing:
        raise ContractError(f"{field} is missing required field(s): {', '.join(missing)}")
    for key, child_spec in fields.items():
        if key in value:
            _validate_value(value[key], child_spec, f"{field}.{key}", errors)


def _validate_value(value: Any, spec: dict[str, Any], field: str, errors: list[str]) -> None:
    kind = spec.get("type")
    if kind == "const":
        if value != spec.get("value"):
            raise ContractError(f"{field} must equal {spec.get('value')!r}")
    elif kind == "monotonic_timestamp":
        if not isinstance(value, str) or not MONOTONIC_TIMESTAMP_RE.fullmatch(value):
            raise ContractError(f"{field} must be a non-negative finite monotonic timestamp")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ContractError(f"{field} is not a valid monotonic timestamp") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ContractError(f"{field} must be a non-negative finite monotonic timestamp")
    elif kind == "monotonic_timestamp_or_none":
        if value is not None:
            _validate_value(value, {"type": "monotonic_timestamp"}, field, errors)
    elif kind == "text":
        _text(value, max_bytes=_spec_limit(spec, "max_bytes", CONTRACT["limits"]["text_bytes"]), field=field)
    elif kind == "text_or_none":
        if value is not None:
            _text(value, max_bytes=_spec_limit(spec, "max_bytes", CONTRACT["limits"]["text_bytes"]), field=field)
    elif kind == "token":
        _token(value, max_bytes=_spec_limit(spec, "max_bytes", CONTRACT["limits"]["token_bytes"]), field=field)
    elif kind == "identifier":
        _identifier(value, allow_none=False, field=field)
    elif kind == "identifier_or_none":
        _identifier(value, allow_none=True, field=field)
    elif kind == "sha256":
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ContractError(f"{field} must be a lowercase SHA-256 digest")
    elif kind == "sha256_or_none":
        if value is not None and (not isinstance(value, str) or not SHA256_RE.fullmatch(value)):
            raise ContractError(f"{field} must be a lowercase SHA-256 digest or null")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ContractError(f"{field} must be a boolean")
    elif kind == "positive_integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{field} must be a positive integer")
    elif kind == "enum":
        if value not in spec.get("values", []):
            raise ContractError(f"{field} is not one of the permitted values")
    elif kind == "enum_ref":
        if value not in _enum_ref(spec["ref"]):
            raise ContractError(f"{field} is not in {spec['ref']}")
    elif kind == "enum_ref_or_none":
        if value is not None and value not in _enum_ref(spec["ref"]):
            raise ContractError(f"{field} is not in {spec['ref']} or null")
    elif kind == "path":
        normalize_repo_path(value)
    elif kind == "path_list":
        _path_list(value, max_items=spec.get("max_items", CONTRACT["limits"]["scope_entries"]), field=field)
    elif kind == "reference_list":
        _reference_list(value, max_items=spec.get("max_items", CONTRACT["limits"]["scope_entries"]), field=field)
    elif kind == "token_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["scope_entries"]),
            field=field,
            item_validator=lambda item, item_field: _token(
                item, max_bytes=CONTRACT["limits"]["token_bytes"], field=item_field
            ),
            unique=spec.get("set_like", False),
        )
    elif kind == "text_list":
        _simple_text_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["scope_entries"]),
            max_bytes=_spec_limit(spec, "max_bytes", CONTRACT["limits"]["text_bytes"]),
            field=field,
        )
    elif kind == "bounded_text_or_list":
        max_bytes = _spec_limit(spec, "max_bytes", CONTRACT["limits"]["summary_bytes"])
        if isinstance(value, str):
            _text(value, max_bytes=max_bytes, field=field)
        else:
            _simple_text_list(value, max_items=spec.get("max_items", 16), max_bytes=max_bytes, field=field)
    elif kind == "capability_map":
        if not isinstance(value, dict):
            raise ContractError(f"{field} must be an object")
        unknown = sorted(set(value) - set(ALL_CAPABILITIES))
        if unknown:
            raise ContractError(f"{field} has unknown capability keys: {','.join(unknown)}")
        if any(not isinstance(item, bool) for item in value.values()):
            raise ContractError(f"{field} values must be booleans")
    elif kind == "role_status":
        if not isinstance(value, str):
            raise ContractError(f"{field} must be a status string")
        allowed = set(CONTRACT["statuses"]["common_exceptional"])
        allowed.update(CONTRACT["statuses"]["review"])
        allowed.update(CONTRACT["statuses"]["role_success"].values())
        if value not in allowed:
            raise ContractError(f"{field} is not a ContractV2 role status")
    elif kind == "check_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["checks"]),
            field=field,
            item_validator=lambda item, item_field: _validate_check(item, item_field),
            unique=True,
        )
        _require_unique_item_key(value, "id", field)
    elif kind == "risk_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["risks"]),
            field=field,
            item_validator=lambda item, item_field: _validate_risk(item, item_field),
            unique=True,
        )
        _require_unique_item_key(value, "id", field)
    elif kind == "finding_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["findings"]),
            field=field,
            item_validator=lambda item, item_field: _validate_finding(item, item_field),
            unique=True,
        )
        _require_unique_item_key(value, "id", field)
    elif kind == "model_profile":
        _validate_model_profile(value, field)
    elif kind == "role_payload":
        _validate_role_payload(value, field)
    elif kind == "record":
        sub_kind = spec["kind"]
        errors.extend(f"{field}.{item}" for item in validate_record(value, sub_kind))
    elif kind == "record_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["scope_entries"]),
            field=field,
            item_validator=lambda item, item_field: _validate_record_item(item, spec["kind"], item_field),
            unique=False,
        )
        if len(value) < spec.get("min_items", 0):
            raise ContractError(f"{field} has fewer than {spec['min_items']} entries")
        if spec["kind"] == "review_round":
            _require_unique_item_key(value, "round", field)
    elif kind == "plan_list":
        _validate_list(value, max_items=spec.get("max_items", 32), field=field, item_validator=_validate_plan, unique=True)
        _require_unique_item_key(value, "step_id", field)
    elif kind == "acceptance_list":
        _validate_list(value, max_items=spec.get("max_items", 32), field=field, item_validator=_validate_acceptance, unique=True)
        if len(value) < spec.get("min_items", 0):
            raise ContractError(f"{field} has fewer than {spec['min_items']} entries")
        _require_unique_item_key(value, "criterion_id", field)
    elif kind == "focused_check_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", 32),
            field=field,
            item_validator=lambda item, item_field: _validate_record_item(item, "focused_check", item_field),
            unique=True,
        )
        _require_unique_item_key(value, "id", field)
    elif kind == "baseline":
        _validate_baseline(value, field)
    elif kind == "checkpoint_or_none":
        if value is not None:
            _validate_checkpoint(value, field)
    elif kind == "artifact_list":
        _validate_list(value, max_items=spec.get("max_items", 32), field=field, item_validator=_validate_artifact, unique=True)
        _require_unique_item_key(value, "artifact_id", field)
    elif kind == "active_work_list":
        _validate_list(value, max_items=spec.get("max_items", 32), field=field, item_validator=_validate_active_work, unique=True)
        _require_unique_item_key(value, "invocation_id", field)
    elif kind == "decision_list":
        _validate_list(value, max_items=spec.get("max_items", 16), field=field, item_validator=_validate_decision, unique=True)
        _require_unique_item_key(value, "decision_id", field)
    elif kind == "runtime_event_list":
        _validate_list(
            value,
            max_items=spec.get("max_items", CONTRACT["limits"]["runtime_events"]),
            field=field,
            item_validator=_validate_runtime_event,
            unique=False,
        )
        event_ids: set[str] = set()
        for index, event in enumerate(value):
            event_id = event.get("event_id")
            if event_id in event_ids:
                raise ContractError(f"{field} contains duplicate event_id at index {index}")
            event_ids.add(event_id)
    else:
        raise ContractError(f"unsupported ContractV2 field type: {kind}")


def _validate_record_item(value: Any, kind: str, field: str) -> None:
    errors = validate_record(value, kind)
    if errors:
        raise ContractError(f"{field}: {'; '.join(errors)}")


def _validate_declared_object(value: Any, name: str, field: str) -> None:
    spec = CONTRACT["nested_records"].get(name)
    if spec is None:
        raise ContractError(f"missing nested ContractV2 definition: {name}")
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object")
    minimum = spec.get("min_properties")
    if minimum is not None and len(value) < minimum:
        raise ContractError(f"{field} requires at least {minimum} field(s)")
    _validate_object(value, spec, field, [])


def _validate_check(value: Any, field: str) -> None:
    _validate_declared_object(value, "check", field)


def _validate_risk(value: Any, field: str) -> None:
    _validate_declared_object(value, "risk", field)


def _validate_finding(value: Any, field: str) -> None:
    _validate_declared_object(value, "finding", field)


def _validate_model_profile(value: Any, field: str) -> None:
    _validate_declared_object(value, "model_profile", field)


def _validate_role_payload(value: Any, field: str) -> None:
    _validate_declared_object(value, "role_payload", field)


def _validate_plan(value: Any, field: str) -> None:
    _validate_declared_object(value, "plan_step", field)


def _validate_acceptance(value: Any, field: str) -> None:
    _validate_declared_object(value, "acceptance_criterion", field)


def _validate_baseline(value: Any, field: str) -> None:
    _validate_declared_object(value, "baseline", field)


def _validate_checkpoint(value: Any, field: str) -> None:
    _validate_declared_object(value, "checkpoint", field)


def _validate_artifact(value: Any, field: str) -> None:
    _validate_declared_object(value, "artifact", field)


def _validate_active_work(value: Any, field: str) -> None:
    _validate_declared_object(value, "active_work", field)


def _validate_decision(value: Any, field: str) -> None:
    _validate_declared_object(value, "decision", field)


def _validate_runtime_event(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object")
    event_type = value.get("event_type")
    if not isinstance(event_type, str):
        raise ContractError(f"{field}.event_type must be a string")
    kind = RUNTIME_EVENT_KINDS.get(event_type)
    if kind is None:
        raise ContractError(f"{field}.event_type is not a known runtime event type")
    errors = validate_record(value, kind)
    if errors:
        raise ContractError(f"{field}: {'; '.join(errors)}")


def _require_runtime_identity(record: dict[str, Any]) -> None:
    for field in (
        "run_id",
        "task_id",
        "invocation_id",
        "agent_id",
        "agent_channel",
        "transport_invocation_association",
        "binding_token",
    ):
        if record[field] is None or record[field] in SENTINELS:
            raise ContractError(f"{field} must contain an authoritative runtime identity")
    if record["runtime_target"] in SENTINELS:
        raise ContractError("runtime_target must contain an authoritative target")


def _require_real_identifier(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value or value in SENTINELS:
        raise ContractError(f"{field} must contain a non-sentinel identity")


def _scope_contains(scope_paths: Iterable[str], path: str) -> bool:
    normalized = normalize_repo_path(path)
    return any(
        normalized == scope_path or normalized.startswith(f"{scope_path}/")
        for scope_path in scope_paths
    )


def _path_scopes_overlap(first: str, second: str) -> bool:
    """Return whether two repository path scopes contain one another."""
    try:
        first_path = normalize_repo_path(first)
        second_path = normalize_repo_path(second)
    except ContractError:
        return False
    return (
        first_path == second_path
        or first_path.startswith(f"{second_path}/")
        or second_path.startswith(f"{first_path}/")
    )


def _path_scope_contains(scope: str, path: str) -> bool:
    """Return whether one repository path scope contains the path."""
    try:
        normalized_scope = normalize_repo_path(scope)
        normalized_path = normalize_repo_path(path)
    except ContractError:
        return False
    return normalized_path == normalized_scope or normalized_path.startswith(
        f"{normalized_scope}/"
    )


def _acceptance_definitions(task: dict[str, Any]) -> dict[str, str]:
    return {
        criterion["criterion_id"]: criterion["summary"]
        for criterion in task["acceptance_criteria"]
    }


def _focused_check_definitions(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        check["id"]: canonicalize_record(check, "focused_check")
        for check in task["focused_checks"]
    }


def validate_record(record: Any, kind: str) -> list[str]:
    """Return deterministic validation errors for a ContractV2 record."""
    if kind not in CONTRACT["records"]:
        return [f"unknown ContractV2 record kind: {kind}"]
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    errors: list[str] = []
    spec = CONTRACT["records"][kind]
    try:
        _validate_object(record, spec, "record", errors)
        if kind == "impact_scope":
            changed = {normalize_repo_path(item) for item in record["changed_paths"]}
            review = {normalize_repo_path(item) for item in record["review_paths"]}
            excluded = {normalize_scope_reference(item) for item in record["explicit_exclusions"]}
            uncovered = sorted(path for path in changed if not _scope_contains(review, path))
            if uncovered:
                raise ContractError(f"changed_paths are not covered by review_paths: {', '.join(uncovered)}")
            overlap = next(
                (
                    (review_path, excluded_path)
                    for review_path in sorted(review)
                    for excluded_path in sorted(excluded)
                    if _path_scopes_overlap(review_path, excluded_path)
                ),
                None,
            )
            if overlap is not None:
                review_path, excluded_path = overlap
                raise ContractError(
                    "review_paths overlap explicit_exclusions: "
                    f"{review_path} <-> {excluded_path}"
                )
        elif kind == "focused_check":
            if record["required"] and not record["covered_scope"]:
                raise ContractError("required focused checks must declare covered_scope")
        elif kind == "task_spec":
            if record["role"] != "implementer" and record["write_scope"]:
                raise ContractError("non-implementer TaskSpec write_scope must be empty")
            if record["mode"] == "portable":
                if record["binding_mode"] != "transport_bound_provisional":
                    raise ContractError("portable TaskSpec requires transport_bound_provisional binding")
                if record["binding_token"] is not None:
                    raise ContractError("portable TaskSpec must not carry a binding token")
                if record["write_scope"]:
                    raise ContractError("portable delegated TaskSpec write_scope must be empty")
            elif record["binding_mode"] != "runtime_atomic":
                raise ContractError("strict TaskSpec requires runtime_atomic binding")
            if record["mode"] == "strict":
                _require_real_identifier(record, "binding_token")
            if record["role"] == "reviewer":
                for field in (
                    "invocation_id",
                    "base_snapshot",
                    "base_content_identity",
                    "snapshot_id",
                    "content_identity",
                    "artifact_path",
                    "artifact_access_proof",
                    "review_coverage_proof",
                ):
                    _require_real_identifier(record, field)
                review_paths = {normalize_repo_path(path) for path in record["impact_scope"]["review_paths"]}
                read_scope = {normalize_repo_path(path) for path in record["read_scope"]}
                if any(not _scope_contains(read_scope, path) for path in review_paths):
                    raise ContractError("reviewer read_scope must include every impact_scope.review_paths entry")
        elif kind == "capability_preflight":
            mode = record["mode"]
            required = set(PORTABLE_CAPABILITIES if mode == "portable" else STRICT_CAPABILITIES)
            capabilities = record["capabilities"]
            if set(capabilities) != required:
                missing_keys = sorted(required - set(capabilities))
                extra_keys = sorted(set(capabilities) - required)
                raise ContractError(
                    "capabilities must contain exactly the keys required for mode "
                    f"(missing={missing_keys}, extra={extra_keys})"
                )
            expected_missing = sorted(name for name in required if not capabilities[name])
            actual_missing = sorted(record["missing"])
            if actual_missing != expected_missing:
                raise ContractError("missing must exactly list false required capabilities")
            if mode == "portable":
                expected_result = "PORTABLE_READY" if not expected_missing else "NOT_READY"
            elif record["authority"] != "authoritative_runtime_record":
                expected_result = "NOT_READY"
            else:
                expected_result = "STRICT_READY" if not expected_missing else "NOT_READY"
            if record["result"] != expected_result:
                raise ContractError(f"result must be {expected_result} for this mode and capability map")
            if mode == "strict" and record["result"] == "STRICT_READY" and record["authority"] != "authoritative_runtime_record":
                raise ContractError("strict preflight requires authoritative_runtime_record authority")
            if mode == "strict" and record["result"] == "STRICT_READY":
                raise ContractError("bundled tooling cannot attest STRICT_READY; validate with an authoritative runtime adapter")
        elif kind == "role_result":
            role = record["role"]
            status = record["status"]
            allowed = set(CONTRACT["statuses"]["common_exceptional"])
            if role == "reviewer":
                allowed.update(CONTRACT["statuses"]["review"])
            else:
                allowed.add(CONTRACT["statuses"]["role_success"][role])
            if status not in allowed:
                raise ContractError(f"record.status {status!r} is invalid for role {role!r}")
            if role in {"researcher", "planner", "reviewer", "verifier"} and record["changed_paths"]:
                raise ContractError(f"{role} results must report changed_paths=[]")
            success_statuses = set(CONTRACT["statuses"]["role_success"].values()) | {"CLEAN", "FINDINGS"}
            if status in success_statuses:
                if not record["completed_scope"]:
                    raise ContractError("successful role results require completed_scope")
                if record["blocker_or_input"] is not None:
                    raise ContractError("successful role results require blocker_or_input=null")
            if status in {"NEEDS_INPUT", "NEEDS_USER_DECISION", "BLOCKED", "REVIEW_BLOCKED"} and record["blocker_or_input"] is None:
                raise ContractError(f"{status} requires blocker_or_input")
            review_only = {
                "snapshot_id", "artifact_access_proof", "review_coverage_proof",
                "reviewed_paths", "findings",
            }
            if role != "reviewer" and review_only & set(record):
                raise ContractError("non-reviewer role results must not carry review-only fields")
            if status == "FINDINGS" and not record.get("findings"):
                raise ContractError("reviewer FINDINGS requires at least one finding")
            if status == "FINDINGS" and not any(
                finding["status"] in CONTRACT["statuses"]["actionable_finding_statuses"]
                for finding in record["findings"]
            ):
                raise ContractError("reviewer FINDINGS requires an actionable finding")
            if status in {"CLEAN", "FINDINGS"} and not record.get("reviewed_paths"):
                raise ContractError(f"reviewer {status} requires reviewed_paths")
            if status == "CLEAN" and any(
                finding["status"] in CONTRACT["statuses"]["actionable_finding_statuses"]
                for finding in record.get("findings", [])
            ):
                raise ContractError("reviewer CLEAN cannot contain actionable findings")
            if status == "CLEAN" and any(risk["status"] == "OPEN" for risk in record.get("risks", [])):
                raise ContractError("reviewer CLEAN cannot contain open risks")
            if role == "reviewer" and status in {"CLEAN", "FINDINGS"}:
                for field in (
                    "mode",
                    "run_id",
                    "task_id",
                    "invocation_id",
                    "report_id",
                    "base_snapshot",
                    "base_content_identity",
                    "snapshot_id",
                    "content_identity",
                    "artifact_access_proof",
                    "review_coverage_proof",
                ):
                    _require_real_identifier(record, field)
                if not record["checks"]:
                    raise ContractError("terminal reviewer results require at least one check")
                if status == "CLEAN" and any(check["status"] != "PASSED" for check in record["checks"]):
                    raise ContractError("reviewer CLEAN requires only passed checks")
                for finding in record.get("findings", []):
                    if not _scope_contains(record["reviewed_paths"], finding["path"]):
                        raise ContractError(f"finding path is outside reviewed_paths: {finding['path']}")
                payload = record.get("role_payload")
                if payload is not None and payload.get("mode") != record["mode"]:
                    raise ContractError("role_payload.mode must match role_result.mode")
        elif kind == "workflow_outcome":
            outcome = record["outcome"]
            expected_mode = {"ACCEPTED_PORTABLE": "portable", "ACCEPTED_STRICT": "strict"}.get(outcome)
            if expected_mode and record["mode"] != expected_mode:
                raise ContractError(f"{outcome} requires mode={expected_mode}")
            if outcome == "ACCEPTED_STRICT":
                raise ContractError("bundled tooling cannot attest ACCEPTED_STRICT")
            if expected_mode and (
                record["accepted"] is not True
                or record["review_status"] != "CLEAN"
                or record["validation_status"] != "PASSED"
            ):
                raise ContractError(
                    "accepted outcomes require accepted=true, CLEAN review, and passed validation"
                )
            if outcome == "NOT_ACCEPTED" and record["accepted"] is not False:
                raise ContractError("NOT_ACCEPTED requires accepted=false")
            if record["accepted"]:
                for field in (
                    "snapshot_id", "content_identity", "capability_preflight_digest",
                    "final_review_round_digest", "full_validation_check_id",
                ):
                    _require_real_identifier(record, field)
                if not record["validation_checks"] or any(
                    check["status"] != "PASSED" for check in record["validation_checks"]
                ):
                    raise ContractError("accepted outcomes require nonempty passed validation_checks")
                if record["full_validation_check_id"] not in {
                    check["id"] for check in record["validation_checks"]
                }:
                    raise ContractError("full_validation_check_id must identify a validation check")
            if outcome == "NOT_ACCEPTED" and record["reason"] is None:
                raise ContractError("NOT_ACCEPTED requires a reason")
            if outcome != "NOT_ACCEPTED" and record["reason"] is not None:
                raise ContractError("accepted outcomes cannot carry a not-accepted reason")
            reason = record["reason"]
            if reason is not None:
                for field, allowed in CONTRACT["statuses"]["not_accepted_reason_rules"].get(reason, {}).items():
                    if record[field] not in allowed:
                        raise ContractError(f"{reason} requires {field} in {allowed}")
            commit_status = record["commit_status"]
            if commit_status == "NOT_REQUESTED":
                if record["commit_requested"] or record["commit_id"] is not None or record["commit_blocker"] is not None:
                    raise ContractError("NOT_REQUESTED requires no commit intent, id, or blocker")
            elif commit_status == "PENDING":
                if not record["commit_requested"] or not record["accepted"] or record["commit_id"] is not None or record["commit_blocker"] is not None:
                    raise ContractError("PENDING requires accepted commit intent without an id or blocker")
            elif commit_status == "CREATED":
                if not record["commit_requested"] or not record["accepted"] or record["commit_blocker"] is not None:
                    raise ContractError("CREATED requires accepted commit intent and no blocker")
                _require_real_identifier(record, "commit_id")
            elif commit_status == "BLOCKED":
                if not record["commit_requested"] or record["commit_id"] is not None or record["commit_blocker"] is None:
                    raise ContractError("BLOCKED requires commit intent, a blocker, and no commit id")
        elif kind == "artifact_access_proof":
            if record["snapshot_id"] != record["manifest_identity"]:
                raise ContractError("snapshot_id must equal manifest_identity")
            for field in ("proof_id", "base_snapshot", "base_content_identity"):
                _require_real_identifier(record, field)
        elif kind == "review_coverage_proof":
            required_ids = set(record["required_check_ids"])
            passed_ids = set(record["passed_check_ids"])
            if not passed_ids.issuperset(required_ids):
                raise ContractError("passed_check_ids must include every required_check_id")
            if record["coverage_status"] == "COMPLETE" and not record["completed_scope"]:
                raise ContractError("complete review coverage requires completed_scope")
        elif kind == "review_round":
            task = record["task_spec"]
            result = record["review_result"]
            artifact = record["artifact_access_proof"]
            coverage = record["review_coverage_proof"]
            if task["role"] != "reviewer" or result["role"] != "reviewer":
                raise ContractError("review_round requires reviewer task and result records")
            if record["round"] != coverage["round"]:
                raise ContractError("review round numbers do not match")
            for field in ("base_snapshot", "base_content_identity"):
                values = (task[field], result[field], artifact[field])
                if len(set(values)) != 1:
                    raise ContractError(f"review round has inconsistent {field}")
            for field in ("snapshot_id", "content_identity"):
                values = (task[field], result[field], artifact[field], coverage[field])
                if len(set(values)) != 1:
                    raise ContractError(f"review round has inconsistent {field}")
            if len({task["mode"], result["mode"], artifact["mode"]}) != 1:
                raise ContractError("review round has inconsistent mode")
            for field in ("run_id", "task_id", "invocation_id"):
                if task[field] != result[field]:
                    raise ContractError(f"review task/result {field} values do not match")
            if task["artifact_access_proof"] != result["artifact_access_proof"] or task["artifact_access_proof"] != artifact["proof_id"]:
                raise ContractError("artifact access proof identifiers do not match")
            if task["review_coverage_proof"] != result["review_coverage_proof"] or task["review_coverage_proof"] != coverage["proof_id"]:
                raise ContractError("review coverage proof identifiers do not match")
            result_digest = digest_record(result, "role_result")
            if artifact["reviewer_result_digest"] != result_digest or coverage["reviewer_result_digest"] != result_digest:
                raise ContractError("reviewer_result_digest does not match review_result")
            artifact_digest = digest_record(artifact, "artifact_access_proof")
            if coverage["artifact_access_proof_digest"] != artifact_digest:
                raise ContractError("artifact_access_proof_digest does not match artifact_access_proof")
            scope_digest = digest_record(task["impact_scope"], "impact_scope")
            if artifact["impact_scope_digest"] != scope_digest or coverage["impact_scope_digest"] != scope_digest:
                raise ContractError("impact_scope_digest does not match task impact_scope")
            expected_paths = {
                normalize_repo_path(path) for path in task["impact_scope"]["review_paths"]
            }
            if {normalize_repo_path(path) for path in artifact["scope_paths"]} != expected_paths:
                raise ContractError("artifact scope_paths do not match impact_scope.review_paths")
            if {normalize_repo_path(path) for path in coverage["review_paths"]} != expected_paths:
                raise ContractError("coverage review_paths do not match impact_scope.review_paths")
            if {normalize_repo_path(path) for path in result["reviewed_paths"]} != expected_paths:
                raise ContractError("review_result.reviewed_paths do not match impact_scope.review_paths")
            if set(coverage["completed_scope"]) != set(result["completed_scope"]):
                raise ContractError("coverage completed_scope does not match review_result.completed_scope")
            required_checks = [check for check in task["focused_checks"] if check["required"]]
            required_ids = {check["id"] for check in required_checks}
            if set(coverage["required_check_ids"]) != required_ids:
                raise ContractError("coverage required_check_ids do not match required focused checks")
            review_paths = task["impact_scope"]["review_paths"]
            for check in required_checks:
                if not any(
                    _path_scopes_overlap(covered, review_path)
                    for covered in check["covered_scope"]
                    for review_path in review_paths
                ):
                    raise ContractError(
                        f"required focused check {check['id']} does not cover the review scope"
                    )
            uncovered_changed_paths = [
                changed_path
                for changed_path in task["impact_scope"]["changed_paths"]
                if not any(
                    _path_scope_contains(covered, changed_path)
                    for check in required_checks
                    for covered in check["covered_scope"]
                )
            ]
            if uncovered_changed_paths:
                raise ContractError(
                    "required focused checks do not cover changed_paths: "
                    + ", ".join(sorted(uncovered_changed_paths))
                )
            passed_ids = {check["id"] for check in result["checks"] if check["status"] == "PASSED"}
            if set(coverage["passed_check_ids"]) != passed_ids:
                raise ContractError("coverage passed_check_ids do not match passed reviewer checks")
            if result["status"] == "CLEAN" and coverage["coverage_status"] != "COMPLETE":
                raise ContractError("CLEAN review requires COMPLETE coverage")
        elif kind == "acceptance_evidence":
            preflight = record["capability_preflight"]
            rounds = record["review_rounds"]
            outcome = record["workflow_outcome"]
            if len(rounds) > CONTRACT["limits"]["review_rounds"]:
                raise ContractError("review round count exceeds the ContractV2 limit")
            if [item["round"] for item in rounds] != list(range(1, len(rounds) + 1)):
                raise ContractError("review rounds must be contiguous and begin at 1")
            _require_unique_item_key(
                [item["task_spec"] for item in rounds if item["task_spec"].get("invocation_id") is not None],
                "invocation_id",
                "review rounds",
            )
            _require_unique_item_key(
                [item["review_result"] for item in rounds if item["review_result"].get("report_id") is not None],
                "report_id",
                "review rounds",
            )
            _require_unique_item_key(
                [item["artifact_access_proof"] for item in rounds], "proof_id", "review rounds"
            )
            _require_unique_item_key(
                [item["review_coverage_proof"] for item in rounds], "proof_id", "review rounds"
            )
            _require_unique_item_key(
                [item["task_spec"] for item in rounds], "snapshot_id", "review rounds"
            )
            _require_unique_item_key(
                [item["task_spec"] for item in rounds], "content_identity", "review rounds"
            )
            for index, current in enumerate(rounds):
                coverage = current["review_coverage_proof"]
                if current["task_spec"]["mode"] != preflight["mode"]:
                    raise ContractError("review round mode does not match capability preflight")
                if index == 0:
                    if coverage["prior_reviewer_result_digest"] is not None or coverage["addressed_finding_ids"]:
                        raise ContractError("the first review round cannot reference prior findings")
                    continue
                previous = rounds[index - 1]
                previous_result = previous["review_result"]
                for field in ("task_id", "run_id", "base_snapshot", "base_content_identity"):
                    if current["task_spec"][field] != previous["task_spec"][field]:
                        raise ContractError(f"review rounds have inconsistent {field}")
                current_task = current["task_spec"]
                previous_task = previous["task_spec"]
                if current_task["objective"] != previous_task["objective"]:
                    raise ContractError("review rounds cannot change the task objective")
                if _acceptance_definitions(current_task) != _acceptance_definitions(previous_task):
                    raise ContractError("review rounds cannot change acceptance-criterion definitions")
                if _focused_check_definitions(current_task) != _focused_check_definitions(previous_task):
                    raise ContractError("review rounds cannot change focused-check definitions")
                if digest_record(current_task["impact_scope"], "impact_scope") != digest_record(
                    previous_task["impact_scope"], "impact_scope"
                ):
                    raise ContractError("review rounds cannot change impact scope")
                if previous_result["status"] != "FINDINGS":
                    raise ContractError("only FINDINGS may be followed by another review round")
                previous_digest = digest_record(previous_result, "role_result")
                if coverage["prior_reviewer_result_digest"] != previous_digest:
                    raise ContractError("review round does not bind the immediately preceding result")
                finding_ids = {
                    finding["id"] for finding in previous_result.get("findings", [])
                    if finding["status"] in CONTRACT["statuses"]["actionable_finding_statuses"]
                }
                if set(coverage["addressed_finding_ids"]) != finding_ids:
                    raise ContractError("review round must account for every preceding actionable finding")
                if (
                    current["review_result"]["snapshot_id"] == previous_result["snapshot_id"]
                    or current["review_result"]["content_identity"] == previous_result["content_identity"]
                ):
                    raise ContractError("a findings fix requires a new snapshot and content identity")
            if outcome["mode"] != preflight["mode"]:
                raise ContractError("workflow outcome mode does not match capability preflight")
            if outcome["accepted"]:
                if outcome["mode"] == "strict":
                    raise ContractError("bundled tooling cannot attest ACCEPTED_STRICT")
                if preflight["result"] != "PORTABLE_READY":
                    raise ContractError("portable acceptance requires PORTABLE_READY preflight")
                final_round = rounds[-1]
                final_result = final_round["review_result"]
                final_artifact = final_round["artifact_access_proof"]
                final_coverage = final_round["review_coverage_proof"]
                if any(
                    criterion["status"] != "met"
                    for criterion in final_round["task_spec"]["acceptance_criteria"]
                ):
                    raise ContractError("accepted evidence requires every acceptance criterion to be met")
                if final_result["status"] != "CLEAN":
                    raise ContractError("accepted evidence requires a final CLEAN review")
                if final_coverage["coverage_status"] != "COMPLETE":
                    raise ContractError("accepted evidence requires complete final review coverage")
                if final_artifact["artifact_verification_status"] != "PASSED" or final_artifact["workspace_compare_status"] != "PASSED":
                    raise ContractError("accepted evidence requires passed artifact verification and workspace comparison")
                if outcome["capability_preflight_digest"] != digest_record(preflight, "capability_preflight"):
                    raise ContractError("capability_preflight_digest does not match preflight")
                if outcome["final_review_round_digest"] != digest_record(final_round, "review_round"):
                    raise ContractError("final_review_round_digest does not match final review round")
                if outcome["snapshot_id"] != final_result["snapshot_id"] or outcome["content_identity"] != final_result["content_identity"]:
                    raise ContractError("workflow outcome is not bound to the final reviewed snapshot")
        elif kind == "runtime_completion_event":
            _require_runtime_identity(record)
            if record["status"] != "completed" or record["result_delivery_confirmed"] != "yes":
                raise ContractError("completion event must confirm completed result delivery")
        elif kind == "runtime_terminal_event":
            if record["status"] == "spawn_failed":
                expected_unassigned = {
                    "agent_id": "UNASSIGNED",
                    "agent_channel": "UNASSIGNED",
                    "transport_invocation_association": "UNASSIGNED",
                    "runtime_target": "NONE",
                    "binding_token": "NONE",
                }
                for field, expected in expected_unassigned.items():
                    if record[field] != expected:
                        raise ContractError(f"spawn_failed requires {field}={expected}")
                if record["child_started"] != "no" or record["stop_confirmed"] != "not_applicable":
                    raise ContractError("spawn_failed must report child_started=no and no stop confirmation")
            else:
                _require_runtime_identity(record)
                if record["child_started"] != "yes" or record["stop_confirmed"] != "yes":
                    raise ContractError("a started terminal event requires confirmed stop")
                if record["status"] == "cancelled" and record["cancel_confirmed_at"] is None:
                    raise ContractError("cancelled terminal events require cancel confirmation")
                if record["cancel_confirmed_at"] is not None and Decimal(record["cancel_confirmed_at"]) > Decimal(record["terminal_at"]):
                    raise ContractError("terminal_at must not precede cancel_confirmed_at")
        elif kind == "runtime_stop_event":
            _require_runtime_identity(record)
            if record["status"] != "stopped" or record["stop_confirmed"] != "yes":
                raise ContractError("stop event must confirm status=stopped and stop_confirmed=yes")
        elif kind == "runtime_event_sequence":
            events = record["events"]
            if not events:
                raise ContractError("runtime event sequence must contain at least one event")
            if any(
                event["event_type"] == "RUNTIME_TERMINAL_EVENT"
                and event["status"] == "spawn_failed"
                for event in events
            ) and len(events) != 1:
                raise ContractError("spawn_failed must be the only event in a runtime event sequence")
            identity_fields = (
                "run_id",
                "task_id",
                "invocation_id",
                "attempt",
                "agent_id",
                "agent_channel",
                "transport_invocation_association",
                "runtime_target",
                "binding_token",
            )
            for field in identity_fields:
                values = {
                    event[field]
                    for event in events
                    if event.get(field) is not None and event[field] not in SENTINELS
                }
                if len(values) > 1:
                    raise ContractError(f"runtime event sequence has inconsistent {field}")
            timestamps: list[Decimal] = []
            for event in events:
                timestamp_field = {
                    "RUNTIME_COMPLETION_EVENT": "terminal_at",
                    "RUNTIME_TERMINAL_EVENT": "terminal_at",
                    "RUNTIME_STOP_EVENT": "stopped_at",
                }[event["event_type"]]
                timestamps.append(Decimal(event[timestamp_field]))
            if any(previous > current for previous, current in zip(timestamps, timestamps[1:])):
                raise ContractError("runtime event sequence timestamps must be non-decreasing")
        elif kind == "context_handoff":
            mode = record["mode"]
            if mode == "independent":
                if record["checkpoint"] is not None or record["artifacts"] or record["active_work"]:
                    raise ContractError("independent handoff must not copy checkpoint, artifacts, or active work")
                if record["source_task_id"] is not None or record["source_run_id"] is not None:
                    raise ContractError("independent handoff must not carry continuation identities")
            elif record["checkpoint"] is None:
                raise ContractError("continuation handoff requires a checkpoint")
            if mode == "continuation" and (
                record["source_task_id"] != record["task_id"]
                or record["source_run_id"] != record["run_id"]
            ):
                raise ContractError("continuation handoff must preserve task and run identities")
            if mode == "continuation":
                checkpoint_identity = record["checkpoint"].get("content_identity")
                if checkpoint_identity is not None and checkpoint_identity not in SENTINELS:
                    if not any(
                        artifact.get("content_identity") == checkpoint_identity
                        for artifact in record["artifacts"]
                    ):
                        raise ContractError(
                            "continuation checkpoint content_identity requires a matching artifact"
                        )
    except (ContractError, KeyError, RecursionError) as exc:
        errors.append(str(exc))
    return sorted(set(errors))


def _canonicalize_value(value: Any, spec: dict[str, Any]) -> Any:
    kind = spec.get("type")
    if kind == "path":
        return normalize_repo_path(value)
    if kind == "path_list":
        values = [normalize_repo_path(item) for item in value]
        return sorted(values) if spec.get("set_like") else values
    if kind in {"reference_list", "token_list"}:
        values = [normalize_scope_reference(item) for item in value] if kind == "reference_list" else list(value)
        return sorted(values) if spec.get("set_like") else values
    if kind in {
        "check_list",
        "risk_list",
        "finding_list",
        "plan_list",
        "acceptance_list",
        "artifact_list",
        "active_work_list",
        "decision_list",
    }:
        nested_name = {
            "check_list": "check",
            "risk_list": "risk",
            "finding_list": "finding",
            "plan_list": "plan_step",
            "acceptance_list": "acceptance_criterion",
            "artifact_list": "artifact",
            "active_work_list": "active_work",
            "decision_list": "decision",
        }[kind]
        return [_canonicalize_declared_object(item, nested_name) for item in value]
    if kind == "record":
        return canonicalize_record(value, spec["kind"])
    if kind == "record_list":
        return [canonicalize_record(item, spec["kind"]) for item in value]
    if kind == "focused_check_list":
        return [canonicalize_record(item, "focused_check") for item in value]
    if kind == "model_profile":
        return _canonicalize_declared_object(value, "model_profile")
    if kind == "role_payload":
        return _canonicalize_declared_object(value, "role_payload")
    if kind == "baseline":
        return _canonicalize_declared_object(value, "baseline")
    if kind == "checkpoint_or_none":
        return None if value is None else _canonicalize_declared_object(value, "checkpoint")
    if kind == "runtime_event_list":
        return [
            canonicalize_record(item, RUNTIME_EVENT_KINDS[item["event_type"]])
            for item in value
        ]
    return value


def _canonicalize_declared_object(value: dict[str, Any], name: str) -> dict[str, Any]:
    spec = CONTRACT["nested_records"][name]
    return {
        key: _canonicalize_value(value[key], spec["fields"][key])
        for key in sorted(value)
    }


def canonicalize_record(record: dict[str, Any], kind: str) -> dict[str, Any]:
    errors = validate_record(record, kind)
    if errors:
        raise ContractError("; ".join(errors))
    spec = CONTRACT["records"][kind]
    return {
        key: _canonicalize_value(record[key], spec["fields"][key])
        for key in sorted(record)
    }


def digest_record(record: dict[str, Any], kind: str) -> str:
    canonical = canonicalize_record(record, kind)
    payload = {"kind": kind, "record": canonical}
    return hashlib.sha256(DOMAINS["record"] + canonical_json(payload)).hexdigest()


def _diagnostic(kind: str, valid: bool, *, errors: Iterable[str] = (), digest: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"valid": valid, "kind": kind, "errors": list(errors)}
    if digest is not None:
        result["digest"] = digest
    return result


def _run(command: str, kind: str, input_path: str) -> int:
    def emit(value: dict[str, Any]) -> None:
        print(json.dumps(value, ensure_ascii=True, sort_keys=True))

    try:
        record = load_json_file(input_path)
        errors = validate_record(record, kind)
        if errors:
            emit(_diagnostic(kind, False, errors=errors))
            return 2
        digest = digest_record(record, kind)
        if command == "validate":
            emit(_diagnostic(kind, True, digest=digest))
        else:
            emit({"digest": digest, "kind": kind, "valid": True})
        return 0
    except (ContractError, OSError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        emit(_diagnostic(kind, False, errors=[str(exc)]))
        return 2


def _lookup_contract_path(path: str) -> Any:
    if not isinstance(path, str) or not path:
        raise ContractError("describe --path must be a non-empty dotted path")
    current: Any = CONTRACT
    for component in path.split("."):
        if not component or not isinstance(current, dict) or component not in current:
            raise ContractError(f"unknown ContractV2 path: {path}")
        current = current[component]
    return current


def _run_describe(*, kind: str | None, section: str | None, path: str | None) -> int:
    selected = sum(value is not None for value in (kind, section, path))
    if selected != 1:
        print(json.dumps({"valid": False, "errors": ["describe requires exactly one of --kind, --section, or --path"]}, sort_keys=True))
        return 2
    value: Any
    if kind is not None:
        if kind not in CONTRACT["records"]:
            print(json.dumps({"valid": False, "errors": [f"unknown ContractV2 record kind: {kind}"]}, sort_keys=True))
            return 2
        value = CONTRACT["records"][kind]
    elif section is not None:
        if section not in CONTRACT:
            print(json.dumps({"valid": False, "errors": [f"unknown ContractV2 section: {section}"]}, sort_keys=True))
            return 2
        value = CONTRACT[section]
    else:
        try:
            assert path is not None
            value = _lookup_contract_path(path)
        except ContractError as exc:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
            return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "digest"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--kind", required=True, choices=sorted(CONTRACT["records"]))
        subparser.add_argument("input", help="JSON record path, or - for stdin")
    describe = subparsers.add_parser("describe")
    selection = describe.add_mutually_exclusive_group(required=True)
    selection.add_argument("--kind", choices=sorted(CONTRACT["records"]))
    selection.add_argument("--section", choices=sorted(CONTRACT))
    selection.add_argument("--path", metavar="DOTTED_PATH", help="effective ContractV2 path, such as records.role_result.fields.status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "describe":
        return _run_describe(kind=args.kind, section=args.section, path=args.path)
    return _run(args.command, args.kind, args.input)


if __name__ == "__main__":
    sys.exit(main())
