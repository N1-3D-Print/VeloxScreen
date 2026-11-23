# Contributing to VeloxScreen

VeloxScreen is an early alpha. **Bug reports, suggestions, screenshots and code contributions are welcome.** You do not need to be a developer to help improve the project.

## Reporting a bug

Open an Issue using the **Bug report** template. Please describe:

1. The VeloxScreen version and whether this is a fresh install or update.
2. Raspberry Pi OS/architecture, screen model and resolution.
3. Klipper and Moonraker versions (when relevant).
4. What you tapped or entered, what you expected, and what happened instead.
5. Minimal steps to reproduce, plus sanitized logs or a screenshot.

Avoid posting API keys, passwords, private access URLs, full printer configs containing secrets, or personally identifying information.

## Requesting a feature

Please use the **Feature request** template and explain which printer configuration or workflow it would improve. Feature proposals are welcome; implementation is not guaranteed.

## Development notes

- Application code: `main.py` (Python 3 + PyQt5 Qt Widgets).
- UI reads printer state through Moonraker's HTTP endpoints and sends G-Code through Moonraker.
- Config is stored under `~/printer_data/config/veloxscreen.conf`.
- Keep all UI input touch-friendly and do not assume a physical keyboard.
- English is the default language. If adding visible strings, provide English first; German translations are optional.
- Keep safety limits and confirmation settings in place; do not silently bypass Klipper checks.
- Never include user printer settings, tokens or private logs in commits.

Check code syntax before opening a pull request:

```bash
python3 -m py_compile main.py
bash -n scripts/*.sh
```

Actual hardware testing is important. Describe what you did and did not test in the pull request.

## Licensing

Contributions are intended to be distributed under the repository's GNU GPL v3.0 license. Only submit code or artwork that you are permitted to share.
