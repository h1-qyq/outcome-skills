from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

import scripts.validate_repo as repository_validator
from scripts.validate_repo import validate_repository


ROOT = Path(__file__).parents[1]
INSTALLER_PATH = ROOT / "scripts" / "install.py"
SKILL_IDS = ("outcome-offer", "proof-pack", "reply-to-close")


def load_installer():
    assert INSTALLER_PATH.exists(), "scripts/install.py is missing"
    spec = importlib.util.spec_from_file_location("one_cent_outcomes_installer", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("target", "scope", "relative_destination"),
    [
        ("codex", "project", Path("project/.agents/skills")),
        ("codex", "user", Path("home/.agents/skills")),
        ("joycode", "project", Path("project/.joycode/skills")),
        ("joycode", "user", Path("home/.joycode/skills")),
        ("openclaw", "project", Path("project/skills")),
        ("openclaw", "user", Path("home/.openclaw/skills")),
    ],
)
def test_installer_maps_all_target_and_scope_destinations(tmp_path, target, scope, relative_destination):
    installer = load_installer()

    destination = installer.destination_root(
        target,
        scope,
        project_root=tmp_path / "project",
        home_root=tmp_path / "home",
    )

    assert destination == tmp_path / relative_destination


def test_installer_dry_run_makes_no_writes(tmp_path):
    installer = load_installer()
    destination = tmp_path / "destination"

    planned = installer.install_skills(ROOT, destination, dry_run=True)

    assert [path.name for path in planned] == list(SKILL_IDS)
    assert not destination.exists()


def test_installer_can_install_one_named_skill_without_the_other_two(tmp_path):
    installer = load_installer()
    destination = tmp_path / "destination"

    installed = installer.install_skills(
        ROOT,
        destination,
        skill_ids=("proof-pack",),
    )

    assert [path.name for path in installed] == ["proof-pack"]
    assert (destination / "proof-pack" / "SKILL.md").is_file()
    assert not (destination / "outcome-offer").exists()
    assert not (destination / "reply-to-close").exists()


def test_installer_copies_exactly_three_complete_skill_directories(tmp_path):
    installer = load_installer()
    destination = tmp_path / "destination"

    installed = installer.install_skills(ROOT, destination)

    assert [path.name for path in installed] == list(SKILL_IDS)
    assert sorted(path.parent.name for path in destination.glob("*/SKILL.md")) == list(SKILL_IDS)
    for skill_id in SKILL_IDS:
        source_files = {
            path.relative_to(ROOT / "skills" / skill_id)
            for path in (ROOT / "skills" / skill_id).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}
            and path.name not in {".DS_Store", "Thumbs.db"}
        }
        installed_files = {
            path.relative_to(destination / skill_id)
            for path in (destination / skill_id).rglob("*")
            if path.is_file()
        }
        assert installed_files == source_files
        assert {
            Path("SKILL.md"),
            Path("scripts/client.py"),
            Path("references/quality-rubric.md"),
            Path("agents/openai.yaml"),
        } <= installed_files
    assert not (destination / "gateway").exists()
    assert not (destination / ".superpowers").exists()
    assert not (destination / ".git").exists()


def test_installer_excludes_runtime_and_ignored_residue_from_skill_sources(tmp_path):
    installer = load_installer()
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "skills", repository / "skills")
    skill = repository / "skills" / "proof-pack"
    (skill / "scripts" / "__pycache__").mkdir(exist_ok=True)
    (skill / "scripts" / "__pycache__" / "client.cpython-311.pyc").write_bytes(b"bytecode")
    (skill / "orders.sqlite3").write_bytes(b"database")
    (skill / ".DS_Store").write_bytes(b"metadata")
    (skill / ".coverage").write_bytes(b"coverage")
    (skill / "build").mkdir()
    (skill / "build" / "generated.txt").write_text("generated", encoding="utf-8")
    (skill / "wallet.key").write_text("secret-shaped", encoding="utf-8")

    destination = tmp_path / "destination"
    installer.install_skills(repository, destination)

    assert not (destination / "proof-pack" / "scripts" / "__pycache__").exists()
    assert not (destination / "proof-pack" / "orders.sqlite3").exists()
    assert not (destination / "proof-pack" / ".DS_Store").exists()
    assert not (destination / "proof-pack" / ".coverage").exists()
    assert not (destination / "proof-pack" / "build").exists()
    assert not (destination / "proof-pack" / "wallet.key").exists()


