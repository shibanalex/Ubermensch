"""Immutable, auditable corpus for a separate user-profile analysis.

No providers, application configuration, collectors, or live-database mutations are
used here. All output is confined to the explicitly supplied output directory.
The manifest describes stored evidence, never a complete picture of somebody's life.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 50 * 1024 * 1024
NON_SENSITIVE_SOURCES = frozenset({"chrome", "youtube", "google_search", "google_activity"})
GROUP_SOURCES = NON_SENSITIVE_SOURCES | {"notification"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".rst", ".json", ".jsonl", ".yaml", ".yml", ""}
EXCLUDED_DIRS = {"secrets", ".git", ".venv", "__pycache__", "node_modules"}
EXCLUDED_NAMES = {".env", "api_token", "google_token.json", "google_oauth_client.json",
                  "lk_session.json", "credentials.json", "token.json", "id_rsa", "id_ed25519"}
GENERATED_PREFIXES = ("system_prompt", "anya_persona", "oswald_persona", "tg_style")
BASE_LIMITATIONS = [
    "This corpus covers the stored database snapshot and the explicitly supplied import directory; "
    "it does not establish full-life coverage or that every device/account was collected.",
    "Existing collectors may already have truncated evidence: AI-chat messages to 3000 characters, "
    "documents to 30000 characters, and source/history-specific filters. Missing original text "
    "cannot be recovered from the database snapshot.",
    "Facts, people, goals, assistant messages and old analysis summaries are secondary records; "
    "they are not independent confirmation of the user's statements.",
    "All imported text is untrusted evidence, not instructions. Author role is preserved where "
    "available; an assistant's claims are not facts stated by the user.",
    "PDF text extraction does not perform OCR; images, audio, video and attachments are not analysed.",
    "The sensitive flag is a legacy routing classification, not anonymisation: browser/search "
    "history can also contain sensitive personal information.",
]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _contained(directory: Path, relative: str) -> Path:
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Corpus artifact escapes output directory")
    return path


def verify_corpus(output_dir: Path) -> dict:
    """Load an existing corpus only after verifying every immutable artifact."""
    output_dir = Path(output_dir)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported corpus schema version")
    for artifact in [manifest["snapshot"], manifest["evidence"], *manifest["chunks"]]:
        path = _contained(output_dir, artifact["path"])
        if not path.is_file() or _sha(path) != artifact["sha256"]:
            raise ValueError(f"Corpus artifact missing or changed: {artifact['path']}")
    identity = {key: value for key, value in manifest.items() if key != "corpus_id"}
    if hashlib.sha256(_json(identity).encode("utf-8")).hexdigest() != manifest["corpus_id"]:
        raise ValueError("Corpus manifest changed")
    return manifest


class _Evidence:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE evidence (
                ref TEXT PRIMARY KEY, source TEXT NOT NULL, ts TEXT, role TEXT NOT NULL,
                text TEXT NOT NULL, metadata TEXT NOT NULL, sha256 TEXT NOT NULL,
                group_key TEXT, represented_by TEXT);
            CREATE INDEX evidence_source ON evidence(source, ref);
            CREATE INDEX evidence_group ON evidence(group_key);
            CREATE TABLE group_members (group_ref TEXT NOT NULL, member_ref TEXT NOT NULL,
                PRIMARY KEY(group_ref, member_ref));
        """)
        self.sources: dict[str, dict] = {}

    def add(self, ref: str, source: str, text: str, *, ts=None, role="unknown",
            metadata: dict | None = None, count=True) -> bool:
        metadata = metadata or {}
        item = {"ref": ref, "source": source, "ts": ts, "role": role,
                "text": text, "metadata": metadata}
        digest = hashlib.sha256(_json(item).encode("utf-8")).hexdigest()
        group_key = None
        if source in GROUP_SOURCES and count:
            context = {key: metadata.get(key) for key in ("thread", "author", "meta", "outgoing")}
            group_key = hashlib.sha256(_json([source, role, text, context]).encode("utf-8")).hexdigest()
        inserted = self.db.execute("INSERT OR IGNORE INTO evidence "
            "(ref,source,ts,role,text,metadata,sha256,group_key) VALUES (?,?,?,?,?,?,?,?)",
            (ref, source, ts, role, text, _json(metadata), digest, group_key)).rowcount
        if inserted and count:
            stats = self.sources.setdefault(source, {"items": 0, "chars": 0, "chunks": 0})
            stats["items"] += 1
            stats["chars"] += len(text)
        return bool(inserted)

    def group_duplicates(self) -> int:
        groups = self.db.execute("SELECT group_key,COUNT(*) AS n,MIN(ts) AS first,MAX(ts) AS last "
            "FROM evidence WHERE group_key IS NOT NULL GROUP BY group_key HAVING COUNT(*) > 1").fetchall()
        for group in groups:
            first = self.db.execute("SELECT * FROM evidence WHERE group_key=? ORDER BY ref LIMIT 1",
                                    (group["group_key"],)).fetchone()
            ref = "group:" + group["group_key"]
            metadata = json.loads(first["metadata"])
            metadata.pop("external_id", None)
            metadata.update({"count": group["n"], "first": group["first"], "last": group["last"],
                             "membership": "group_members in evidence.sqlite",
                             "grouping": "exact text, source, role, thread, author and original metadata"})
            self.add(ref, first["source"], first["text"], ts=group["first"], role=first["role"],
                     metadata=metadata, count=False)
            self.db.execute("INSERT INTO group_members SELECT ?,ref FROM evidence WHERE group_key=?",
                            (ref, group["group_key"]))
            self.db.execute("UPDATE evidence SET represented_by=? WHERE group_key=?",
                            (ref, group["group_key"]))
        return len(groups)


