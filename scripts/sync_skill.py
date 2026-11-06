"""Install/check the repository skill; back up an existing copy before replacing managed files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def files(root):
    result = {}
    for path in root.rglob("*"):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Skill resources must be regular files: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sync(source, target, backup=None, check=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("Source and installed skill must be separate")
    if not (source / "SKILL.md").is_file():
        raise ValueError("Repository skill is missing SKILL.md")
    wanted, installed = files(source), files(target)
    extra = sorted(set(installed) - set(wanted))
    changed = sorted(name for name, digest in wanted.items() if installed.get(name) != digest)
    result = {"passed": not extra and not changed, "source": str(source), "target": str(target),
              "files": len(wanted), "changed": changed, "extra": extra}
    if check or result["passed"]:
        return result
    if extra:
        raise ValueError(f"Installed skill contains unmanaged files; preserved without changes: {extra}")
    metadata = "agents/openai.yaml"
    if metadata in installed:
        before = (target / metadata).read_text(encoding="utf-8")
        after = (source / metadata).read_text(encoding="utf-8")
        if before != after:
            raise ValueError("Installed UI/policy metadata differs; preserve and reconcile it explicitly")
    if installed:
        if backup is None:
            raise ValueError("An existing installed skill requires a new backup directory")
        backup = Path(backup).resolve()
        if backup.exists() or any(backup == p or backup.is_relative_to(p) or p.is_relative_to(backup) for p in (source,target)):
            raise ValueError("Backup must be a new, separate directory")
        shutil.copytree(target, backup)
    target.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    verified = files(target)
    if verified != wanted:
        raise ValueError("Installed skill differs after copying")
    return dict(result, passed=True, updated=True, backup=str(backup) if backup else None,
                changed=[], manifest_sha256=hashlib.sha256(json.dumps(wanted,sort_keys=True).encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "skills/reconstruct-structured-interiors")
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        result = sync(args.source,args.target,args.backup,args.check)
    except (ValueError,OSError) as error:
        parser.exit(1, str(error)+"\n")
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
