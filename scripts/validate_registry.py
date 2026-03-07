from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
ID_RE = re.compile(r"^[a-z0-9_]+$")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate community registry packages.")
    parser.add_argument("registry_json", help="Path to sensor-registry.json")
    args = parser.parse_args()

    registry_path = Path(args.registry_json)
    if not registry_path.exists():
        print(f"Registry file not found: {registry_path}")
        return 1

    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {registry_path}: {exc}")
        return 1

    packages = data.get("packages", [])
    if not isinstance(packages, list) or not packages:
        print("Registry must include a non-empty 'packages' list.")
        return 1

    print(f"Validating {len(packages)} package(s) from {registry_path}")
    failures: List[Tuple[str, List[str]]] = []
    for idx, pkg in enumerate(packages, start=1):
        name, issues = validate_package_entry(pkg, registry_path.parent)
        print(f"[{idx}/{len(packages)}] {name}")
        if issues:
            print(f"  FAIL ({len(issues)} issue(s))")
            for issue in issues:
                print(f"    - {issue}")
            failures.append((name, issues))
        else:
            print("  PASS")

    if failures:
        print("")
        print(f"Validation failed for {len(failures)} package(s):")
        for name, issues in failures:
            print(f"- {name}: {len(issues)} issue(s)")
        return 1

    print("Registry validation passed.")
    return 0


def validate_package_entry(pkg: Any, base_dir: Path) -> Tuple[str, List[str]]:
    if not isinstance(pkg, dict):
        return "unknown", ["Package entry must be an object."]

    name = str(pkg.get("name") or "unknown")
    version = str(pkg.get("version") or "")
    url = str(pkg.get("url") or "")
    sha256 = str(pkg.get("sha256") or "").lower()
    issues: List[str] = []

    if not url:
        issues.append("Missing required field: url")
    if not sha256:
        issues.append("Missing required field: sha256")
    elif not SHA256_RE.match(sha256):
        issues.append("Invalid sha256 format: expected 64 lowercase hex characters")
    if issues:
        return _decorate_name(name, version), issues

    try:
        payload = download(url, base_dir)
    except Exception as exc:
        issues.append(f"Download failed from '{url}': {exc.__class__.__name__}: {exc}")
        return _decorate_name(name, version), issues

    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        issues.append(f"sha256 mismatch: expected {sha256}, got {digest}")
        return _decorate_name(name, version), issues

    issues.extend(validate_zip(payload, name))
    return _decorate_name(name, version), issues


def _decorate_name(name: str, version: str) -> str:
    return f"{name} (v{version})" if version else name