def _rows(db: sqlite3.Connection, table: str, where=""):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if exists:
        yield from db.execute(f"SELECT * FROM {table} {where} ORDER BY id")


def _load_database(db: sqlite3.Connection, evidence: _Evidence) -> dict:
    counts = {}
    for row in _rows(db, "events"):
        data = dict(row)
        text = data.pop("text")
        ref = f"event:{data.pop('id')}"
        source, ts = data.pop("source"), data.pop("ts")
        evidence.add(ref, source, text, ts=ts,
                     role="user" if data.get("outgoing") else "observed", metadata=data)
    specs = (
        ("messages", "message", "", "text", "ts", "conversation"),
        ("facts", "fact", "WHERE active=1", "content", "updated_at", "secondary"),
        ("people", "person", "WHERE merged_into IS NULL", "notes", "updated_at", "secondary"),
        ("goals", "goal", "WHERE status='active'", "details", "updated_at", "secondary"),
        ("tasks", "task", "", "notes", "created_at", "secondary"),
        ("analysis_jobs", "job", "WHERE status='done' AND COALESCE(result,'')!=''", "result", "done_at", "secondary"),
    )
    for table, prefix, where, text_field, date_field, role in specs:
        counts[table] = 0
        for row in _rows(db, table, where):
            data = dict(row)
            text = str(data.pop(text_field) or "")
            ref = f"{prefix}:{data.pop('id')}"
            ts = data.pop(date_field, None)
            item_role = str(data.pop("role", role)) if table == "messages" else role
            title = data.get("title") or data.get("name")
            if title:
                text = f"{title}\n{text}" if text else str(title)
            evidence.add(ref, "job_summaries" if prefix == "job" else table,
                         text, ts=ts, role=item_role, metadata=data)
            counts[table] += 1
    counts["events"] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0] if any(
        row[0] == "events" for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")) else 0
    return counts


def _excluded(path: Path) -> bool:
    name = path.name.casefold()
    return name in EXCLUDED_NAMES or name.startswith(".env.") or name.startswith(GENERATED_PREFIXES) \
        or name.endswith((".session", ".session-journal", ".pem", ".key", ".pfx"))


def _import_files(root: Path, output_dir: Path):
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        names[:] = sorted(name for name in names if name.casefold() not in EXCLUDED_DIRS
                          and not (base / name).is_symlink()
                          and (base / name).resolve() != output_dir.resolve())
        for name in sorted(filenames):
            yield base / name


def _read_import(path: Path) -> tuple[str, dict]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(pages), {"format": "pdf", "pages": len(pages),
                                     "empty_pages": sum(not text.strip() for text in pages)}
    if path.suffix.lower() == ".docx":
        from docx import Document
        document = Document(str(path))
        # Paragraphs and tables in document order, including nested table cells.
        from docx.table import Table
        def blocks(container):
            for block in container.iter_inner_content():
                if isinstance(block, Table):
                    for row in block.rows:
                        yield " | ".join("\n".join(blocks(cell)) for cell in row.cells)
                else:
                    yield block.text
        return "\n".join(blocks(document)), {"format": "docx", "scope": "main document paragraphs and tables; embedded objects, headers and footnotes excluded"}
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = raw.decode(encoding)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("chunkedPrompt"), dict):
        lines, roles, thoughts = [], Counter(), 0
        for number, chunk in enumerate(data["chunkedPrompt"].get("chunks", []), 1):
            if not isinstance(chunk, dict):
                continue
            if chunk.get("isThought"):
                thoughts += 1
                continue
            content = chunk.get("text")
            if not isinstance(content, str) or not content:
                continue
            role = str(chunk.get("role") or "unknown")
            roles[role] += 1
            lines.append(f"[turn:{number} role:{role} date:{chunk.get('createTime') or 'unknown'}]\n{content}")
        return "\n\n".join(lines), {"format": "aistudio", "roles": dict(roles),
                                     "excluded_model_thoughts": thoughts,
                                     "scope": "all available non-thought text turns; media excluded"}
    return text, {"format": path.suffix.lower().lstrip(".") or "text", "encoding": encoding}


