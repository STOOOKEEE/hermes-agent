#!/usr/bin/env python3
"""Installe Agent Reach, twitter-cli et XActions à des versions auditées.

Sans ``--apply``, le script affiche uniquement son plan. Aucun cookie n’est lu,
écrit ou affiché par cet installateur.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class InstallationError(RuntimeError):
    """Le déploiement social ne peut pas continuer en sécurité."""


def _load_versions(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "integrations" / "versions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise InstallationError(f"Manifest de versions invalide : {path}")
    return data


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    subprocess.run(command, check=True, env=env, cwd=cwd)


def _ensure_clean_xactions_checkout(
    target: Path,
    repository: str,
    commit: str,
) -> None:
    if target.exists():
        if not (target / ".git").is_dir():
            raise InstallationError(
                f"{target} existe mais n’est pas un checkout Git géré par ce script"
            )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        if status.strip():
            raise InstallationError(
                f"Checkout XActions modifié localement, mise à jour refusée : {target}"
            )
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=target,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        normalized_remote = remote.rstrip("/").removesuffix(".git").lower()
        normalized_repository = repository.rstrip("/").removesuffix(".git").lower()
        if normalized_remote != normalized_repository:
            raise InstallationError(f"Remote XActions inattendu : {remote}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--filter=blob:none", "--no-checkout", repository, str(target)])

    _run(
        ["git", "fetch", "--depth", "1", "origin", commit],
        env=os.environ.copy(),
        cwd=target,
    )
    _run(
        ["git", "checkout", "--detach", commit],
        env=os.environ.copy(),
        cwd=target,
    )
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if actual != commit:
        raise InstallationError(f"Révision XActions incorrecte : {actual}")


def _safe_link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        if target.resolve() == source.resolve():
            return
        raise InstallationError(f"Lien existant non géré, remplacement refusé : {target}")
    if target.exists():
        raise InstallationError(f"Fichier existant, remplacement refusé : {target}")
    temporary = target.with_name(f".{target.name}.social.tmp")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(source)
    os.replace(temporary, target)


def _install_managed_file(source: Path, target: Path, mode: int = 0o755) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.social.tmp")
    shutil.copy2(source, temporary)
    temporary.chmod(mode)
    os.replace(temporary, target)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="effectuer l’installation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        versions = _load_versions(repo_root)
    except (OSError, yaml.YAMLError, InstallationError) as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    install_root = Path("~/.local/share/hermes-social").expanduser().resolve()
    bin_dir = Path("~/.local/bin").expanduser().resolve()
    venv = install_root / "agent-reach-venv"
    xactions = install_root / "xactions"

    print("Plan social :")
    print(
        f"- Agent Reach {versions['agent_reach']['version']} "
        f"({versions['agent_reach']['commit'][:12]}) dans {venv}"
    )
    print(f"- twitter-cli {versions['twitter_cli']['version']} derrière une façade lecture seule")
    print(f"- XActions ({versions['xactions']['commit'][:12]}) dans {xactions}, sans npm install")
    print("- aucun cookie manipulé par cet installateur")
    if not args.apply:
        print("Aucun fichier modifié. Relancer avec --apply après vérification.")
        return 0

    missing = [name for name in ("git", "node") if not shutil.which(name)]
    if missing:
        print("Dépendance système absente : " + ", ".join(missing), file=sys.stderr)
        return 2

    install_root.mkdir(parents=True, exist_ok=True)
    install_root.chmod(0o700)
    try:
        if not (venv / "bin" / "python").is_file():
            _run([sys.executable, "-m", "venv", str(venv)])

        pip = venv / "bin" / "python"
        agent = versions["agent_reach"]
        twitter = versions["twitter_cli"]
        _run(
            [
                str(pip),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                f"git+{agent['repository']}@{agent['commit']}",
                f"{twitter['package']}=={twitter['version']}",
            ]
        )

        xa = versions["xactions"]
        _ensure_clean_xactions_checkout(
            xactions,
            str(xa["repository"]),
            str(xa["commit"]),
        )

        readonly_source = repo_root / "integrations" / "twitter_readonly.py"
        readonly = install_root / "bin" / "twitter-readonly"
        _install_managed_file(readonly_source, readonly)
        _safe_link(venv / "bin" / "agent-reach", bin_dir / "agent-reach")
        _safe_link(readonly, bin_dir / "twitter")

        env = os.environ.copy()
        env["AGENT_REACH_LANG"] = "en"
        env["PATH"] = f"{bin_dir}:{venv / 'bin'}:{env.get('PATH', '')}"
        _run(
            [
                str(venv / "bin" / "agent-reach"),
                "install",
                "--env=server",
                "--safe",
                "--channels=twitter",
            ],
            env=env,
        )
    except (OSError, KeyError, subprocess.CalledProcessError, InstallationError) as exc:
        print(f"Échec de l’installation : {exc}", file=sys.stderr)
        return 1

    print("Installation sociale terminée. Les cookies X restent à configurer séparément.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
