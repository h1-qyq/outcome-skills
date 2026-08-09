"""Install one or all public One Cent Outcomes Skills into a supported host."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


SKILL_IDS = ("outcome-offer", "proof-pack", "reply-to-close")
_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        ".venv",
        ".worktrees",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "outputs",
        "work",
    }
)
_IGNORED_FILE_NAMES = frozenset(
    {".coverage", ".DS_Store", "Thumbs.db", "credentials", "credentials.json", "secrets.json"}
)
_IGNORED_FILE_SUFFIXES = frozenset(
    {
        ".db",
        ".key",
        ".p12",
        ".pem",
        ".pfx",
        ".pyc",
        ".pyd",
        ".pyo",
        ".sqlite",
        ".sqlite3",
    }
)


class InstallError(RuntimeError):
    """Raised when an installation cannot be completed safely."""


def destination_root(
    target: str,
    scope: str,
    *,
    project_root: Path,
    home_root: Path,
) -> Path:
    """Return the deterministic Skill directory for a host and scope."""
    destinations = {
        ("codex", "project"): Path(".agents/skills"),
        ("codex", "user"): Path(".agents/skills"),
        ("joycode", "project"): Path(".joycode/skills"),
        ("joycode", "user"): Path(".joycode/skills"),
        ("openclaw", "project"): Path("skills"),
        ("openclaw", "user"): Path(".openclaw/skills"),
    }
    try:
        suffix = destinations[(target, scope)]
    except KeyError as error:
        raise InstallError(
            f"unsupported target/scope combination: {target!r}/{scope!r}"
        ) from error
    base = project_root if scope == "project" else home_root
    return Path(base) / suffix


def _source_skills(
    repository_root: Path,
    skill_ids: tuple[str, ...] | None = None,
) -> tuple[Path, ...]:
    source_root = repository_root / "skills"
    actual = {
        path.parent.name for path in source_root.glob("*/SKILL.md") if path.is_file()
    }
    expected = set(SKILL_IDS)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        extra = ", ".join(sorted(actual - expected)) or "none"
        raise InstallError(
            f"source must contain exactly the public Skills "
            f"(missing: {missing}; unexpected: {extra})"
        )

    selected = SKILL_IDS if skill_ids is None else tuple(skill_ids)
    if not selected or any(skill_id not in SKILL_IDS for skill_id in selected):
        raise InstallError(f"skill selection must use only: {', '.join(SKILL_IDS)}")
    if len(set(selected)) != len(selected):
        raise InstallError("skill selection cannot contain duplicates")
    sources = tuple(source_root / skill_id for skill_id in selected)
    required = (
        Path("SKILL.md"),
        Path("scripts/client.py"),
        Path("references/quality-rubric.md"),
    )
    for source in sources:
        absent = [str(path) for path in required if not (source / path).is_file()]
        if absent:
            raise InstallError(f"incomplete source Skill {source.name}: {', '.join(absent)}")
        _reject_source_links(source)
    return sources


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _reject_source_links(source: Path) -> None:
    for directory, directory_names, file_names in os.walk(source, followlinks=False):
        for name in (*directory_names, *file_names):
            candidate = Path(directory) / name
            if _is_link_or_reparse(candidate):
                raise InstallError(
                    f"source Skill contains a symbolic link or reparse point: "
                    f"{candidate.relative_to(source)}"
                )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return os.path.abspath(left) == os.path.abspath(right)


def _entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _remove_entry(path: Path) -> None:
    if not _entry_exists(path):
        return
    if path.is_dir() and not _is_link_or_reparse(path):
        shutil.rmtree(path)
    else:
        path.unlink()


def _ignored_skill_entries(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(name)
        if (
            name in _IGNORED_DIRECTORY_NAMES
            or name.endswith(".egg-info")
            or name in _IGNORED_FILE_NAMES
            or name == ".env"
            or name.startswith(".env.")
            or path.suffix.lower() in _IGNORED_FILE_SUFFIXES
        ):
            ignored.add(name)
    return ignored


def install_skills(
    repository_root: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    skill_ids: tuple[str, ...] | None = None,
) -> tuple[Path, ...]:
    """Copy selected public Skills, staging complete directories before replacement."""
    repository_root = Path(repository_root)
    destination = Path(destination)
    sources = _source_skills(repository_root, skill_ids)

    if _same_path(destination, repository_root / "skills"):
        return sources

    targets = tuple(destination / source.name for source in sources)
    if _entry_exists(destination) and not destination.is_dir():
        raise InstallError(f"destination already exists and is not a directory: {destination}")
    collisions = [target for target in targets if _entry_exists(target)]
    if collisions and not force:
        names = ", ".join(target.name for target in collisions)
        raise InstallError(f"destination already exists for: {names}; use --force to replace")
    if dry_run:
        return targets

    destination_parent_existed = destination.parent.exists()
    destination_existed = destination.exists()
    stage_root: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage_root = Path(
            tempfile.mkdtemp(prefix=".one-cent-outcomes-", dir=destination.parent)
        )
        staged_root = stage_root / "staged"
        staged_root.mkdir()
        for source in sources:
            shutil.copytree(
                source,
                staged_root / source.name,
                symlinks=False,
                ignore=_ignored_skill_entries,
            )
    except OSError as error:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)
        if not destination_parent_existed:
            try:
                destination.parent.rmdir()
            except OSError:
                pass
        raise InstallError(f"copy failed: {error}") from error

    backup_root = stage_root / "backups"
    installed: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    preserve_stage = False
    try:
        destination.mkdir(parents=True, exist_ok=True)
        backup_root.mkdir()
        for target in targets:
            backup = backup_root / target.name
            if _entry_exists(target):
                os.replace(target, backup)
                backups.append((target, backup))
            os.replace(stage_root / "staged" / target.name, target)
            installed.append(target)
    except OSError as error:
        rollback_errors: list[str] = []
        for target in reversed(installed):
            try:
                _remove_entry(target)
            except OSError as rollback_error:
                rollback_errors.append(f"remove {target}: {rollback_error}")
        for target, backup in reversed(backups):
            if _entry_exists(backup):
                try:
                    if _entry_exists(target):
                        raise OSError("replacement target could not be cleared")
                    os.replace(backup, target)
                except OSError as rollback_error:
                    rollback_errors.append(f"restore {target}: {rollback_error}")
        if not destination_existed:
            try:
                destination.rmdir()
            except OSError as rollback_error:
                if _entry_exists(destination):
                    rollback_errors.append(
                        f"remove installation directory {destination}: {rollback_error}"
                    )
        if rollback_errors:
            preserve_stage = True
            detail = "; ".join(rollback_errors)
            raise InstallError(
                f"replacement failed: {error}; rollback incomplete: {detail}; "
                f"recovery backup preserved at {backup_root}"
            ) from error
        raise InstallError(f"replacement failed: {error}") from error
    finally:
        if not preserve_stage:
            shutil.rmtree(stage_root, ignore_errors=True)

    return targets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install one or all public One Cent Outcomes Skills."
    )
    parser.add_argument("--target", choices=("codex", "joycode", "openclaw"), required=True)
    parser.add_argument("--scope", choices=("project", "user"), required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--home-root", type=Path, default=Path.home())
    parser.add_argument(
        "--skill",
        dest="skill_ids",
        action="append",
        choices=SKILL_IDS,
        help="Install only this Skill; repeat the option to install a selected set (default: all).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    destination = destination_root(
        arguments.target,
        arguments.scope,
        project_root=arguments.project_root,
        home_root=arguments.home_root,
    )
    try:
        installed = install_skills(
            repository_root,
            destination,
            dry_run=arguments.dry_run,
            force=arguments.force,
            skill_ids=tuple(arguments.skill_ids) if arguments.skill_ids else None,
        )
    except InstallError as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1

    verb = "Would install" if arguments.dry_run else "Installed"
    for path in installed:
        print(f"{verb}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
