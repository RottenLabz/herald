"""Reviewed committed-source export. No runtime backup and no shell-sourced config."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tarfile
import tempfile
import zlib

MANIFEST = "approved-source-manifest.txt"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ENV_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
RUNTIME_DIRS = {"data", "logs", "backups", "runtime", ".git", ".venv", "venv", "env",
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"}
PRIVATE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".pyc", ".pyo",
                    ".tar", ".tar.gz", ".tgz", ".zip", "-wal", "-shm", "-journal", ".instance.lock")
DB_SIGNATURES = (b"SQLite format 3\x00", b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83",
                 b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"(?<![\w-])[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}(?![\w-])"),
    re.compile(r"(?<![\w-])mfa\.[A-Za-z0-9_-]{40,}"),
    re.compile(r"(?<![\w-])(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_-]{20,}"),
    re.compile(r"https?://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?(?:DISCORD_TOKEN|[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY))\s*=\s*(.*)$"
)
PLACEHOLDERS = {"", "0", "changeme", "replace-me", "your-token-here", "example", "placeholder"}


class ExportError(RuntimeError):
    """A closed export failure; messages must never include secret values."""


def git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportError("Git execution failed; export refused.") from exc
    return result.stdout


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and all(p not in (".", "..", "") for p in name.split("/")) \
        and "\\" not in name and not any(ord(c) < 32 for c in name)


def runtime_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    base = parts[-1].lower()
    return (any(p.lower() in RUNTIME_DIRS for p in parts)
            or (base.startswith(".env") and name != ".env.example")
            or base.endswith(".env") or base.endswith(PRIVATE_SUFFIXES)
            or base in {"id_rsa", "id_ed25519", "id_ecdsa"})


def read_env_file(path: Path) -> dict[str, str]:
    """Parse single-line dotenv syntax as data, without expansion or execution.

    Complex/multiline syntax is refused rather than guessing runtime paths.
    """
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENV_BYTES:
            raise ExportError("Unsafe or excessive runtime environment file; export refused.")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ExportError("Runtime environment could not be read; export refused.") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ExportError("Unsupported runtime environment syntax; export refused.")
        key, value = match.groups()
        if not value.startswith(("'", '"')):
            # dotenv preserves unquoted backslashes and treats # as a comment
            # only after whitespace; shell tokenization would corrupt paths.
            values[key] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            continue
        quote, result, index = value[0], [], 1
        while index < len(value):
            char = value[index]
            if char == quote:
                trailing = value[index + 1:].strip()
                if trailing and not trailing.startswith("#"):
                    raise ExportError("Unsupported runtime environment quoting; export refused.")
                values[key] = "".join(result)
                break
            if char == "\\" and index + 1 < len(value):
                nxt = value[index + 1]
                replacements = {"\\": "\\", quote: quote}
                if quote == '"':
                    replacements.update({"n": "\n", "r": "\r", "t": "\t"})
                if nxt in replacements:
                    result.append(replacements[nxt])
                    index += 2
                    continue
            result.append(char)
            index += 1
        else:
            raise ExportError("Unsupported runtime environment quoting; export refused.")
    return values


def runtime_paths(root: Path, env_file: Path | None) -> set[Path]:
    values: dict[str, str] = {}
    default = root / ".env"
    if default.exists() or default.is_symlink():
        values.update(read_env_file(default))
    configured_env = os.environ.get("HERALD_ENV_PATH")
    if configured_env:
        values.update(read_env_file(Path(configured_env)))
    if env_file is not None:
        values.update(read_env_file(env_file))
    values.update(os.environ)
    paths = {root / "data", root / "logs", root / "backups"}
    for key, value in values.items():
        if key.startswith("HERALD_") and key.endswith(("_PATH", "_DIR")) and value:
            if "$" in value or "`" in value:
                raise ExportError("Runtime path expansion is unsupported; export refused.")
            path = Path(value).expanduser()
            paths.add((path if path.is_absolute() else root / path).resolve())
    raw_plugins = values.get("HERALD_PRIVATE_PROVIDERS", "[]")
    try:
        plugins = json.loads(raw_plugins)
        if not isinstance(plugins, list):
            raise ValueError
        for plugin in plugins:
            if not isinstance(plugin, dict) or not isinstance(plugin.get("path"), str):
                raise ValueError
            path = Path(plugin["path"])
            paths.add((path if path.is_absolute() else root / path).resolve())
    except (ValueError, TypeError) as exc:
        raise ExportError("Private provider configuration cannot be assessed; export refused.") from exc
    return paths


def configured_runtime(name: str, root: Path, paths: set[Path]) -> bool:
    path = root / name
    return any(path == item or item in path.parents or str(path) in
               {str(item) + suffix for suffix in ("-wal", "-shm", "-journal", ".instance.lock")} for item in paths)


def scan_content(name: str, content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise ExportError("Source file exceeds the reviewed export limit.")
    if content.startswith(DB_SIGNATURES) or b"\x00" in content:
        raise ExportError("Binary or database material is not reviewed source.")
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ExportError("Source text decoding failed; export refused.") from exc
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ExportError("Secret-like content found; export refused (value withheld).")
    for line in text.splitlines():
        match = CREDENTIAL_ASSIGNMENT.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith(("env_str(", "os.getenv(", "os.environ", "re.compile(")):
            continue
        if value.startswith(("'", '"')):
            try:
                tokens = shlex.split(value, comments=True)
            except ValueError as exc:
                raise ExportError("Credential scan could not parse assignment.") from exc
            value = tokens[0] if tokens else ""
        else:
            value = value.split("#", 1)[0].strip()
        if value.lower() not in PLACEHOLDERS:
            raise ExportError("Credential assignment found; export refused (value withheld).")


def source_files(root: Path, env_file: Path | None = None) -> dict[str, bytes]:
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ExportError("Run export at the reviewed Git repository root.")
    if git(root, "status", "--porcelain=v1", "--untracked-files=no"):
        raise ExportError("Tracked working tree/index must be clean before source export.")
    paths = runtime_paths(root, env_file)
    entries: dict[str, tuple[str, str]] = {}
    for record in git(root, "ls-tree", "-r", "-z", "HEAD").split(b"\x00"):
        if not record:
            continue
        info, raw_name = record.split(b"\t", 1)
        mode, kind, oid = info.decode("ascii").split()
        name = raw_name.decode("utf-8")
        if not safe_name(name):
            raise ExportError("Unsafe tracked path; export refused.")
        entries[name] = (mode, oid)
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ExportError("Symlinks/submodules are forbidden in source export.")
    if MANIFEST not in entries:
        raise ExportError("Reviewed source manifest is missing from committed source.")
    manifest_data = git(root, "cat-file", "blob", entries[MANIFEST][1])
    scan_content(MANIFEST, manifest_data)
    approved = [line.strip() for line in manifest_data.decode().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if len(approved) != len(set(approved)) or not all(safe_name(x) for x in approved):
        raise ExportError("Reviewed source manifest is invalid.")
    if not {MANIFEST, ".env.example", "source_export.py"}.issubset(approved):
        raise ExportError("Required reviewed source files are absent from manifest.")
    if set(approved) - set(entries):
        raise ExportError("Manifest references absent committed source.")
    selected: dict[str, bytes] = {}
    for name, (_, oid) in entries.items():
        if runtime_name(name) or configured_runtime(name, root, paths):
            continue
        # Size check precedes reading blobs so accidental tracked large data is bounded.
        if int(git(root, "cat-file", "-s", oid)) > MAX_FILE_BYTES:
            raise ExportError("Tracked file exceeds reviewed export limit.")
        content = git(root, "cat-file", "blob", oid)
        if content.startswith(DB_SIGNATURES):
            continue
        if name not in approved:
            raise ExportError("Unreviewed tracked source exists; review the explicit manifest.")
        scan_content(name, content)
        selected[name] = content
    if ".env.example" not in selected:
        raise ExportError("Public .env.example must be retained.")
    if sum(map(len, selected.values())) > MAX_ARCHIVE_BYTES:
        raise ExportError("Source set exceeds reviewed export limit.")
    return selected


def verify_archive(path: Path, expected: dict[str, bytes]) -> None:
    seen: set[str] = set()
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ExportError("Archive exceeds verification limit.")
        compressed = path.read_bytes()
        decoder = zlib.decompressobj(wbits=31)
        # Bound decompression before tar parsing and reject extra gzip streams/data.
        raw_tar = decoder.decompress(compressed, MAX_ARCHIVE_BYTES + 1)
        if len(raw_tar) > MAX_ARCHIVE_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            raise ExportError("Archive framing/decompression limit failed.")
        logical_end = 0
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
            for member in archive:
                if not member.isfile() or not safe_name(member.name) or member.name in seen:
                    raise ExportError("Archive contains unsafe or duplicate members.")
                expected_mode = 0o755 if member.name.endswith(".sh") else 0o644
                if member.pax_headers or member.uid or member.gid or member.mtime or member.uname or member.gname or member.mode != expected_mode:
                    raise ExportError("Archive contains unreviewed metadata.")
                if member.name not in expected or member.size != len(expected[member.name]):
                    raise ExportError("Archive contents differ from reviewed source.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ExportError("Archive member is unreadable.")
                content = stream.read(MAX_FILE_BYTES + 1)
                scan_content(member.name, content)
                if content != expected[member.name]:
                    raise ExportError("Archive bytes differ from reviewed committed source.")
                seen.add(member.name)
                logical_end = member.offset_data + ((member.size + 511) // 512) * 512
        # Tar readers stop at end markers: forbid hidden payload after the last member.
        if any(raw_tar[logical_end:]):
            raise ExportError("Archive has unexpected trailing payload.")
        if seen != set(expected):
            raise ExportError("Archive is missing reviewed source.")
    except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
        raise ExportError("Archive verification failed; export refused.") from exc


def export_source(root: Path, output: Path, env_file: Path | None = None) -> str:
    expected = source_files(root, env_file)
    output = output.absolute()
    sidecar = Path(str(output) + ".sha256")
    if not output.parent.is_dir() or output.exists() or sidecar.exists():
        raise ExportError("Use a new archive filename in an existing output directory.")
    temporary: Path | None = None
    published = False
    sidecar_created = False
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".herald-export-", delete=False) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for name, content in sorted(expected.items()):
                        member = tarfile.TarInfo(name)
                        member.mode = 0o755 if name.endswith(".sh") else 0o644
                        member.size = len(content)
                        archive.addfile(member, io.BytesIO(content))
            raw.flush()
            os.fsync(raw.fileno())
        verify_archive(temporary, expected)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.link(temporary, output)  # Atomic no-clobber publication on the same filesystem.
        published = True
        fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        sidecar_created = True
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{digest}  {output.name}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return digest
    except Exception:
        if published:
            output.unlink(missing_ok=True)
        if sidecar_created:
            sidecar.unlink(missing_ok=True)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--env-file", type=Path, help="Additional runtime .env to parse as data")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="New archive filename; parent must already exist")
    action.add_argument("--verify", type=Path)
    action.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.output:
            digest = export_source(args.root, args.output, args.env_file)
            print(f"PASS: reviewed committed-source archive verified; SHA-256 {digest}")
        else:
            expected = source_files(args.root, args.env_file)
            if args.verify:
                verify_archive(args.verify, expected)
            print("PASS: reviewed committed-source check completed.")
        return 0
    except Exception:
        # Avoid exposing paths, token values, subprocess stderr, or parser input.
        print("FAIL: source export/check refused; inspect source, manifest and runtime configuration locally.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
