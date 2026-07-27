"""Build and parse the release artifact compatibility manifest."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scripts.release.contract_versions import (
    CONTRACT_KEYS,
    ContractVersions,
    ContractVersionsError,
    check_generated_bindings,
    decode_json_object,
    exact_object,
    load_contract_versions,
    parse_contract_versions_payload,
    render_json_object,
)

MAX_COMPATIBILITY_MANIFEST_BYTES = 16 * 1024

_MANIFEST_SCHEMA = "awesome.release-compatibility"
_MANIFEST_VERSION = 1
_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_MANIFEST_KEYS = CONTRACT_KEYS | {"product", "schema", "version"}


class CompatibilityManifestError(RuntimeError):
    """The release compatibility manifest is missing or inconsistent."""


@dataclass(frozen=True, slots=True)
class ReleaseCompatibility:
    product_version: str
    contracts: ContractVersions

    @property
    def protocol_version(self) -> int:
        return self.contracts.protocol_version

    @property
    def application_schema_current(self) -> int:
        return self.contracts.application_schema_current

    @property
    def application_schema_migration_floor(self) -> int:
        return self.contracts.application_schema_migration_floor

    def payload(self) -> dict[str, object]:
        return {
            **self.contracts.payload(),
            "product": {"version": self.product_version},
            "schema": _MANIFEST_SCHEMA,
            "version": _MANIFEST_VERSION,
        }


def render_release_compatibility(root: Path, product_version: str) -> bytes:
    _validate_product_version(product_version)
    try:
        observed_version = (root / "VERSION").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CompatibilityManifestError(
            "product version owner is unavailable"
        ) from error
    if observed_version != f"{product_version}\n":
        raise CompatibilityManifestError("product version owner does not match")
    try:
        contracts = load_contract_versions(root)
        check_generated_bindings(root, contracts)
    except ContractVersionsError as error:
        raise CompatibilityManifestError(
            "contract version catalog is invalid"
        ) from error
    return _render_compatibility(
        ReleaseCompatibility(
            product_version=product_version,
            contracts=contracts,
        )
    )


def verify_release_compatibility(
    content: bytes,
    *,
    product_version: str,
) -> ReleaseCompatibility:
    if len(content) > MAX_COMPATIBILITY_MANIFEST_BYTES:
        raise CompatibilityManifestError("compatibility manifest is too large")
    _validate_product_version(product_version)
    try:
        payload = exact_object(
            decode_json_object(
                content,
                maximum_bytes=MAX_COMPATIBILITY_MANIFEST_BYTES,
                label="compatibility manifest",
            ),
            _MANIFEST_KEYS,
            "compatibility manifest",
        )
    except ContractVersionsError as error:
        raise CompatibilityManifestError(str(error)) from error
    if payload["schema"] != _MANIFEST_SCHEMA or payload["version"] != (
        _MANIFEST_VERSION
    ):
        raise CompatibilityManifestError("compatibility manifest identity is invalid")

    try:
        product = exact_object(payload["product"], {"version"}, "product")
    except ContractVersionsError as error:
        raise CompatibilityManifestError(str(error)) from error
    if product["version"] != product_version:
        raise CompatibilityManifestError("compatibility product version does not match")
    contract_payload = {key: payload[key] for key in CONTRACT_KEYS}
    try:
        contracts = parse_contract_versions_payload(contract_payload)
    except ContractVersionsError as error:
        raise CompatibilityManifestError(
            "compatibility contract inventory is invalid"
        ) from error
    compatibility = ReleaseCompatibility(
        product_version=product_version,
        contracts=contracts,
    )
    if content != _render_compatibility(compatibility):
        raise CompatibilityManifestError(
            "compatibility manifest is not canonically encoded"
        )
    return compatibility


def _render_compatibility(compatibility: ReleaseCompatibility) -> bytes:
    return render_json_object(compatibility.payload())


def _validate_product_version(product_version: str) -> None:
    if _SEMVER.fullmatch(product_version) is None:
        raise CompatibilityManifestError("product version is invalid")


__all__ = [
    "MAX_COMPATIBILITY_MANIFEST_BYTES",
    "CompatibilityManifestError",
    "ReleaseCompatibility",
    "render_release_compatibility",
    "verify_release_compatibility",
]