def download(url: str, base_dir: Path) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("", "file"):
        local_path = _resolve_local_package_path(url, parsed, base_dir)
        return local_path.read_bytes()

    req = urllib.request.Request(url, headers={"User-Agent": "autofw-registry-validator/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _resolve_local_package_path(raw_url: str, parsed: urllib.parse.ParseResult, base_dir: Path) -> Path:
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise ValueError(f"Unsupported file:// host in URL: {raw_url}")
        path = Path(urllib.request.url2pathname(parsed.path))
        if path.exists():
            return path
        raise FileNotFoundError(f"Local package not found: {path}")

    path = Path(raw_url)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    if path.exists():
        return path
    raise FileNotFoundError(f"Local package not found: {path}")


def validate_zip(payload: bytes, name: str) -> List[str]:
    issues: List[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / f"{name}.zip"
        zip_path.write_bytes(payload)
        extract_root = Path(tmp_dir) / "pkg"
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            return [f"Invalid ZIP archive: {exc}"]
        except Exception as exc:
            return [f"Failed to extract ZIP: {exc.__class__.__name__}: {exc}"]

        pkg_root = find_package_root(extract_root)
        if not pkg_root:
            entries = sorted(p.name for p in extract_root.iterdir()) if extract_root.exists() else []
            issues.append("Could not find package root with sensor.yaml or display.yaml")
            if entries:
                issues.append(f"Top-level ZIP entries: {', '.join(entries)}")
            return issues

        issues.extend(run_validator(pkg_root))
    return issues


def find_package_root(root: Path) -> Path | None:
    if (root / "sensor.yaml").exists() or (root / "display.yaml").exists():
        return root
    for d in root.iterdir():
        if d.is_dir() and ((d / "sensor.yaml").exists() or (d / "display.yaml").exists()):
            return d
    return None


def run_validator(pkg_root: Path) -> List[str]:
    errors = validate_package_dir(pkg_root)
    return [f"Package validation error: {err}" for err in errors]


def validate_package_dir(package_dir: Path) -> List[str]:
    errors: List[str] = []
    if not package_dir.exists():
        return [f"Package path does not exist: {package_dir}"]
    if not package_dir.is_dir():
        return [f"Package path is not a directory: {package_dir}"]

    sensor_path = package_dir / "sensor.yaml"
    display_path = package_dir / "display.yaml"
    components_path = package_dir / "components_map.yaml"
    menu_path = package_dir / "menu.yaml"

    if not components_path.exists():
        return ["Missing components_map.yaml"]
    if not sensor_path.exists() and not display_path.exists():
        return ["Missing sensor.yaml or display.yaml"]

    sensor_data = _load_yaml_dict(sensor_path, errors, "sensor.yaml") if sensor_path.exists() else {}
    display_data = _load_yaml_dict(display_path, errors, "display.yaml") if display_path.exists() else {}
    components_data = _load_yaml_dict(components_path, errors, "components_map.yaml")
    if errors:
        return errors

    if sensor_path.exists():
        sensor_id = sensor_data.get("id")
        if not sensor_id or not isinstance(sensor_id, str):
            errors.append("sensor.yaml: id is required and must be a string")
        elif not ID_RE.match(sensor_id):
            errors.append("sensor.yaml: id must match ^[a-z0-9_]+$")

        if not isinstance(sensor_data.get("name"), str) or not sensor_data.get("name"):
            errors.append("sensor.yaml: name is required and must be a string")
        if not isinstance(sensor_data.get("bus"), str) or not sensor_data.get("bus"):
            errors.append("sensor.yaml: bus is required and must be a string")

        sensors_map = components_data.get("sensors")
        if not isinstance(sensors_map, dict):
            errors.append("components_map.yaml: sensors must be a map")
        elif sensor_id and sensor_id not in sensors_map:
            errors.append(f"components_map.yaml: sensors missing entry for '{sensor_id}'")
        elif sensor_id:
            entry = sensors_map.get(sensor_id, {})
            if not isinstance(entry, dict):
                errors.append(f"components_map.yaml: sensors.{sensor_id} must be a map")
            else:
                source = entry.get("source", "registry")
                if source == "local":
                    _require_str(entry, "name", errors, f"components_map.yaml: sensors.{sensor_id}")
                    component_name = entry.get("name")
                    component_dir = package_dir / "component"
                    if _validate_local_component_dir(component_dir, errors):
                        if isinstance(component_name, str) and component_name:
                            _validate_local_sensor_interface(component_dir, component_name, errors)
                else:
                    _require_str(entry, "name", errors, f"components_map.yaml: sensors.{sensor_id}")
                    _require_str(entry, "version", errors, f"components_map.yaml: sensors.{sensor_id}")

    if display_path.exists():
        display_id = display_data.get("id")
        if not display_id or not isinstance(display_id, str):
            errors.append("display.yaml: id is required and must be a string")
        elif not ID_RE.match(display_id):
            errors.append("display.yaml: id must match ^[a-z0-9_]+$")

        if not isinstance(display_data.get("name"), str) or not display_data.get("name"):
            errors.append("display.yaml: name is required and must be a string")
        if not isinstance(display_data.get("bus"), str) or not display_data.get("bus"):
            errors.append("display.yaml: bus is required and must be a string")

        displays_map = components_data.get("displays")
        if not isinstance(displays_map, dict):
            errors.append("components_map.yaml: displays must be a map")
        elif display_id and display_id not in displays_map:
            errors.append(f"components_map.yaml: displays missing entry for '{display_id}'")
        elif display_id:
            entry = displays_map.get(display_id, {})
            if not isinstance(entry, dict):
                errors.append(f"components_map.yaml: displays.{display_id} must be a map")
            else:
                source = entry.get("source", "registry")
                if source == "local":
                    if entry.get("name") not in ("display", ""):
                        errors.append("Local display component must use name: display")
                    component_dir = package_dir / "component"
                    if _validate_local_component_dir(component_dir, errors):
                        _validate_local_display_interface(component_dir, errors)
                else:
                    _require_str(entry, "name", errors, f"components_map.yaml: displays.{display_id}")
                    _require_str(entry, "version", errors, f"components_map.yaml: displays.{display_id}")

    if menu_path.exists():
        _validate_menu_yaml(menu_path, errors, "menu.yaml")

    return errors


def _load_yaml_dict(path: Path, errors: List[str], label: str) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{label}: failed to read YAML: {exc}")
        return {}
    if not isinstance(data, dict):
        errors.append(f"{label}: top-level must be a map")
        return {}
    return data


def _require_str(data: Dict[str, Any], key: str, errors: List[str], label: str) -> None:
    value = data.get(key)
    if not value or not isinstance(value, str):
        errors.append(f"{label}.{key} is required and must be a string")


def _validate_menu_yaml(path: Path, errors: List[str], label: str) -> None:
    data = _load_yaml_dict(path, errors, label)
    if not data:
        return
    menu = data.get("menu", [])
    if not isinstance(menu, list):
        errors.append(f"{label}: menu must be a list")
        return
    for item in menu:
        if not isinstance(item, dict):
            errors.append(f"{label}: menu items must be maps")
            continue
        if not item.get("id"):
            errors.append(f"{label}: menu item missing id")
        if not item.get("title"):
            errors.append(f"{label}: menu item missing title")
        if not item.get("screen"):
            errors.append(f"{label}: menu item missing screen")


def _validate_local_component_dir(component_dir: Path, errors: List[str]) -> bool:
    if not component_dir.exists():
        errors.append("Local component missing: component/ directory not found")
        return False
    if not component_dir.is_dir():
        errors.append("Local component path must be a directory: component/")
        return False
    if not (component_dir / "CMakeLists.txt").exists():
        errors.append("Local component missing: component/CMakeLists.txt not found")
        return False
    return True


def _validate_local_sensor_interface(component_dir: Path, component_name: str, errors: List[str]) -> None:
    header_name = f"{component_name}.h"
    if not _component_file_exists(component_dir, header_name):
        errors.append(f"Local sensor component missing header: {header_name}")
    symbol_sources = _collect_component_sources(component_dir, source_only=True)
    symbol_all = _collect_component_sources(component_dir, source_only=False)
    for symbol in (f"{component_name}_init", f"{component_name}_start"):
        if not _component_has_symbol(symbol_all, symbol):
            errors.append(f"Local sensor component missing symbol: {symbol}()")
        elif not _component_has_symbol(symbol_sources, symbol):
            errors.append(f"Local sensor component missing implementation in .c/.cpp: {symbol}()")


def _validate_local_display_interface(component_dir: Path, errors: List[str]) -> None:
    if not _component_file_exists(component_dir, "display.h"):
        errors.append("Local display component missing header: display.h")
    symbol_sources = _collect_component_sources(component_dir, source_only=True)
    symbol_all = _collect_component_sources(component_dir, source_only=False)
    for symbol in ("display_init", "display_update_status"):
        if not _component_has_symbol(symbol_all, symbol):
            errors.append(f"Local display component missing symbol: {symbol}()")
        elif not _component_has_symbol(symbol_sources, symbol):
            errors.append(f"Local display component missing implementation in .c/.cpp: {symbol}()")


def _component_file_exists(component_dir: Path, filename: str) -> bool:
    return any(path.is_file() for path in component_dir.rglob(filename))


def _collect_component_sources(component_dir: Path, source_only: bool) -> List[str]:
    source_exts = {".c", ".cc", ".cpp", ".cxx"}
    header_exts = {".h", ".hh", ".hpp", ".hxx"}
    collected: List[str] = []
    for path in component_dir.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if source_only and ext not in source_exts:
            continue
        if not source_only and ext not in (source_exts | header_exts):
            continue
        try:
            collected.append(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return collected


def _component_has_symbol(source_texts: List[str], symbol: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    return any(pattern.search(content) for content in source_texts)


if __name__ == "__main__":
    raise SystemExit(main())
