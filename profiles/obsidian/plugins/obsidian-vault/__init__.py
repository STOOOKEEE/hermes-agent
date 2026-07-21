"""Outils Hermes bornés à un vault Obsidian local et privé."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE_NAME = "obsidian"
SYNC_TOOL_NAME = "obsidian_git_sync"
MAX_FILE_BYTES = 1_000_000
MAX_RESULTS = 200
TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
    ".canvas",
    ".css",
    ".js",
}
BLOCKED_PARTS = {".git", ".hermes-backups"}


class VaultConfigurationError(RuntimeError):
    """Le vault ou son profil Hermes n'est pas configuré correctement."""


def _active_profile_is_obsidian() -> bool:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).name == PROFILE_NAME
    except ImportError:
        return os.getenv("HERMES_PROFILE", "").strip().lower() == PROFILE_NAME


def _profile_secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret

        return str(get_secret(name, "") or "")
    except ImportError:
        return os.getenv(name, "")


def _profile_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser().resolve()
    except ImportError:
        explicit = os.getenv("HERMES_PROFILE_HOME", "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        return Path.home() / ".hermes" / "profiles" / PROFILE_NAME


def _vault_root() -> Path:
    if not _active_profile_is_obsidian():
        raise VaultConfigurationError("Outils Obsidian indisponibles hors du profil obsidian")
    raw = _profile_secret("OBSIDIAN_VAULT_PATH").strip()
    if not raw:
        raise VaultConfigurationError(
            "OBSIDIAN_VAULT_PATH manque dans le .env du profil obsidian"
        )
    configured = Path(raw).expanduser()
    if not configured.is_absolute():
        raise VaultConfigurationError("OBSIDIAN_VAULT_PATH doit être un chemin absolu")
    root = configured.resolve()
    if not root.is_dir():
        raise VaultConfigurationError(f"Vault Obsidian introuvable : {root}")
    return root


def _relative_path(value: Any, *, allow_root: bool = False) -> Path:
    if not isinstance(value, str):
        raise ValueError("path doit être une chaîne")
    candidate = value.strip().replace("\\", "/")
    if not candidate:
        if allow_root:
            return Path(".")
        raise ValueError("path est obligatoire")
    path = Path(candidate)
    if path.is_absolute() or "\x00" in candidate:
        raise ValueError("path doit être relatif au vault")
    if any(part in BLOCKED_PARTS or part == ".." for part in path.parts):
        raise ValueError("path interdit dans le vault")
    return path


def _resolve(value: Any, *, allow_root: bool = False) -> tuple[Path, Path, Path]:
    root = _vault_root()
    relative = _relative_path(value, allow_root=allow_root)
    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("path sort du vault")
    return root, relative, resolved


def _ensure_text_path(path: Path) -> None:
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        allowed = ", ".join(sorted(TEXT_EXTENSIONS))
        raise ValueError(f"type de fichier non modifiable ; extensions autorisées : {allowed}")


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError("fichier introuvable")
    _ensure_text_path(path)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"fichier trop volumineux ({size} octets)")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("le fichier n'est pas un texte UTF-8") from exc


def _backup_existing(root: Path, relative: Path, target: Path) -> str | None:
    if not target.exists():
        return None
    if not target.is_file():
        raise ValueError("la cible existe mais n'est pas un fichier")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = _profile_home() / "backups" / "obsidian-vault" / stamp / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    return str(backup)


def _atomic_write(root: Path, relative: Path, target: Path, content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"contenu trop volumineux ({len(encoded)} octets)")
    _ensure_text_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_existing(root, relative, target)
    mode = (target.stat().st_mode & 0o777) if target.exists() else 0o600
    temporary = target.with_name(f".{target.name}.hermes.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "success": True,
        "path": relative.as_posix(),
        "bytes": len(encoded),
        "backup_created": backup is not None,
    }


