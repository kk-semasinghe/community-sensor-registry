from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, List, Tuple

sys.path.insert(0, str(Path(__file__).parents[1] / "code_generator"))

from core.validator import validate_package_dir

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


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
    issues: List[str] = []

    url = str(pkg.get("url") or "")
    version = str(pkg.get("version") or "")
    sha256 = str(pkg.get("sha256") or "").lower()

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
    if version:
        return f"{name} (v{version})"
    return name


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
    candidates = [d for d in root.iterdir() if d.is_dir()]
    for d in candidates:
        if (d / "sensor.yaml").exists() or (d / "display.yaml").exists():
            return d
    return None


def run_validator(pkg_root: Path) -> List[str]:
    errors = validate_package_dir(pkg_root)
    if not errors:
        return []
    return [f"Package validation error: {err}" for err in errors]


if __name__ == "__main__":
    raise SystemExit(main())