def test_installer_excludes_worktree_and_native_bytecode_residue(tmp_path):
    installer = load_installer()
    repository = tmp_path / "repository"
    shutil.copytree(
        ROOT / "skills",
        repository / "skills",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    skill = repository / "skills" / "proof-pack"
    worktree_residue = skill / ".worktrees" / "review-copy"
    worktree_residue.mkdir(parents=True)
    (worktree_residue / "outside.txt").write_text("ignored", encoding="utf-8")
    (skill / "scripts" / "native-extension.pyd").write_bytes(b"native bytecode")

    destination = tmp_path / "destination"
    installer.install_skills(repository, destination)

    assert not (destination / "proof-pack" / ".worktrees").exists()
    assert not (destination / "proof-pack" / "scripts" / "native-extension.pyd").exists()


def test_installer_refuses_symbolic_links_in_skill_sources(tmp_path):
    installer = load_installer()
    repository = tmp_path / "repository"
    shutil.copytree(
        ROOT / "skills",
        repository / "skills",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    link = repository / "skills" / "proof-pack" / "references" / "outside.txt"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows host")

    with pytest.raises(installer.InstallError, match="symbolic link|reparse point"):
        installer.install_skills(repository, tmp_path / "destination")

    assert not (tmp_path / "destination").exists()


def test_installer_refuses_any_collision_before_writing(tmp_path):
    installer = load_installer()
    destination = tmp_path / "destination"
    existing = destination / "proof-pack"
    existing.mkdir(parents=True)
    marker = existing / "owned-by-user.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(installer.InstallError, match="already exists"):
        installer.install_skills(ROOT, destination)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (destination / "outcome-offer").exists()
    assert not (destination / "reply-to-close").exists()


def test_installer_force_replaces_complete_same_name_directories(tmp_path):
    installer = load_installer()
    destination = tmp_path / "destination"
    for skill_id in SKILL_IDS:
        existing = destination / skill_id
        existing.mkdir(parents=True)
        (existing / "stale.txt").write_text("stale", encoding="utf-8")

    installer.install_skills(ROOT, destination, force=True)

    for skill_id in SKILL_IDS:
        assert not (destination / skill_id / "stale.txt").exists()
        assert (destination / skill_id / "SKILL.md").read_bytes() == (
            ROOT / "skills" / skill_id / "SKILL.md"
        ).read_bytes()


def test_installer_force_copy_failure_preserves_existing_skill(tmp_path, monkeypatch):
    installer = load_installer()
    destination = tmp_path / "destination"
    existing = destination / "proof-pack"
    existing.mkdir(parents=True)
    marker = existing / "owned-by-user.txt"
    marker.write_text("keep", encoding="utf-8")

    def fail_copy(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copy)

    with pytest.raises(installer.InstallError, match="copy failed"):
        installer.install_skills(ROOT, destination, force=True)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not any(path.name.startswith(".one-cent-outcomes-") for path in tmp_path.rglob("*"))


def test_installer_force_replacement_failure_restores_all_existing_skills(tmp_path, monkeypatch):
    installer = load_installer()
    destination = tmp_path / "destination"
    for skill_id in SKILL_IDS:
        existing = destination / skill_id
        existing.mkdir(parents=True)
        (existing / "owned-by-user.txt").write_text(skill_id, encoding="utf-8")

    real_replace = installer.os.replace
    staged_moves = 0

    def fail_second_staged_move(source, target):
        nonlocal staged_moves
        source_path = Path(source)
        if source_path.parent.name == "staged":
            staged_moves += 1
            if staged_moves == 2:
                raise OSError("simulated replacement failure")
        return real_replace(source, target)

    monkeypatch.setattr(installer.os, "replace", fail_second_staged_move)

    with pytest.raises(installer.InstallError, match="replacement failed"):
        installer.install_skills(ROOT, destination, force=True)

    for skill_id in SKILL_IDS:
        assert (destination / skill_id / "owned-by-user.txt").read_text(
            encoding="utf-8"
        ) == skill_id
        assert not (destination / skill_id / "SKILL.md").exists()
    assert not any(path.name.startswith(".one-cent-outcomes-") for path in tmp_path.iterdir())


def test_installer_preserves_recovery_backup_when_rollback_fails(tmp_path, monkeypatch):
    installer = load_installer()
    destination = tmp_path / "destination"
    for skill_id in SKILL_IDS:
        existing = destination / skill_id
        existing.mkdir(parents=True)
        (existing / "owned-by-user.txt").write_text(skill_id, encoding="utf-8")

    real_replace = installer.os.replace
    replacement_failed = False

    def fail_replacement_and_one_restore(source, target):
        nonlocal replacement_failed
        source_path = Path(source)
        if source_path.parent.name == "staged" and source_path.name == "proof-pack":
            replacement_failed = True
            raise OSError("simulated replacement failure")
        if (
            replacement_failed
            and source_path.parent.name == "backups"
            and source_path.name == "proof-pack"
        ):
            raise OSError("simulated rollback failure")
        return real_replace(source, target)

    monkeypatch.setattr(installer.os, "replace", fail_replacement_and_one_restore)

    with pytest.raises(installer.InstallError, match="recovery backup preserved"):
        installer.install_skills(ROOT, destination, force=True)

    recovery_roots = list(tmp_path.glob(".one-cent-outcomes-*"))
    assert len(recovery_roots) == 1
    recovery = recovery_roots[0]
    assert (recovery / "backups" / "proof-pack" / "owned-by-user.txt").read_text(
        encoding="utf-8"
    ) == "proof-pack"
    assert (destination / "outcome-offer" / "owned-by-user.txt").read_text(
        encoding="utf-8"
    ) == "outcome-offer"
    assert (destination / "reply-to-close" / "owned-by-user.txt").read_text(
        encoding="utf-8"
    ) == "reply-to-close"


def test_openclaw_active_workspace_checkout_needs_no_copy():
    installer = load_installer()

    installed = installer.install_skills(ROOT, ROOT / "skills")

    assert installed == tuple(ROOT / "skills" / skill_id for skill_id in SKILL_IDS)


def test_release_surface_has_bilingual_outcome_first_copy_and_health_files():
    required = {
        "README.md",
        "README.zh-CN.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        ".github/workflows/validate.yml",
        "docs/deploy.md",
        "docs/activate-live-payments.md",
    }
    assert not [path for path in sorted(required) if not (ROOT / path).is_file()]

    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "One brief in. One finished result out." in english
    assert "Free public test: get one useful sales asset you can use immediately." in english
    assert "No prompt engineering. No subscription." in english
    assert "免费公测" in chinese
    assert "README.zh-CN.md" in english
    assert "README.md" in chinese
    assert "pay only after production" not in (english + chinese).lower()
    for skill_id in SKILL_IDS:
        assert skill_id in english
        assert skill_id in chinese


def test_ci_is_read_only_python_311_and_runs_all_release_gates():
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")

    assert "contents: read" in workflow
    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow
    assert 'python-version: "3.11"' in workflow
    assert "python -m pytest -q" in workflow
    assert "python scripts/validate_repo.py" in workflow
    assert "git diff --exit-code" in workflow
    assert "deploy" not in workflow.lower()
    assert "publish" not in workflow.lower()


def test_release_workflow_publishes_three_independent_skill_archives():
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "contents: write" in workflow
    assert "gh release create" in workflow
    assert "tags:" in workflow
    for skill_id in SKILL_IDS:
        assert skill_id in workflow


def copy_validator_fixture(tmp_path: Path) -> Path:
    for relative in (".codex-plugin", "skills", "examples"):
        shutil.copytree(
            ROOT / relative,
            tmp_path / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    shutil.copy2(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_release_validator_rejects_placeholder_copy(tmp_path):
    root = copy_validator_fixture(tmp_path)
    (root / "README.md").write_text("Launch copy: TODO", encoding="utf-8")

    with pytest.raises(ValueError, match="placeholder"):
        validate_repository(root)


@pytest.mark.parametrize("relative_path", ["wallet.key", "orders.sqlite3", ".superpowers/release-ledger.json"])
def test_release_validator_rejects_secret_database_and_development_paths(tmp_path, relative_path):
    root = copy_validator_fixture(tmp_path)
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sensitive", encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden release path"):
        validate_repository(root)


def test_release_validator_rejects_private_key_material(tmp_path):
    root = copy_validator_fixture(tmp_path)
    (root / "README.md").write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="secret-like material"):
        validate_repository(root)


def test_release_validator_rejects_non_ascii_public_skill(tmp_path):
    root = copy_validator_fixture(tmp_path)
    skill = root / "skills" / "proof-pack" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="ascii") + "\nnon-ascii: café\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ASCII"):
        validate_repository(root)


