from pathlib import Path

CANDIDATE = Path("genius/pro-code/smithery_control_plane/ci/akos-connector-policy.yml")
INSTALLED = Path(".github/workflows/akos-connector-policy.yml")
if CANDIDATE.is_file():
    INSTALLED.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED.write_bytes(CANDIDATE.read_bytes())
