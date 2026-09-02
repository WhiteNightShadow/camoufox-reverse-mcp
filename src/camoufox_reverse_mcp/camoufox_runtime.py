"""Read-only compatibility helpers for Camoufox browser installations.

Camoufox 0.5 introduced a versioned browser cache while 0.4 uses one flat
installation.  This module deliberately limits itself to discovery and launch
selection: it never calls Camoufox's ``set_active``/``save_config`` helpers and
never downloads, migrates, or removes browser data.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any


CAPABILITIES_FILE = "camoufox-reverse-capabilities.json"


def _package_version() -> str:
    try:
        return distribution_version("camoufox")
    except PackageNotFoundError:
        return "not-installed"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in value.split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple((parts + [0, 0, 0])[:3])


def _load_multiversion_module():
    from camoufox import multiversion

    return multiversion


def _load_pkgman_module():
    from camoufox import pkgman

    return pkgman


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_browser_metadata(root: Path) -> dict[str, Any]:
    """Read legacy/current ``version.json`` and the optional capability marker."""
    version_data = _read_json(root / "version.json")
    version = version_data.get("version")
    build = version_data.get("build") or version_data.get("release") or version_data.get("tag")

    marker = _read_json(root / CAPABILITIES_FILE)
    raw_capabilities = marker.get("capabilities")
    if isinstance(raw_capabilities, dict):
        normalized = {
            str(name) for name, enabled in raw_capabilities.items() if enabled
        }
    elif isinstance(raw_capabilities, list):
        normalized = {str(item) for item in raw_capabilities}
    else:
        normalized = set()
    property_trace = bool(
        marker.get("property_trace")
        or marker.get("propertyTrace")
        or "property_trace" in normalized
        or "propertyTrace" in normalized
    )
    return {
        "version": str(version) if version is not None else None,
        "build": str(build) if build is not None else None,
        "capabilities": sorted(normalized),
        "property_trace": property_trace,
        "capabilities_marker": str(root / CAPABILITIES_FILE) if marker else None,
    }


def _firefox_major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _public_install(
    *,
    root: Path,
    repo: str,
    active: bool,
    folder: str | None = None,
) -> dict[str, Any]:
    metadata = _read_browser_metadata(root)
    version = metadata["version"]
    build = metadata["build"]
    full_version = f"{version}-{build}" if version and build else None
    folder_name = folder or root.name
    selector = f"{repo}/{folder_name}" if repo else full_version
    return {
        "selector": selector,
        "repo": repo or None,
        "version": version,
        "build": build,
        "full_version": full_version,
        "firefox_major": _firefox_major(version),
        "path": str(root),
        "folder": folder_name,
        "active": active,
        "capabilities": metadata["capabilities"],
        "property_trace": metadata["property_trace"],
        "capabilities_marker": metadata["capabilities_marker"],
    }


def _inspect_multiversion() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    module = _load_multiversion_module()
    installs: list[dict[str, Any]] = []
    for item in module.list_installed():
        root = Path(item.path)
        repo = str(item.repo_name).lower()
        installs.append(
            _public_install(
                root=root,
                repo=repo,
                active=bool(item.is_active),
                folder=root.name,
            )
        )

    config = module.load_config()
    install_dir = Path(module.BROWSERS_DIR).parent
    compat_flag = Path(module.COMPAT_FLAG)
    migration_risk = bool(
        install_dir.exists()
        and any(install_dir.iterdir())
        and not compat_flag.exists()
    )
    return installs, {
        "active_version": config.get("active_version"),
        "channel": config.get("channel"),
        "pinned": config.get("pinned"),
        "cache_dir": str(install_dir),
        "legacy_cache_migration_risk": migration_risk,
    }


def _inspect_legacy() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pkgman = _load_pkgman_module()
    install_dir = Path(pkgman.INSTALL_DIR)
    installs: list[dict[str, Any]] = []
    if (install_dir / "version.json").exists():
        installs.append(_public_install(root=install_dir, repo="", active=True))
    return installs, {
        "active_version": installs[0]["full_version"] if installs else None,
        "channel": None,
        "pinned": None,
        "cache_dir": str(install_dir),
        "legacy_cache_migration_risk": False,
    }


def inspect_camoufox_runtime() -> dict[str, Any]:
    """Return Camoufox package/cache state without changing active selection."""
    package_version = _package_version()
    if package_version == "not-installed":
        return {
            "python_version": package_version,
            "multiversion_supported": False,
            "active": None,
            "installed": [],
            "error": "camoufox package is not installed",
        }

    multiversion = _version_tuple(package_version) >= (0, 5, 0)
    try:
        installs, config = _inspect_multiversion() if multiversion else _inspect_legacy()
    except Exception as exc:
        return {
            "python_version": package_version,
            "multiversion_supported": multiversion,
            "active": None,
            "installed": [],
            "error": str(exc),
        }

    active = next((item for item in installs if item["active"]), None)
    return {
        "python_version": package_version,
        "multiversion_supported": multiversion,
        "active": active,
        "installed": installs,
        **config,
    }


def resolve_browser_version(selector: str) -> dict[str, Any]:
    """Resolve one explicit, repo-qualified Camoufox 0.5 browser selector.

    The returned ``launch_selector`` points at the exact version folder, which
    avoids Camoufox's ambiguous build-only lookup when repositories contain the
    same Firefox build.
    """
    requested = str(selector or "").strip()
    if not requested:
        raise ValueError("browser_version must not be empty")

    runtime = inspect_camoufox_runtime()
    package_version = runtime.get("python_version", "not-installed")
    if not runtime.get("multiversion_supported"):
        raise ValueError(
            f"browser_version requires Camoufox Python >= 0.5.0; installed version is {package_version}. "
            "Omit browser_version to keep using the active Camoufox 0.4.x browser."
        )
    if "/" not in requested:
        raise ValueError(
            "browser_version must be repo-qualified (for example "
            "'official/beta.28'); build-only selectors are ambiguous across repositories"
        )

    repo, value = requested.split("/", 1)
    repo = repo.strip().lower()
    value = value.strip().lower()
    if not repo or not value:
        raise ValueError("browser_version must use the form 'repo/build-or-version'")

    matches: list[dict[str, Any]] = []
    for item in runtime.get("installed", []):
        if str(item.get("repo") or "").lower() != repo:
            continue
        accepted = {
            str(item.get("build") or "").lower(),
            str(item.get("full_version") or "").lower(),
            str(item.get("folder") or "").lower(),
        }
        if value in accepted:
            matches.append(item)

    if not matches:
        available = [
            item["selector"]
            for item in runtime.get("installed", [])
            if item.get("selector")
        ]
        suffix = f" Installed selectors: {', '.join(available)}" if available else ""
        raise ValueError(f"browser_version '{requested}' is not installed.{suffix}")
    if len(matches) > 1:
        exact = [f"{item['repo']}/{item['folder']}" for item in matches]
        raise ValueError(
            f"browser_version '{requested}' matches multiple installed assets; "
            f"use an exact folder selector: {', '.join(exact)}"
        )

    selected = dict(matches[0])
    major = selected.get("firefox_major")
    if major is None:
        raise ValueError(
            f"Unable to determine Firefox major version from {selected.get('path')}/version.json"
        )
    active = runtime.get("active")
    if not active:
        raise ValueError(
            "browser_version requires one active Camoufox 0.5 browser because the "
            "upstream wrapper reads shared resources before applying the per-launch selector"
        )
    active_major = active.get("firefox_major")
    if active_major != major:
        raise ValueError(
            "Cross-major browser_version selection is unsafe: the active browser is "
            f"Firefox {active_major}, while the selected browser is Firefox {major}. "
            "Use an active browser with the same Firefox major."
        )
    if active.get("full_version") != selected.get("full_version"):
        raise ValueError(
            "Active and selected Camoufox builds must match exactly because the "
            "upstream wrapper reads shared resources from the active browser before "
            f"applying the selector: active={active.get('full_version')}, "
            f"selected={selected.get('full_version')}."
        )
    selected["requested_selector"] = requested
    selected["launch_selector"] = f"{selected['repo']}/{selected['folder']}"
    return selected


def launch_overrides(selector: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return Camoufox kwargs plus serializable selection metadata."""
    selected = resolve_browser_version(selector)
    # The resolver requires active and selected browsers to share a Firefox
    # major, so Camoufox's normal active-major fingerprint generation is
    # already correct. Passing ff_version explicitly would emit LeakWarning.
    return {"browser": selected["launch_selector"]}, selected