def test_release_validator_fallback_rejects_non_string_skill_description(tmp_path, monkeypatch):
    root = copy_validator_fixture(tmp_path)
    skill = root / "skills" / "proof-pack" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="ascii").replace(
            "description: Use when a buyer wants to turn a verified result, metric, note, or exact testimonial into a sales-ready proof pack during the free public test period.",
            "description: [not, a, string]",
        ),
        encoding="ascii",
    )
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="description.*string"):
        repository_validator.validate_repository(root)


def test_release_validator_fallback_rejects_broken_agent_manifest(tmp_path, monkeypatch):
    root = copy_validator_fixture(tmp_path)
    agent_manifest = root / "skills" / "proof-pack" / "agents" / "openai.yaml"
    agent_manifest.write_text("interface: [not-an-object]\n", encoding="utf-8")
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="agent.*interface.*object"):
        repository_validator.validate_repository(root)


def test_release_validator_fallback_rejects_unknown_plugin_interface_field(
    tmp_path, monkeypatch
):
    root = copy_validator_fixture(tmp_path)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["interface"]["credentialField"] = "must-not-be-accepted"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="interface.*unsupported"):
        repository_validator.validate_repository(root)


def test_release_validator_fallback_rejects_unvalidated_apps_field(tmp_path, monkeypatch):
    root = copy_validator_fixture(tmp_path)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apps"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="fallback.*apps"):
        repository_validator.validate_repository(root)


