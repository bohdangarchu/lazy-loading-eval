import re
import subprocess

from shared import log

STARGZ_CONFIG_PATH = "/etc/containerd-stargz-grpc/config.toml"


def _to_toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


def read_base_config() -> str:
    result = subprocess.run(
        ["sudo", "cat", STARGZ_CONFIG_PATH],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def apply_overrides(base_content: str, overrides: dict) -> str:
    """Replace existing key=value lines or append new ones for each override."""
    content = base_content
    for key, value in overrides.items():
        toml_val = _to_toml_value(value)
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key} = {toml_val}"
        if pattern.search(content):
            content = pattern.sub(replacement, content)
        else:
            section_match = re.search(r"^\[", content, re.MULTILINE)
            if section_match:
                idx = section_match.start()
                content = content[:idx].rstrip("\n") + f"\n{replacement}\n\n" + content[idx:]
            else:
                content = content.rstrip("\n") + f"\n{replacement}\n"
    return content


def apply_stargz_config(config_content: str) -> None:
    """Stop service, write config, start service."""
    current = read_base_config()
    log.info("--- applying stargz config ---")
    log.info(f"BEFORE:\n{current}")
    log.info(f"AFTER:\n{config_content}")
    tmp = "/tmp/stargz-config-measure.toml"
    with open(tmp, "w") as f:
        f.write(config_content)
    subprocess.run(["sudo", "systemctl", "stop", "stargz-snapshotter"], check=True)
    subprocess.run(["sudo", "cp", tmp, STARGZ_CONFIG_PATH], check=True)
    subprocess.run(["sudo", "systemctl", "start", "stargz-snapshotter"], check=True)
