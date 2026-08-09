"""Validate the public One Cent Outcomes release contract without network access."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import yaml


EXPECTED_SKILL_IDS = frozenset({"outcome-offer", "proof-pack", "reply-to-close"})
EXPECTED_SKILL_ORDER = ("outcome-offer", "proof-pack", "reply-to-close")
ALLOWED_SKILL_FRONTMATTER = frozenset(
    {"name", "description", "license", "allowed-tools", "metadata"}
)
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
PLACEHOLDER_COPY = re.compile(r"(?i)(?<![A-Za-z0-9_-])(TODO|TBD|CHANGEME)(?![A-Za-z0-9_-])")
PRIVATE_KEY_MATERIAL = re.compile(
    br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE
)
TOKEN_MATERIAL = (
    re.compile(br"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(br"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(br"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(br"\b0x[0-9a-fA-F]{64}\b"),
)
SECRET_ASSIGNMENT = re.compile(
    br"(?i)(?:sm4[_-]?key|api[_-]?key|auth[_-]?token|wallet[_-]?key|"
    br"payment[_-]?credential)\s*[\"']?\s*[:=]\s*[\"']?"
    br"(?P<value>[A-Za-z0-9+/=_-]{16,})[\"']?"
)
SAFE_SECRET_PLACEHOLDER_PREFIXES = (b"replace-", b"replace_", b"your-", b"your_")
FORBIDDEN_FILE_SUFFIXES = frozenset(
    {".db", ".key", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}
)
EXAMPLE_HEADINGS = {
    "outcome-offer": (
        "### From-to outcome",
        "### Product name",
        "### Buyer",
        "### Buying moment",
        "### Deliverables",
        "### Three benefits",
        "### Risk reversal",
        "### Three headlines",
        "### Paste-ready sales block",
        "## Fixture scoring",
    ),
    "proof-pack": (
        "## PROOF HEADLINE",
        "## PROPOSAL BLURB",
        "## CASE STORY",
        "## EVIDENCE BULLETS",
        "## SOCIAL POST",
        "## SALES-CONVERSATION VERSION",
        "## CLAIM TRACEABILITY",
        "## MISSING EVIDENCE",
        "## QUALITY CHECK",
    ),
    "reply-to-close": (
        "## COPY-PASTE REPLY",
        "## SHORT REPLY",
        "## OBJECTION CLASSIFICATION",
        "## LOW-FRICTION NEXT STEP",
        "## ASSUMPTIONS AND TRACEABILITY",
        "## QUALITY CHECK",
    ),
}


def _read_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"missing plugin manifest: {manifest_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid plugin manifest: {manifest_path}: {error.msg}") from error
    if not isinstance(manifest, dict):
        raise ValueError("plugin manifest must contain a JSON object")
    return manifest


def validate_product_skills(root: Path) -> None:
    """Require exactly the three public product Skill directories."""
    skills_root = root / "skills"
    if not skills_root.is_dir():
        raise ValueError(f"missing public Skills directory: {skills_root}")
    actual_skill_ids = {
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
    }
    if actual_skill_ids != EXPECTED_SKILL_IDS:
        missing = sorted(EXPECTED_SKILL_IDS - actual_skill_ids)
        extra = sorted(actual_skill_ids - EXPECTED_SKILL_IDS)
        details = []
        if missing:
            details.append(f"missing product Skills: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected product Skills: {', '.join(extra)}")
        raise ValueError("; ".join(details))
    for skill_id in EXPECTED_SKILL_ORDER:
        if not (skills_root / skill_id / "SKILL.md").is_file():
            raise ValueError(f"missing product Skill manifest: {skill_id}/SKILL.md")


def _frontmatter_values(skill_file: Path) -> dict[str, Any]:
    contents = skill_file.read_text(encoding="ascii")
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", contents, re.DOTALL)
    if match is None:
        raise ValueError(f"Skill quick validation failed for {skill_file.parent.name}: invalid frontmatter")
    try:
        values = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise ValueError(
            f"Skill quick validation failed for {skill_file.parent.name}: invalid YAML"
        ) from error
    if not isinstance(values, dict) or not all(isinstance(key, str) for key in values):
        raise ValueError(
            f"Skill quick validation failed for {skill_file.parent.name}: "
            "frontmatter must be an object with string keys"
        )
    return values


def _fallback_quick_validate(skill_root: Path) -> None:
    skill_file = skill_root / "SKILL.md"
    values = _frontmatter_values(skill_file)
    unexpected = set(values) - ALLOWED_SKILL_FRONTMATTER
    if unexpected:
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: unexpected frontmatter "
            f"{', '.join(sorted(unexpected))}"
        )
    if "name" not in values or "description" not in values:
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: name and description are required"
        )
    name = values["name"]
    description = values["description"]
    if not isinstance(name, str):
        raise ValueError(f"Skill quick validation failed for {skill_root.name}: name must be a string")
    if not isinstance(description, str):
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: description must be a string"
        )
    name = name.strip()
    description = description.strip()
    if not name or not description:
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: name and description cannot be empty"
        )
    if name != skill_root.name or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: invalid or mismatched name"
        )
    if len(name) > 64 or len(description) > 1024 or "<" in description or ">" in description:
        raise ValueError(
            f"Skill quick validation failed for {skill_root.name}: invalid name or description"
        )


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _official_validator(relative_path: Path, override_name: str) -> Path | None:
    override = os.environ.get(override_name)
    candidates = [Path(override).expanduser()] if override else []
    candidates.append(_codex_home() / "skills" / ".system" / relative_path)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _run_validator(validator: Path, target: Path, label: str) -> None:
    result = subprocess.run(
        [sys.executable, str(validator), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        output = (result.stdout + "\n" + result.stderr).strip()
        raise ValueError(f"{label} failed: {output or f'exit {result.returncode}'}")


def validate_skills(root: Path) -> None:
    """Keep public Skill files ASCII and run the official quick check when installed."""
    validator = _official_validator(
        Path("skill-creator/scripts/quick_validate.py"),
        "ONE_CENT_OUTCOMES_SKILL_VALIDATOR",
    )
    for skill_id in EXPECTED_SKILL_ORDER:
        skill_root = root / "skills" / skill_id
        skill_file = skill_root / "SKILL.md"
        try:
            skill_file.read_bytes().decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError(f"public Skill must remain ASCII: {skill_file}") from error
        if validator is not None:
            _run_validator(validator, skill_root, f"official quick validator for {skill_id}")
        else:
            _fallback_quick_validate(skill_root)


def _reject_unknown_fields(
    payload: dict[Any, Any], allowed: set[str], label: str
) -> None:
    if not all(isinstance(key, str) for key in payload):
        raise ValueError(f"{label} fields must have string names")
    unexpected = set(payload) - allowed
    if unexpected:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(unexpected))}")


def _require_nonempty_string(
    payload: dict[str, Any], field: str, *, label: str = "plugin manifest"
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} field {field!r} must be a non-empty string")
    return value


def _validate_optional_nonempty_string(
    payload: dict[str, Any], field: str, *, label: str
) -> None:
    if payload.get(field) is not None:
        _require_nonempty_string(payload, field, label=label)


def _validate_optional_https_url(
    payload: dict[str, Any], field: str, *, label: str
) -> None:
    value = payload.get(field)
    if value is None:
        return
    parsed = urlparse(value) if isinstance(value, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} field {field!r} must be an absolute HTTPS URL")


def _validate_asset_path(
    *, base_dir: Path, allowed_root: Path, raw_path: Any, label: str
) -> None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} asset path must be a non-empty relative path")
    candidate = PurePosixPath(raw_path.replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"{label} asset path must stay inside the plugin archive")
    try:
        resolved = (base_dir / candidate.as_posix()).resolve()
        allowed = allowed_root.resolve()
    except OSError as error:
        raise ValueError(f"{label} asset path cannot be resolved") from error
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"{label} asset path must stay inside the plugin archive")
    if not resolved.is_file():
        raise ValueError(f"{label} asset path points to a missing file")


def _fallback_agent_validation(skill_root: Path) -> None:
    agent_path = skill_root / "agents" / "openai.yaml"
    if not agent_path.is_file():
        return
    try:
        payload = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"skill agent {skill_root.name} must contain valid YAML") from error
    if not isinstance(payload, dict):
        raise ValueError(f"skill agent {skill_root.name} must be an object")
    _reject_unknown_fields(
        payload, {"interface", "policy", "dependencies"}, f"skill agent {skill_root.name}"
    )
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        raise ValueError(f"skill agent {skill_root.name} interface must be an object")
    allowed_interface = {
        "display_name",
        "short_description",
        "icon_small",
        "icon_large",
        "brand_color",
        "default_prompt",
    }
    _reject_unknown_fields(
        interface, allowed_interface, f"skill agent {skill_root.name} interface"
    )
    for field in ("display_name", "short_description"):
        value = interface.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"skill agent {skill_root.name} interface.{field} must be a non-empty string"
            )
    default_prompt = interface.get("default_prompt")
    if default_prompt is not None and (
        not isinstance(default_prompt, str) or not default_prompt.strip()
    ):
        raise ValueError(
            f"skill agent {skill_root.name} interface.default_prompt must be a non-empty string"
        )
    brand_color = interface.get("brand_color")
    if brand_color is not None and (
        not isinstance(brand_color, str)
        or re.fullmatch(r"#[0-9A-Fa-f]{6}", brand_color) is None
    ):
        raise ValueError(
            f"skill agent {skill_root.name} interface.brand_color must use #RRGGBB"
        )
    plugin_root = skill_root.parent.parent
    for field in ("icon_small", "icon_large"):
        raw_path = interface.get(field)
        if raw_path is not None:
            _validate_asset_path(
                base_dir=skill_root,
                allowed_root=plugin_root,
                raw_path=raw_path,
                label=f"skill agent {skill_root.name} interface.{field}",
            )
    policy = payload.get("policy")
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError(f"skill agent {skill_root.name} policy must be an object")
        _reject_unknown_fields(
            policy, {"allow_implicit_invocation"}, f"skill agent {skill_root.name} policy"
        )
        allowed = policy.get("allow_implicit_invocation")
        if allowed is not None and not isinstance(allowed, bool):
            raise ValueError(
                f"skill agent {skill_root.name} policy.allow_implicit_invocation must be a boolean"
            )
    dependencies = payload.get("dependencies")
    if dependencies is not None:
        if not isinstance(dependencies, dict):
            raise ValueError(f"skill agent {skill_root.name} dependencies must be an object")
        _reject_unknown_fields(
            dependencies, {"tools"}, f"skill agent {skill_root.name} dependencies"
        )


def _fallback_plugin_validation(root: Path, manifest: dict[str, Any]) -> None:
    for unsupported_companion in ("apps", "mcpServers"):
        if unsupported_companion in manifest:
            raise ValueError(
                f"fallback plugin validation does not support {unsupported_companion!r}; "
                "the official plugin validator is required"
            )
    allowed = {
        "id",
        "name",
        "version",
        "description",
        "skills",
        "apps",
        "mcpServers",
        "interface",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
    _reject_unknown_fields(manifest, allowed, "plugin manifest")
    _validate_optional_nonempty_string(manifest, "id", label="plugin manifest")
    for field in ("name", "version", "description"):
        _require_nonempty_string(manifest, field)
    if SEMVER.fullmatch(str(manifest["version"])) is None:
        raise ValueError("plugin manifest version must be strict semver")
    author = manifest.get("author")
    if not isinstance(author, dict):
        raise ValueError("plugin manifest field 'author' must be an object")
    _reject_unknown_fields(author, {"name", "email", "url"}, "plugin author")
    _require_nonempty_string(author, "name", label="plugin author")
    _validate_optional_nonempty_string(author, "email", label="plugin author")
    _validate_optional_https_url(author, "url", label="plugin author")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise ValueError("plugin manifest field 'interface' must be an object")
    allowed_interface = {
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "websiteURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "brandColor",
        "composerIcon",
        "logo",
        "logoDark",
        "screenshots",
        "defaultPrompt",
        "default_prompt",
    }
    _reject_unknown_fields(interface, allowed_interface, "plugin interface")
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        _require_nonempty_string(interface, field, label="plugin interface")
    if "defaultPrompt" not in interface and "default_prompt" not in interface:
        raise ValueError("plugin interface requires defaultPrompt or default_prompt")
    _validate_optional_nonempty_string(interface, "defaultPrompt", label="plugin interface")
    _validate_optional_nonempty_string(interface, "default_prompt", label="plugin interface")
    capabilities = interface.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and item.strip() for item in capabilities
    ):
        raise ValueError("plugin interface capabilities must be an array of non-empty strings")
    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        _validate_optional_https_url(interface, field, label="plugin interface")
    brand_color = interface.get("brandColor")
    if brand_color is not None and (
        not isinstance(brand_color, str)
        or re.fullmatch(r"#[0-9A-Fa-f]{6}", brand_color) is None
    ):
        raise ValueError("plugin interface brandColor must use #RRGGBB")
    for field in ("composerIcon", "logo", "logoDark"):
        raw_path = interface.get(field)
        if raw_path is not None:
            _validate_asset_path(
                base_dir=root,
                allowed_root=root,
                raw_path=raw_path,
                label=f"plugin interface.{field}",
            )
    screenshots = interface.get("screenshots", [])
    if not isinstance(screenshots, list):
        raise ValueError("plugin interface screenshots must be an array")
    for index, raw_path in enumerate(screenshots):
        _validate_asset_path(
            base_dir=root,
            allowed_root=root,
            raw_path=raw_path,
            label=f"plugin interface.screenshots[{index}]",
        )
    for skill_id in EXPECTED_SKILL_ORDER:
        _fallback_agent_validation(root / "skills" / skill_id)


def validate_plugin(root: Path, manifest: dict[str, Any]) -> None:
    validator = _official_validator(
        Path("plugin-creator/scripts/validate_plugin.py"),
        "ONE_CENT_OUTCOMES_PLUGIN_VALIDATOR",
    )
    if validator is not None:
        _run_validator(validator, root, "official plugin validator")
    else:
        _fallback_plugin_validation(root, manifest)


def validate_examples(root: Path) -> None:
    for skill_id, required_headings in EXAMPLE_HEADINGS.items():
        example = root / "examples" / f"{skill_id}.md"
        try:
            contents = example.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ValueError(f"example contract missing for {skill_id}: {example}") from error
        missing = [heading for heading in required_headings if contents.count(heading) != 1]
        if missing:
            raise ValueError(
                f"example contract invalid for {skill_id}; headings must occur exactly once: "
                + ", ".join(missing)
            )


def _git_release_files(root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode:
        return None
    return [root / Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw]


def _fallback_release_files(root: Path) -> list[Path]:
    ignored_parts = frozenset({".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
    return [
        path
        for path in root.rglob("*")
        if (path.is_file() or _is_link_or_reparse(path))
        and not ignored_parts.intersection(path.relative_to(root).parts)
    ]


def _release_files(root: Path) -> list[Path]:
    files = _git_release_files(root)
    return files if files is not None else _fallback_release_files(root)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_forbidden_path(relative: Path) -> bool:
    lower_parts = tuple(part.lower() for part in relative.parts)
    name = relative.name.lower()
    suffix = relative.suffix.lower()
    if ".superpowers" in lower_parts or ".worktrees" in lower_parts:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if suffix in FORBIDDEN_FILE_SUFFIXES:
        return True
    if suffix in {".pyc", ".pyd", ".pyo"} or "__pycache__" in lower_parts:
        return True
    return (
        ("wallet" in name and suffix not in {".md", ".py"})
        or "payment-record" in name
        or "payment_record" in name
        or "release-ledger" in name
        or "release_ledger" in name
        or name in {"credentials", "credentials.json", "secrets", "secrets.json"}
    )


def _is_private_prompt_contract_path(relative: Path) -> bool:
    parts = tuple(part.lower() for part in relative.parts)
    return len(parts) >= 2 and parts[:2] == ("gateway", "prompts")


def _iter_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, child in value.items():
            if isinstance(key, str):
                strings.append(key)
            strings.extend(_iter_string_values(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_iter_string_values(child))
        return strings
    return []


def _validate_private_prompt_packaging(root: Path) -> None:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return
    try:
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"invalid pyproject.toml: {error}") from error
    setuptools = project.get("tool", {}).get("setuptools", {})
    for value in _iter_string_values(setuptools):
        normalized = value.replace("\\", "/").replace(".", "/").lower().lstrip("./")
        if normalized == "gateway/prompts" or normalized.startswith("gateway/prompts/"):
            raise ValueError("private prompt contract must not be packaged from gateway/prompts")
    package_data = setuptools.get("package-data", {}) if isinstance(setuptools, dict) else {}
    if isinstance(package_data, dict):
        for package, patterns in package_data.items():
            if not isinstance(package, str) or not package.startswith("gateway"):
                continue
            for pattern in _iter_string_values(patterns):
                normalized = pattern.replace("\\", "/").lower().lstrip("./")
                if normalized == "prompts" or normalized.startswith("prompts/"):
                    raise ValueError(
                        "private prompt contract must not be packaged from gateway/prompts"
                    )


def _is_release_copy(relative: Path) -> bool:
    posix = relative.as_posix()
    return (
        relative.name in {
            "README.md",
            "README.zh-CN.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
        }
        or posix == ".codex-plugin/plugin.json"
        or posix == "docs/payments.md"
        or posix == "docs/deploy.md"
        or posix == "docs/activate-live-payments.md"
        or posix.startswith("skills/")
        or posix.startswith("examples/")
    )


def _read_release_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ValueError(f"unable to scan release file: {path}") from error


def _contains_secret_like_material(data: bytes) -> bool:
    if PRIVATE_KEY_MATERIAL.search(data):
        return True
    if any(pattern.search(data) for pattern in TOKEN_MATERIAL):
        return True
    for match in SECRET_ASSIGNMENT.finditer(data):
        value = match.group("value").lower()
        if not value.startswith(SAFE_SECRET_PLACEHOLDER_PREFIXES):
            return True
    return False


def validate_release_contents(root: Path) -> None:
    _validate_private_prompt_packaging(root)
    for path in _release_files(root):
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise ValueError(f"release file escaped repository root: {path}") from None
        if _is_link_or_reparse(path):
            raise ValueError(f"forbidden release path (symbolic link): {relative.as_posix()}")
        if _is_private_prompt_contract_path(relative):
            raise ValueError(
                f"private prompt contract must stay outside the release: {relative.as_posix()}"
            )
        if _is_forbidden_path(relative):
            raise ValueError(f"forbidden release path: {relative.as_posix()}")
        data = _read_release_bytes(path)
        if _contains_secret_like_material(data):
            raise ValueError(f"secret-like material found in: {relative.as_posix()}")
        if _is_release_copy(relative):
            try:
                release_text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"release copy must be valid UTF-8: {relative.as_posix()}"
                ) from error
            if PLACEHOLDER_COPY.search(release_text):
                raise ValueError(f"placeholder copy found in: {relative.as_posix()}")


def validate_repository(root: Path) -> None:
    """Run deterministic local release checks for manifest, Skills, examples, and files."""
    root = Path(root).resolve()
    manifest = _read_manifest(root)
    if manifest.get("skills") != "./skills/":
        raise ValueError('plugin manifest field "skills" must equal "./skills/"')
    validate_product_skills(root)
    validate_skills(root)
    validate_plugin(root, manifest)
    validate_examples(root)
    validate_release_contents(root)


def main(argv: list[str] | None = None) -> int:
    arguments = argv if argv is not None else sys.argv[1:]
    root = Path(arguments[0]) if arguments else Path.cwd()
    try:
        validate_repository(root)
    except (OSError, ValueError) as error:
        print(f"Repository validation failed: {error}", file=sys.stderr)
        return 1
    print("Repository validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