def test_release_validator_fallback_rejects_unvalidated_mcp_servers_field(
    tmp_path, monkeypatch
):
    root = copy_validator_fixture(tmp_path)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mcpServers"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="fallback.*mcpServers"):
        repository_validator.validate_repository(root)


def test_release_validator_fallback_rejects_agent_asset_path_escape(tmp_path, monkeypatch):
    root = copy_validator_fixture(tmp_path)
    agent_manifest = root / "skills" / "proof-pack" / "agents" / "openai.yaml"
    agent_manifest.write_text(
        agent_manifest.read_text(encoding="utf-8")
        + '  icon_small: "../../../outside.svg"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(repository_validator, "_official_validator", lambda *_args: None)

    with pytest.raises(ValueError, match="asset.*inside|asset.*escape"):
        repository_validator.validate_repository(root)


def test_release_validator_rejects_broken_example_contract(tmp_path):
    root = copy_validator_fixture(tmp_path)
    example = root / "examples" / "proof-pack.md"
    example.write_text(
        example.read_text(encoding="utf-8").replace("## QUALITY CHECK", "## REMOVED CHECK"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="example contract"):
        validate_repository(root)


def test_release_validator_rejects_public_skill_symbolic_links(tmp_path):
    root = copy_validator_fixture(tmp_path)
    external = tmp_path.parent / "outside-release.txt"
    external.write_text("outside", encoding="utf-8")
    link = root / "skills" / "proof-pack" / "references" / "outside.txt"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows host")

    with pytest.raises(ValueError, match="forbidden release path"):
        validate_repository(root)


def test_release_validator_rejects_runtime_residue_under_public_skills(tmp_path):
    root = copy_validator_fixture(tmp_path)
    residue = root / "skills" / "proof-pack" / "scripts" / "__pycache__"
    residue.mkdir()
    (residue / "client.cpython-311.pyc").write_bytes(b"bytecode")

    with pytest.raises(ValueError, match="forbidden release path"):
        validate_repository(root)


@pytest.mark.parametrize("binary_prefix", [b"\x00", b"\xe9"])
def test_release_validator_scans_secret_tokens_inside_binary_files(tmp_path, binary_prefix):
    root = copy_validator_fixture(tmp_path)
    token = b"sk-" + b"abcdefghijklmnopqrstuvwxyz"
    (root / "artifact.bin").write_bytes(binary_prefix + token)

    with pytest.raises(ValueError, match="secret-like material"):
        validate_repository(root)


@pytest.mark.parametrize(
    "assignment",
    [
        b"CLAWTIP_SM4_KEY=" + b"YWJjZGVmZ2hpamtsbW5vcA==",
        b"API_KEY=" + b"abcdefghijklmnopqrstuvwxyz123456",
    ],
)
def test_release_validator_rejects_unquoted_secret_assignments(tmp_path, assignment):
    root = copy_validator_fixture(tmp_path)
    (root / "runtime-config.txt").write_bytes(assignment + b"\n")

    with pytest.raises(ValueError, match="secret-like material"):
        validate_repository(root)


def test_release_validator_rejects_private_gateway_prompt_contracts(tmp_path):
    root = copy_validator_fixture(tmp_path)
    prompt = root / "gateway" / "prompts" / "proof-pack.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("private generation contract", encoding="utf-8")

    with pytest.raises(ValueError, match="private prompt contract"):
        validate_repository(root)


def test_release_validator_rejects_packaging_private_gateway_prompts(tmp_path):
    root = copy_validator_fixture(tmp_path)
    (root / "pyproject.toml").write_text(
        '[tool.setuptools.package-data]\ngateway = ["prompts/*.md"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="private prompt contract"):
        validate_repository(root)