def _walk_files(base: Path, *, recursive: bool) -> list[Path]:
    iterator = base.rglob("*") if recursive else base.glob("*")
    files: list[Path] = []
    for path in iterator:
        if len(files) >= MAX_RESULTS:
            break
        if not path.is_file() or any(part in BLOCKED_PARTS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _handle_list(args: dict[str, Any], **_: Any) -> str:
    try:
        prefix = args.get("path", "") if isinstance(args, dict) else ""
        recursive = bool(args.get("recursive", True)) if isinstance(args, dict) else True
        root, _, base = _resolve(prefix, allow_root=True)
        if not base.is_dir():
            raise ValueError("le dossier demandé n'existe pas")
        files = _walk_files(base, recursive=recursive)
        return _json_result(
            {
                "success": True,
                "files": [
                    {
                        "path": path.relative_to(root).as_posix(),
                        "bytes": path.stat().st_size,
                    }
                    for path in files
                ],
                "truncated": len(files) >= MAX_RESULTS,
            }
        )
    except (OSError, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _handle_read(args: dict[str, Any], **_: Any) -> str:
    try:
        _, relative, target = _resolve(args.get("path"))
        return _json_result(
            {"success": True, "path": relative.as_posix(), "content": _read_text(target)}
        )
    except (AttributeError, OSError, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _handle_write(args: dict[str, Any], **_: Any) -> str:
    try:
        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("content doit être une chaîne")
        root, relative, target = _resolve(args.get("path"))
        return _json_result(_atomic_write(root, relative, target, content))
    except (AttributeError, OSError, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _handle_append(args: dict[str, Any], **_: Any) -> str:
    try:
        addition = args.get("content")
        if not isinstance(addition, str) or not addition:
            raise ValueError("content est obligatoire")
        root, relative, target = _resolve(args.get("path"))
        current = _read_text(target) if target.exists() else ""
        separator = "" if not current or current.endswith("\n") else "\n"
        return _json_result(
            _atomic_write(root, relative, target, current + separator + addition)
        )
    except (AttributeError, OSError, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _handle_search(args: dict[str, Any], **_: Any) -> str:
    try:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query est obligatoire")
        root = _vault_root()
        needle = query.casefold()
        matches: list[dict[str, Any]] = []
        for path in _walk_files(root, recursive=True):
            if path.suffix.lower() != ".md":
                continue
            try:
                text = _read_text(path)
            except (OSError, ValueError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "line": number,
                            "excerpt": line[:300],
                        }
                    )
                    if len(matches) >= MAX_RESULTS:
                        break
            if len(matches) >= MAX_RESULTS:
                break
        return _json_result(
            {
                "success": True,
                "query": query,
                "matches": matches,
                "truncated": len(matches) >= MAX_RESULTS,
            }
        )
    except (OSError, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _git(root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _git_error(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stderr or completed.stdout or "commande Git échouée").strip()[:1000]


def _git_root() -> Path:
    root = _vault_root()
    probe = _git(root, "rev-parse", "--show-toplevel")
    if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != root:
        raise VaultConfigurationError("le vault n'est pas la racine d'un dépôt Git")
    remote = _git(root, "remote", "get-url", "origin")
    url = remote.stdout.strip()
    if remote.returncode != 0 or not (
        url.startswith("git@github.com:") or url.startswith("ssh://git@github.com/")
    ):
        raise VaultConfigurationError("origin doit être un remote SSH GitHub")
    return root


def _handle_git_status(args: dict[str, Any], **_: Any) -> str:
    del args
    try:
        root = _git_root()
        status = _git(root, "status", "--short", "--branch")
        if status.returncode != 0:
            raise VaultConfigurationError(_git_error(status))
        return _json_result({"success": True, "status": status.stdout.strip()})
    except (OSError, subprocess.TimeoutExpired, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


def _validate_commit_message(args: Any) -> str:
    if not isinstance(args, dict):
        raise ValueError("les arguments doivent être un objet")
    message = str(args.get("message") or "").strip()
    if not message or len(message) > 120 or "\n" in message or "\r" in message:
        raise ValueError("message doit contenir entre 1 et 120 caractères sur une ligne")
    return message


def _working_tree_fingerprint(root: Path) -> str:
    """Lie une approbation persistante à l'état exact du vault."""

    root = root.resolve()
    digest = hashlib.sha256()
    status = _git(root, "status", "--porcelain=v1", "-z")
    if status.returncode != 0:
        raise VaultConfigurationError(_git_error(status))
    digest.update(status.stdout.encode("utf-8"))

    changed_paths: set[str] = set()
    for command in (
        ("ls-files", "-m", "-o", "--exclude-standard", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
    ):
        completed = _git(root, *command)
        if completed.returncode != 0:
            raise VaultConfigurationError(_git_error(completed))
        changed_paths.update(item for item in completed.stdout.split("\x00") if item)

    for name in sorted(changed_paths):
        relative = _relative_path(name)
        path = (root / relative).resolve(strict=False)
        if not path.is_relative_to(root):
            raise VaultConfigurationError("un changement Git sort du vault")
        digest.update(relative.as_posix().encode("utf-8"))
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"<deleted-or-directory>")
    return digest.hexdigest()[:20]


def _sync_approval(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    if tool_name != SYNC_TOOL_NAME:
        return None
    try:
        message = _validate_commit_message(args)
        root = _git_root()
        fingerprint = _working_tree_fingerprint(root)
    except (OSError, subprocess.TimeoutExpired, ValueError, VaultConfigurationError) as exc:
        return {"action": "block", "message": f"Synchronisation Obsidian refusée : {exc}"}
    return {
        "action": "approve",
        "message": (
            "Confirmer le commit, le pull --rebase et le push du vault privé "
            f"avec le message exact : {message}"
        ),
        "rule_key": (
            f"{SYNC_TOOL_NAME}:"
            f"{hashlib.sha256((message + fingerprint).encode('utf-8')).hexdigest()[:20]}"
        ),
    }


def _handle_git_sync(args: dict[str, Any], **_: Any) -> str:
    try:
        message = _validate_commit_message(args)
        root = _git_root()
        branch_result = _git(root, "symbolic-ref", "--short", "HEAD")
        branch = branch_result.stdout.strip()
        if branch_result.returncode != 0 or not branch:
            raise VaultConfigurationError("le vault est en HEAD détachée")

        conflicts = _git(root, "diff", "--name-only", "--diff-filter=U")
        if conflicts.returncode != 0 or conflicts.stdout.strip():
            raise VaultConfigurationError("des conflits Git doivent être résolus manuellement")

        add = _git(root, "add", "-A")
        if add.returncode != 0:
            raise VaultConfigurationError(_git_error(add))
        staged = _git(root, "diff", "--cached", "--quiet")
        committed = staged.returncode == 1
        if staged.returncode not in {0, 1}:
            raise VaultConfigurationError(_git_error(staged))
        if committed:
            commit = _git(
                root,
                "-c",
                "user.name=Hermes",
                "-c",
                "user.email=hermes@localhost",
                "commit",
                "-m",
                message,
            )
            if commit.returncode != 0:
                raise VaultConfigurationError(_git_error(commit))

        pull = _git(root, "pull", "--rebase", "origin", branch, timeout=60)
        if pull.returncode != 0:
            _git(root, "rebase", "--abort")
            raise VaultConfigurationError(
                "pull --rebase échoué ; le commit local est conservé : " + _git_error(pull)
            )
        push = _git(root, "push", "origin", f"HEAD:{branch}", timeout=60)
        if push.returncode != 0:
            raise VaultConfigurationError(
                "push échoué ; les changements restent commités localement : " + _git_error(push)
            )
        head = _git(root, "rev-parse", "--short", "HEAD")
        return _json_result(
            {
                "success": True,
                "branch": branch,
                "commit": head.stdout.strip(),
                "created_commit": committed,
            }
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, VaultConfigurationError) as exc:
        return _json_result({"success": False, "error": str(exc)})


LIST_SCHEMA = {
    "name": "obsidian_list_files",
    "description": "Liste jusqu'à 200 fichiers du vault Obsidian, sans exposer .git.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Sous-dossier relatif, vide pour la racine."},
            "recursive": {
                "type": "boolean",
                "description": "Inclure les sous-dossiers (true par défaut).",
            },
        },
        "additionalProperties": False,
    },
}
SEARCH_SCHEMA = {
    "name": "obsidian_search_notes",
    "description": (
        "Recherche du texte, sans distinction de casse, dans les notes Markdown du vault."
    ),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
        "required": ["query"],
        "additionalProperties": False,
    },
}
READ_SCHEMA = {
    "name": "obsidian_read_file",
    "description": "Lit un fichier texte UTF-8 du vault Obsidian (maximum 1 Mo).",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1}},
        "required": ["path"],
        "additionalProperties": False,
    },
}
WRITE_SCHEMA = {
    "name": "obsidian_write_file",
    "description": (
        "Crée ou remplace atomiquement un fichier texte du vault, avec sauvegarde "
        "hors dépôt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string", "maxLength": MAX_FILE_BYTES},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
APPEND_SCHEMA = {
    "name": "obsidian_append_note",
    "description": (
        "Ajoute du texte à une note du vault, avec sauvegarde de la version précédente."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string", "minLength": 1, "maxLength": MAX_FILE_BYTES},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
}
STATUS_SCHEMA = {
    "name": "obsidian_git_status",
    "description": "Affiche l'état Git du vault privé sans le modifier.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}
SYNC_SCHEMA = {
    "name": SYNC_TOOL_NAME,
    "description": (
        "Après approbation, commit les changements du vault, pull --rebase puis push "
        "vers GitHub."
    ),
    "parameters": {
        "type": "object",
        "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 120}},
        "required": ["message"],
        "additionalProperties": False,
    },
}


def _check_available() -> bool:
    try:
        _vault_root()
    except (OSError, VaultConfigurationError):
        return False
    return True


def register(ctx) -> None:
    for schema, handler, emoji in (
        (LIST_SCHEMA, _handle_list, "📂"),
        (SEARCH_SCHEMA, _handle_search, "🔎"),
        (READ_SCHEMA, _handle_read, "📖"),
        (WRITE_SCHEMA, _handle_write, "✍️"),
        (APPEND_SCHEMA, _handle_append, "➕"),
        (STATUS_SCHEMA, _handle_git_status, "🌿"),
        (SYNC_SCHEMA, _handle_git_sync, "🔄"),
    ):
        ctx.register_tool(
            name=schema["name"],
            toolset="obsidian_vault",
            schema=schema,
            handler=handler,
            check_fn=_check_available,
            emoji=emoji,
        )
    ctx.register_hook("pre_tool_call", _sync_approval)
