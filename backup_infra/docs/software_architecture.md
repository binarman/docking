## Modules

There are few modules used by other tools:

- `manifest.py`: Contains infrastructure for managing manifest structure and it's serialization on disk.
- `unit_config.py`: Contains infrastructure for managing config for `unit.py`. It includes serialization on disk and parameter verification.
- `web_interfaces.py`: Contains html templates and methods to report status to the user, exchange status with other units and notification services

## Dependencies

### install.py

Depends on `unit_config.py`.

### unit.py

Depends on `manifest.py`, `unit_config.py`, `web_interfaces.py`.

### desktop-notify.py

Depends on `manifest.py`, `web_interfaces.py`.

### create_manifest.py

Depends on `manifest.py`.

### show_manifest.py

Depends on `manifest.py`.

### compare_manifests.py

Depends on `manifest.py`.