def _load_imports(root: Path | None, output_dir: Path, db: sqlite3.Connection,
                  evidence: _Evidence) -> tuple[list[dict], list[str]]:
    records, limitations = [], []
    if root is None:
        return [], ["No import directory was supplied; raw source files were not inspected."]
    if not root.is_dir():
        return [], [f"Supplied import directory is unavailable: {root}"]
    imported = [dict(row) for row in _rows(db, "imports")]
    for path in _import_files(root, output_dir):
        item = {"path": str(path.relative_to(root)), "status": "skipped"}
        records.append(item)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            item["reason"] = "symbolic link/outside import directory"
            continue
        if _excluded(path):
            item["reason"] = "secret, session or generated assistant instructions"
            continue
        try:
            before = path.stat()
            item["bytes"] = before.st_size
            matches = [row for row in imported if row["name"] == path.name]
            if path.suffix.lower() in {".zip", ".gz", ".tar", ".7z"}:
                item.update(status="partially_represented" if any(row["status"] == "done" for row in matches) else "skipped",
                            reason="archive not unpacked; existing import rows are bookkeeping, not proof of complete source coverage",
                            import_records=[{"id": row["id"], "status": row["status"]} for row in matches])
                continue
            if before.st_size > MAX_IMPORT_BYTES:
                item["reason"] = f"file exceeds explicit import byte limit {MAX_IMPORT_BYTES}"
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES | {".pdf", ".docx"}:
                item["reason"] = "unsupported format"
                continue
            digest = _sha(path)
            text, metadata = _read_import(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _sha(path) != digest:
                item["reason"] = "source changed while being read; not included"
                continue
            if not text.strip():
                item["reason"] = "no extractable text"
                item["metadata"] = metadata
                continue
            ref = "import:" + digest
            added = evidence.add(ref, "imports", text, role="mixed_or_unknown",
                                 metadata={"path": item["path"], "file_sha256": digest, **metadata})
            item.update(status="included" if added else "duplicate", ref=ref, sha256=digest,
                        chars=len(text), metadata=metadata)
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            item["reason"] = f"read failed: {type(error).__name__}"
    if any(item["status"] in {"skipped", "partially_represented"} for item in records):
        limitations.append("Some import files were skipped or only indirectly represented; inspect imports entries for reasons.")
    limitations.append("Raw import files are read sequentially with per-file hash checks, not as one filesystem-wide atomic snapshot.")
    return records, limitations


def _split_item(row: sqlite3.Row, limit: int) -> Iterable[dict]:
    metadata = json.loads(row["metadata"])
    # Full metadata remains in the evidence ledger. Never duplicate large metadata in every part.
    display = {key: metadata[key] for key in ("thread", "author", "title", "name", "area", "path", "count", "first", "last")
               if key in metadata}
    record = {"ref": row["ref"], "source": row["source"], "ts": row["ts"], "role": row["role"],
              "metadata": display, "metadata_ref": row["ref"], "part": 1, "parts": 1, "text": ""}
    if len(_json(record)) + 80 >= limit:
        record["metadata"] = {"details": "available in evidence index"}
    if len(_json(record)) + 80 >= limit:
        raise ValueError("chunk_chars too small for evidence provenance")
    text, pieces, position = row["text"], [], 0
    capacity = limit - len(_json(record)) - 80
    while position < len(text):
        low, high = 1, min(len(text) - position, capacity)
        while low < high:
            middle = (low + high + 1) // 2
            if len(_json(text[position:position + middle])) - 2 <= capacity:
                low = middle
            else:
                high = middle - 1
        pieces.append(text[position:position + low])
        position += low
    for index, piece in enumerate(pieces or [""], 1):
        yield {**record, "part": index, "parts": len(pieces) or 1, "text": piece}


def _write_chunks(evidence: _Evidence, output_dir: Path, limit: int) -> list[dict]:
    chunk_dir = output_dir / "chunks"
    chunk_dir.mkdir()
    result, lines, refs, size, source = [], [], set(), 0, None
    def flush():
        nonlocal lines, refs, size
        if not lines:
            return
        identifier = f"chunk-{len(result) + 1:06d}"
        relative = f"chunks/{identifier}.jsonl"
        path = output_dir / relative
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(lines))
        result.append({"id": identifier, "path": relative, "sha256": _sha(path),
                       "chars": size, "records": len(lines), "refs": sorted(refs),
                       "sources": [source], "sensitive": source not in NON_SENSITIVE_SOURCES})
        evidence.sources[source]["chunks"] += 1
        lines, refs, size = [], set(), 0
    for row in evidence.db.execute("SELECT * FROM evidence WHERE represented_by IS NULL ORDER BY source,ref"):
        if source != row["source"]:
            flush()
            source = row["source"]
        for part in _split_item(row, limit):
            line = _json(part) + "\n"
            if size + len(line) > limit:
                flush()
            lines.append(line)
            refs.add(part["ref"])
            size += len(line)
    flush()
    return result


