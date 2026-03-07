# Sensor Registry Contribution Guide

This guide explains exactly how to add a new community package to `sensor-registry.json`.

## 1. Fork the Registry Repository

1. Open the registry repository on GitHub.
2. Click `Fork`.
3. Clone your fork locally.

```bash
git clone https://github.com/<your-user>/<registry-repo>.git
cd <registry-repo>
```

## 2. Create Your Package Repository and Release Asset

Create your package in a separate repo (sensor package or display package), then publish a GitHub Release with a ZIP asset.

### 2.1 Sensor package structure

Your ZIP must contain this structure:

```text
<package-root>/
  sensor.yaml
  components_map.yaml
  menu.yaml (optional)
  component/
    CMakeLists.txt
    <component_name>.h
    <component_name>.c (or .cpp)
```

Example `sensor.yaml`:

```yaml
id: my_sensor
name: My Sensor
vendor: My Company
bus: i2c
description: Example sensor package
defaults:
  rate_hz: 10
  i2c:
    addr: 0x40
```

Example `components_map.yaml`:

```yaml
sensors:
  my_sensor:
    source: local
    name: community_my_sensor
```

Required functions in source:
- `community_my_sensor_init()`
- `community_my_sensor_start()`

### 2.2 Display package structure

```text
<package-root>/
  display.yaml
  components_map.yaml
  menu.yaml (optional)
  component/
    CMakeLists.txt
    display.h
    display.c (or .cpp)
```

Example `components_map.yaml`:

```yaml
displays:
  my_display:
    source: local
    name: display
```

Required functions in source:
- `display_init()`
- `display_update_status()`

### 2.3 Create a GitHub Release

1. Upload your package ZIP as a release asset.
2. Copy the direct download URL.  
   Example:
   `https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>.zip`

## 3. Generate SHA256 for the Release ZIP

Run this in your terminal:

```bash
curl -L "<ZIP_URL>" | sha256sum
```

Copy the first 64-character lowercase hash.

## 4. Add Package Entry to `sensor-registry.json`

Edit `sensor-registry.json` in your fork and add:

```json
[
  {
    "name": "community-my-sensor",
    "version": "1.0.0",
    "description": "My sensor package",
    "url": "https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>.zip",
    "sha256": "<64-char-sha256>"
  }
]
```

## 5. Validate Locally Before PR

```bash
python -m pip install --upgrade pip
pip install pyyaml
python scripts/validate_registry.py sensor-registry.json
```

If all checks pass, continue.

## 6. Open Pull Request

1. Push your branch to your fork.
2. Open PR to the upstream registry repo.
3. Ensure PR changes only `sensor-registry.json` (fork policy).
4. Wait for `Registry Validation / validate` check to pass.

## 7. Merge Rules

PR can be merged only when:
1. Required status checks pass.
2. Branch protection rules are satisfied.

## Common Errors and Fixes

1. `Missing required field: sha256`  
Fix: Add SHA256 hash for that package entry.

2. `sha256 mismatch`  
Fix: Recalculate hash from the exact final release asset URL.

3. `Could not find package root with sensor.yaml or display.yaml`  
Fix: ZIP layout is wrong. Ensure package files are inside package root as shown.

4. `Local component missing implementation ... _start()`  
Fix: Implement required functions in `.c` or `.cpp`.

5. `Fork PRs can only change sensor-registry.json`  
Fix: Remove changes to other files from your PR.
