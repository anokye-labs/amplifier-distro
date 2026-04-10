# Amplifier Distro

The complete distribution layer for Amplifier. Handles install, onboarding, and bundles a curated set of experience apps and optional capabilities.

## Tech Stack
- Python 3.11+
- Built around the AMPLIFIER_HOME_CONTRACT filesystem spec

## Development
```bash
pip install -e ".[dev]"
python -m pytest
```

## Structure
Distribution packaging and installer for the Amplifier platform.

## Conventions
- Follow existing code patterns
- Include type hints
- Respect the AMPLIFIER_HOME_CONTRACT