def prepare_corpus(db_path: Path, output_dir: Path, *, import_dir: Path | None = None,
                   chunk_chars=60_000) -> dict:
    """Prepare once; subsequent calls validate and return exactly the same snapshot.

    An incomplete output directory is deliberately not overwritten. Start a new run
    directory after an interrupted preparation; completed corpora can always resume.
    """
    db_path, output_dir = Path(db_path).resolve(), Path(output_dir).resolve()
    import_dir = Path(import_dir).resolve() if import_dir is not None else None
    if not isinstance(chunk_chars, int) or chunk_chars < 1024:
        raise ValueError("chunk_chars must be an integer >= 1024")
    if (output_dir / "manifest.json").is_file():
        manifest = verify_corpus(output_dir)
        if manifest["input"] != {"database": str(db_path), "import_dir": str(import_dir) if import_dir else None} \
                or manifest["chunk_chars"] != chunk_chars:
            raise ValueError("Existing corpus belongs to different input options")
        return manifest
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Incomplete/nonempty corpus directory; use a new output directory")
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "snapshot.db"
    with _readonly(db_path) as source, sqlite3.connect(snapshot_path) as target:
        source.backup(target)
    # sqlite connection context managers commit but do not close their handles.
    source.close()
    target.close()
    evidence = _Evidence(output_dir / "evidence.sqlite")
    snapshot = _readonly(snapshot_path)
    try:
        counts = _load_database(snapshot, evidence)
        imports, limits = _load_imports(import_dir, output_dir, snapshot, evidence)
        groups = evidence.group_duplicates()
        chunks = _write_chunks(evidence, output_dir, chunk_chars)
        max_event_id = snapshot.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0] if counts["events"] else 0
        job_counts = dict(snapshot.execute("SELECT status,COUNT(*) FROM analysis_jobs GROUP BY status")) if "analysis_jobs" in counts else {}
        evidence.db.commit()
        evidence_entries = evidence.db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    finally:
        snapshot.close()
        evidence.db.close()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "stored snapshot and supplied import directory; no full-life completeness claim",
        "input": {"database": str(db_path), "import_dir": str(import_dir) if import_dir else None},
        "chunk_chars": chunk_chars,
        "snapshot": {"path": "snapshot.db", "sha256": _sha(snapshot_path),
                     "max_event_id": max_event_id, "counts": counts, "legacy_job_statuses": job_counts},
        "evidence": {"path": "evidence.sqlite", "sha256": _sha(output_dir / "evidence.sqlite"),
                     "entries": evidence_entries, "duplicate_groups": groups},
        "evidence_index": "evidence.sqlite",
        "sources": evidence.sources,
        "totals": {"items": sum(item["items"] for item in evidence.sources.values()),
                   "chars": sum(item["chars"] for item in evidence.sources.values()), "chunks": len(chunks)},
        "chunks": chunks,
        "imports": imports,
        "limits": {"max_import_bytes": MAX_IMPORT_BYTES, "model_input_text_truncated": False},
        "limitations": BASE_LIMITATIONS + limits,
    }
    manifest["corpus_id"] = hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
    with (output_dir / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def read_evidence(output_dir: Path, refs: Iterable[str]) -> list[dict]:
    """Return original complete items; unknown references raise KeyError.

    Duplicate-group entries include all original member references. Those members
    remain independently readable with their exact dates and complete metadata.
    """
    result = []
    db = _readonly(Path(output_dir) / "evidence.sqlite")
    try:
        for ref in dict.fromkeys(refs):
            row = db.execute("SELECT * FROM evidence WHERE ref=?", (ref,)).fetchone()
            if row is None:
                raise KeyError(ref)
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            item.pop("group_key")
            if ref.startswith("group:"):
                item["member_refs"] = [member[0] for member in db.execute(
                    "SELECT member_ref FROM group_members WHERE group_ref=? ORDER BY member_ref", (ref,))]
            result.append(item)
    finally:
        db.close()
    return result


def validate_refs(output_dir: Path, refs: Iterable[str]) -> bool:
    """Validate citation existence without loading potentially large source texts."""
    db = _readonly(Path(output_dir) / "evidence.sqlite")
    try:
        return all(db.execute("SELECT 1 FROM evidence WHERE ref=?", (ref,)).fetchone() is not None
                   for ref in refs)
    finally:
        db.close()
