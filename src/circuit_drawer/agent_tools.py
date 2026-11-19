"""Checked drawing candidates shared by local Agent and MCP callers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .compile import FORMATS
from .layout import Layout
from .profiles import PROFILES, get_profile


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kind(table: dict) -> str:
    if not isinstance(table, dict):
        raise ValueError("A drawing must be a JSON object.")
    kind = table.get("kind")
    if kind == "control" or (kind is None and "links" in table and "elements" not in table):
        return "control"
    if kind in (None, "circuit") and ("elements" in table or "modules" in table):
        return "circuit"
    raise ValueError("Expected a circuit coordinate table or a control diagram.")


@dataclass(frozen=True)
class Candidate:
    text: str
    kind: str
    profile: str
    base_dir: Path
    source_path: Path | None = None
    source_revision: str | None = None

    @property
    def table(self) -> dict:
        # Each caller receives a copy. Concurrent edits always branch from their snapshot.
        return json.loads(self.text)

    @property
    def revision(self) -> str:
        return _digest(self.text)


class AgentWorkspace:
    """In-memory candidates with checked exports inside one local workspace.

    Source files remain the editor's responsibility. A caller adopts a returned
    candidate table through the editor's existing version check and undo stack.
    """

    def __init__(self, workspace: str | Path, output_dir: str | Path = "agent-output"):
        self.root = Path(workspace).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("The workspace must be a directory.")
        self.output_dir = self._path(output_dir)
        self._candidates: dict[str, Candidate] = {}
        self._lock = threading.Lock()

    def _path(self, path: str | Path) -> Path:
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("The path must be inside the configured workspace.")
        return resolved

    def _get(self, candidate_id: str) -> Candidate:
        with self._lock:
            candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise ValueError("Unknown candidate. Read the document or create a candidate again.")
        return candidate

    def _store(self, table: dict, profile: str | None, base_dir: Path,
               source_path: Path | None = None, source_revision: str | None = None) -> dict:
        kind = _kind(table)
        default = table.get("profile") or ("ieee-double" if kind == "control" else "ieee-single")
        profile = get_profile(profile or default).name
        text = _json(table)
        candidate = Candidate(text, kind, profile, base_dir, source_path, source_revision)
        candidate_id = _digest(_json([text, profile, str(base_dir),
                                     str(source_path) if source_path else None, source_revision]))
        with self._lock:
            self._candidates.setdefault(candidate_id, candidate)
        return self.inspect_candidate(candidate_id, include_table=False)

    def capabilities(self) -> dict:
        """Report currently installed export tools and the candidate operations."""
        from .runtime import find_tool

        latex = find_tool("pdflatex") is not None
        poppler = find_tool("pdftocairo") is not None
        return {
            "document_kinds": ["circuit", "control"],
            "profiles": list(PROFILES),
            "formats": list(FORMATS),
            "available_formats": [fmt for fmt in FORMATS if fmt == "tex" or
                                  (latex and (fmt == "pdf" or poppler))],
            "workspace": str(self.root),
            "output_dir": str(self.output_dir),
            "operations": ["read_document", "create_candidate", "inspect_candidate",
                           "edit_candidate", "validate_candidate", "save_candidate", "render_candidate",
                           "simulate_candidate", "identify_modes", "analyse_loop"],
            "adoption": "Return the candidate table to the editor for version-checked adoption and undo.",
        }

    def read_document(self, path: str, profile: str | None = None) -> dict:
        """Read a saved JSON drawing and return its table plus an immutable candidate ID."""
        source = self._path(path)
        if source.suffix.lower() != ".json":
            raise ValueError("Choose a .json drawing file.")
        table = json.loads(source.read_text(encoding="utf-8-sig"))
        result = self._store(table, profile, source.parent, source, _digest(_json(table)))
        result["table"] = table
        return result

    def create_candidate(self, table: dict, profile: str | None = None,
                         base_candidate_id: str | None = None) -> dict:
        """Stage a complete table, optionally inheriting an existing candidate's source context."""
        if base_candidate_id is None:
            return self._store(table, profile, self.root)
        base = self._get(base_candidate_id)
        if _kind(table) != base.kind:
            raise ValueError("An edited candidate must keep its document kind.")
        inherited_profile = base.profile
        if table.get("profile") != base.table.get("profile"):
            inherited_profile = table.get("profile")
        return self._store(table, profile or inherited_profile, base.base_dir,
                           base.source_path, base.source_revision)

    def inspect_candidate(self, candidate_id: str, include_table: bool = True) -> dict:
        """Return a candidate's source revision, validation and optionally its full table."""
        candidate = self._get(candidate_id)
        result = {
            "candidate_id": candidate_id, "revision": candidate.revision,
            "kind": candidate.kind, "profile": candidate.profile,
            "base_dir": str(candidate.base_dir),
            "source": {"path": str(candidate.source_path), "revision": candidate.source_revision}
            if candidate.source_path else None,
            "validation": self.validate_candidate(candidate_id),
        }
        if include_table:
            result["table"] = candidate.table
        return result

    def edit_candidate(self, candidate_id: str, changes: list[dict[str, Any]]) -> dict:
        """Set fields on a copy. Each change has path (keys/zero-based indices) and value.

        Parent containers must exist. Replace a whole list to add/remove entries,
        or use create_candidate for a completely rewritten table.
        """
        candidate = self._get(candidate_id)
        table = candidate.table
        differences = []
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a nonempty list of {path, value} objects.")
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"path", "value"}:
                raise ValueError("Each change must contain exactly path and value.")
            path = change["path"]
            if not isinstance(path, list) or not path:
                raise ValueError("A change path must be a nonempty list of keys or indices.")
            parent = table
            for index, key in enumerate(path):
                is_object = isinstance(parent, dict) and isinstance(key, str)
                is_item = isinstance(parent, list) and type(key) is int and 0 <= key < len(parent)
                if not (is_object or is_item):
                    raise ValueError(f"Invalid change path {path!r} at {key!r}.")
                if index < len(path) - 1:
                    if is_object and key not in parent:
                        raise ValueError(f"Missing parent in change path {path!r}.")
                    parent = parent[key]
                else:
                    existed = is_item or key in parent
                    differences.append({"path": path, "existed": existed,
                                        "before": deepcopy(parent[key]) if existed else None,
                                        "after": deepcopy(change["value"])})
                    parent[key] = deepcopy(change["value"])
        result = self.create_candidate(table, base_candidate_id=candidate_id)
        result["changes"] = differences
        result["base_candidate_id"] = candidate_id
        return result

    def validate_candidate(self, candidate_id: str) -> dict:
        """Run the product checker on the exact candidate table and selected print profile."""
        candidate = self._get(candidate_id)
        prof = get_profile(candidate.profile)
        try:
            if candidate.kind == "control":
                from .control.check import check
                report = check(candidate.table, prof).to_dict()
                if report["ok"] and candidate.table.get("circuit"):
                    from .control.circuit import resolve
                    from .simulation_export import build_circuit
                    table, source, _ = resolve(candidate.table, candidate.base_dir)
                    build_circuit(Layout.from_dict(table), table.get("profile") or "ieee-double",
                                  base_dir=source.parent, control="continuous")
                return report
            from .validate import check
            return check(Layout.from_dict(candidate.table), prof).to_dict()
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            return {"ok": False, "issues": [{"code": "invalid-table", "level": "error",
                                            "message": str(exc)}]}

    def save_candidate(self, candidate_id: str, filename: str | None = None) -> dict:
        """Save a checked copy next to its source, preserving relative references and existing files."""
        candidate = self._get(candidate_id)
        report = self.validate_candidate(candidate_id)
        if not report["ok"]:
            raise ValueError("Fix the candidate's validation errors before saving a copy.")
        if filename is None:
            stem = candidate.source_path.stem if candidate.source_path else "drawing"
            filename = f"{stem}-agent-{candidate.revision[:12]}.json"
        if Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename:
            raise ValueError("filename must be a plain .json filename.")
        target = self._path(candidate.base_dir / filename)
        if target.suffix.lower() != ".json":
            raise ValueError("filename must end in .json.")
        payload = (json.dumps(candidate.table, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        # Windows rename refuses replacement, including on filesystems without hard links.
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".candidate-", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            temporary.write_bytes(payload)
            try:
                if os.name == "nt":
                    temporary.rename(target)
                else:
                    os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != payload:
                    raise ValueError("That filename already contains different content. Choose a new filename.")
        finally:
            temporary.unlink(missing_ok=True)
        return {"candidate_id": candidate_id, "revision": candidate.revision,
                "path": str(target), "sha256": hashlib.sha256(payload).hexdigest(), "validation": report}

    def render_candidate(self, candidate_id: str, formats: list[str] | None = None) -> dict:
        """Validate and export a candidate to a new directory, returning paths and SHA256 values."""
        candidate = self._get(candidate_id)
        formats = ["svg"] if formats is None else formats
        if not formats or any(fmt not in FORMATS for fmt in formats):
            raise ValueError(f"formats must be a nonempty list drawn from {FORMATS}.")
        # Resolve again in case a previously unused output directory became a symlink.
        output = self._path(self.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        destination = output / f"run-{uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=output) as temporary:
            stage = Path(temporary) / "result"
            if candidate.kind == "control":
                from .control.render import render
                result = render(candidate.table, candidate.profile, stage, "figure", tuple(formats), base_dir=candidate.base_dir)
            else:
                from .pipeline import render
                result = render(Layout.from_dict(candidate.table), candidate.profile, stage,
                                "figure", tuple(formats), base_dir=candidate.base_dir)
            files = {fmt: {"path": str(destination / file.name),
                           "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                     for fmt, file in result.files.items()}
            manifest = {"candidate_id": candidate_id, "revision": candidate.revision,
                        "profile": candidate.profile, "base_dir": str(candidate.base_dir),
                        "validation": result.report.to_dict(), "files": files}
            (stage / "manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")
            stage.rename(destination)
        return {**manifest, "manifest": str(destination / "manifest.json")}

    def simulate_candidate(self, candidate_id: str, window: list[float] | None = None,
                           signals: list[str] | None = None, power_quality: dict | None = None) -> dict:
        """Run ngspice and measure full-resolution samples in an explicit window."""
        from .agent_results import run_candidate
        return run_candidate(self, candidate_id, "simulate",
                             options={"window": window, "signals": signals, "power_quality": power_quality})

    def identify_modes(self, candidate_id: str) -> dict:
        """Use the existing instrumented solver to identify fixed-PWM switching modes."""
        from .agent_results import run_candidate
        return run_candidate(self, candidate_id, "modes")

    def analyse_loop(self, candidate_id: str) -> dict:
        """Calculate a control diagram's loop margins and export its Bode curves."""
        from .agent_results import run_candidate
        return run_candidate(self, candidate_id, "loop")
