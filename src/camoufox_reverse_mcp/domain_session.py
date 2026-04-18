"""
Domain-keyed session archive with run layering.

v0.8.0: all analysis artifacts for a given eTLD+1 domain are persisted to
~/.camoufox-reverse/sessions/<domain>/. Each analysis session starts a new
"run" (identified by run_id); events are tagged with run_id while assertions
and request samples persist at the domain level across runs.
"""

from __future__ import annotations

import json
import os
import random
import re
import string
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import tldextract
    _extractor = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)
except ImportError:
    _extractor = None


DEFAULT_ROOT = Path.home() / ".camoufox-reverse" / "sessions"
SCHEMA_VERSION = "2.0"


def _iso_now() -> str:
    ms = int(time.time() * 1000) % 1000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{ms:03d}Z"


def normalize_domain(url_or_host: str) -> str:
    """Extract eTLD+1 from a URL or hostname."""
    if not url_or_host:
        raise ValueError("empty url_or_host")
    s = url_or_host.strip()
    if "://" not in s:
        s = "http://" + s

    try:
        host = urlparse(s).hostname or ""
    except Exception:
        host = ""

    if _extractor is not None:
        try:
            ext = _extractor(s)
            reg = ext.registered_domain
            if reg:
                return reg.lower()
        except Exception:
            pass

    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise ValueError(f"cannot extract domain from: {url_or_host!r}")
    return host.lower()


def new_run_id() -> str:
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"run_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}_{rand}"


@dataclass
class RunInfo:
    run_id: str
    started_at: str
    target_url: str
    note: str = ""
    ended_at: Optional[str] = None
    status: str = "active"
    counters: dict = field(default_factory=lambda: {
        "tool_calls": 0, "network_events": 0,
        "instrumentation": 0, "console": 0
    })


@dataclass
class DomainInfo:
    domain: str
    path: Path
    created_at: str
    status: str = "active"
    runs: list[RunInfo] = field(default_factory=list)
    active_run_id: Optional[str] = None
    active_assertions: list = field(default_factory=list)
    removed_assertions: list = field(default_factory=list)
    related_cases: list = field(default_factory=list)
    notes: str = ""
    domain_counters: dict = field(default_factory=lambda: {
        "tool_calls_total": 0, "network_events_total": 0,
        "instrumentation_total": 0, "samples_total": 0,
        "active_assertions": 0, "removed_assertions": 0
    })
    recording: dict = field(default_factory=lambda: {
        "tool_calls": True, "network_events": True,
        "instrumentation": True, "console": False
    })

    def active_run(self) -> Optional[RunInfo]:
        if not self.active_run_id:
            return None
        for r in self.runs:
            if r.run_id == self.active_run_id:
                return r
        return None


class DomainSessionStore:
    """Manages domain-keyed session archives."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.active: Optional[DomainInfo] = None
        self._locks: dict[str, threading.Lock] = {}
        self._file_handles: dict[str, dict] = {}

    # ---------- lifecycle ----------

    def start_run(self, domain: str, target_url: str, note: str = "",
                  recording: Optional[dict] = None,
                  related_cases: Optional[list[str]] = None) -> tuple[DomainInfo, RunInfo]:
        if self.active is not None and self.active.active_run_id:
            raise RuntimeError(
                f"another run is active on '{self.active.domain}'; stop_run() first")

        dom_path = self.root / domain
        new_domain = not dom_path.exists()
        if new_domain:
            dom_path.mkdir(parents=True, exist_ok=True)
            (dom_path / "samples").mkdir(exist_ok=True)
            (dom_path / "exports").mkdir(exist_ok=True)
            info = DomainInfo(
                domain=domain, path=dom_path, created_at=_iso_now(),
                recording=recording or DomainInfo(domain="", path=Path(), created_at="").recording,
                related_cases=list(related_cases or []),
            )
        else:
            info = self._load_manifest(dom_path)
            if recording is not None:
                info.recording = recording
            if related_cases:
                for c in related_cases:
                    if c not in info.related_cases:
                        info.related_cases.append(c)

        run = RunInfo(run_id=new_run_id(), started_at=_iso_now(),
                      target_url=target_url, note=note, status="active")
        info.runs.append(run)
        info.active_run_id = run.run_id
        info.status = "active"

        self.active = info
        self._locks[domain] = threading.Lock()
        self._file_handles[domain] = {}
        self.flush_manifest(info)
        self._update_index()
        return info, run

    def stop_run(self, status: str = "closed") -> Optional[tuple[DomainInfo, RunInfo]]:
        if self.active is None:
            return None
        info = self.active
        run = info.active_run()
        if run is None:
            return None
        run.ended_at = _iso_now()
        run.status = status

        for h in list(self._file_handles.get(info.domain, {}).values()):
            try:
                h.close()
            except Exception:
                pass
        self._file_handles[info.domain] = {}
        info.active_run_id = None
        self.flush_manifest(info)
        self._update_index()
        self.active = None
        return info, run

    def attach_domain(self, domain: str) -> DomainInfo:
        if self.active is not None and self.active.active_run_id:
            raise RuntimeError("another run is active")
        dom_path = self.root / domain
        if not dom_path.exists():
            raise FileNotFoundError(f"no session archive for domain {domain}")
        info = self._load_manifest(dom_path)
        info.active_run_id = None
        self.active = info
        self._locks.setdefault(domain, threading.Lock())
        self._file_handles.setdefault(domain, {})
        return info

    def detach_domain(self) -> Optional[DomainInfo]:
        if self.active is None:
            return None
        info = self.active
        self.active = None
        return info

    def list_domains(self) -> list[dict]:
        out = []
        for p in self.root.iterdir():
            if not p.is_dir() or p.name.startswith("."):
                continue
            mf = p / "manifest.json"
            if not mf.exists():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                out.append({
                    "domain": data.get("domain"), "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"), "status": data.get("status"),
                    "run_count": len(data.get("runs", [])),
                    "active_assertions_count": len(data.get("active_assertions", [])),
                    "domain_counters": data.get("domain_counters", {}),
                    "related_cases": data.get("related_cases", []),
                    "path": str(p),
                })
            except Exception as e:
                out.append({"domain": p.name, "error": str(e)})
        return out

    # ---------- event writing ----------

    def _jsonl_path(self, info: DomainInfo, event_type: str) -> Path:
        return info.path / f"{event_type}.jsonl"

    def _open_jsonl(self, info: DomainInfo, event_type: str):
        handles = self._file_handles.setdefault(info.domain, {})
        h = handles.get(event_type)
        if h is None or h.closed:
            h = open(self._jsonl_path(info, event_type), "a", encoding="utf-8")
            handles[event_type] = h
        return h

    def record(self, event_type: str, payload: dict) -> bool:
        info = self.active
        if info is None or info.active_run_id is None:
            return False
        if not info.recording.get(event_type, True):
            return False
        run = info.active_run()
        if run is None:
            return False

        lock = self._locks.setdefault(info.domain, threading.Lock())
        with lock:
            h = self._open_jsonl(info, event_type)
            run.counters[event_type] = run.counters.get(event_type, 0) + 1
            info.domain_counters[f"{event_type}_total"] = (
                info.domain_counters.get(f"{event_type}_total", 0) + 1)
            rec = {"ts": _iso_now(), "seq": run.counters[event_type],
                   "run": run.run_id, **payload}
            h.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            h.flush()
        return True

    def record_assertion_event(self, payload: dict) -> bool:
        info = self.active
        if info is None:
            return False
        lock = self._locks.setdefault(info.domain, threading.Lock())
        with lock:
            h = self._open_jsonl(info, "assertions")
            rec = {"ts": _iso_now(), "by_run": info.active_run_id, **payload}
            h.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            h.flush()
        return True

    def record_sample(self, kind: str, data: dict) -> str:
        info = self.active
        if info is None:
            raise RuntimeError("no active domain")
        samples_dir = info.path / "samples"
        samples_dir.mkdir(exist_ok=True)
        n = info.domain_counters.get("samples_total", 0) + 1
        info.domain_counters["samples_total"] = n
        fname = f"{kind}_{n:04d}.json"
        (samples_dir / fname).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return f"samples/{fname}"

    # ---------- manifest ----------

    def flush_manifest(self, info: Optional[DomainInfo] = None) -> None:
        info = info or self.active
        if info is None:
            return
        data = {
            "schema_version": SCHEMA_VERSION, "domain": info.domain,
            "created_at": info.created_at, "updated_at": _iso_now(),
            "status": info.status, "related_cases": info.related_cases,
            "notes": info.notes,
            "runs": [{"run_id": r.run_id, "started_at": r.started_at,
                       "ended_at": r.ended_at, "status": r.status,
                       "target_url": r.target_url, "note": r.note,
                       "counters": r.counters} for r in info.runs],
            "active_run_id": info.active_run_id,
            "domain_counters": {**info.domain_counters,
                                "active_assertions": len(info.active_assertions),
                                "removed_assertions": len(info.removed_assertions)},
            "active_assertions": info.active_assertions,
            "removed_assertions": info.removed_assertions,
            "recording": info.recording, "mcp_version": "0.8.0",
        }
        mf = info.path / "manifest.json"
        tmp = mf.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(mf)

    def _load_manifest(self, dom_path: Path) -> DomainInfo:
        data = json.loads((dom_path / "manifest.json").read_text(encoding="utf-8"))
        info = DomainInfo(
            domain=data["domain"], path=dom_path,
            created_at=data.get("created_at", _iso_now()),
            status=data.get("status", "active"),
            active_assertions=data.get("active_assertions", []),
            removed_assertions=data.get("removed_assertions", []),
            related_cases=data.get("related_cases", []),
            notes=data.get("notes", ""),
            recording=data.get("recording", DomainInfo(domain="", path=Path(), created_at="").recording),
            domain_counters=data.get("domain_counters", DomainInfo(domain="", path=Path(), created_at="").domain_counters),
        )
        for rd in data.get("runs", []):
            info.runs.append(RunInfo(
                run_id=rd["run_id"], started_at=rd["started_at"],
                target_url=rd.get("target_url", ""), note=rd.get("note", ""),
                ended_at=rd.get("ended_at"), status=rd.get("status", "closed"),
                counters=rd.get("counters", {})))
        info.active_run_id = data.get("active_run_id")
        return info

    def _update_index(self) -> None:
        idx = {"schema_version": SCHEMA_VERSION, "updated_at": _iso_now(),
               "domains": self.list_domains()}
        p = self.root / "index.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # ---------- snapshot ----------

    def snapshot(self, sections: Optional[list[str]] = None,
                 run_id: Optional[str] = "current",
                 max_events_per_section: int = 50) -> dict:
        info = self.active
        if info is None:
            return {"error": "no active domain"}
        if run_id is None:
            run_id = "current"
        sections = sections or ["manifest", "tool_calls", "network_events",
                                "instrumentation", "assertions"]
        out: dict = {}
        if "manifest" in sections:
            mf = info.path / "manifest.json"
            if mf.exists():
                out["manifest"] = json.loads(mf.read_text(encoding="utf-8"))
        for et in ("tool_calls", "network_events", "instrumentation", "console"):
            if et in sections:
                events = self._tail_jsonl_filtered(info, et, run_id, max_events_per_section)
                out[et] = {"count_returned": len(events), "run_filter": run_id, "events": events}
        if "assertions" in sections:
            events = self._tail_jsonl_filtered(info, "assertions", "all", max_events_per_section)
            out["assertions"] = {"count_returned": len(events),
                                 "active_count": len(info.active_assertions),
                                 "active_ids": list(info.active_assertions),
                                 "recent_events": events}
        return out

    def _tail_jsonl_filtered(self, info: DomainInfo, event_type: str,
                              run_filter: str, n: int) -> list[dict]:
        path = self._jsonl_path(info, event_type)
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            out: list[dict] = []
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if run_filter == "all":
                    out.append(rec)
                elif run_filter == "current":
                    if rec.get("run") == info.active_run_id:
                        out.append(rec)
                else:
                    if rec.get("run") == run_filter:
                        out.append(rec)
                if len(out) >= n:
                    break
            return list(reversed(out))
        except Exception:
            return []

    # ---------- export / import ----------

    def export_domain(self, output_path: Optional[Path] = None) -> Path:
        info = self.active
        if info is None:
            raise RuntimeError("no active domain to export")
        self.flush_manifest(info)
        exports = info.path / "exports"
        exports.mkdir(exist_ok=True)
        if output_path is None:
            fname = f"{info.domain}-{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.zip"
            output_path = exports / fname
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in info.path.rglob("*"):
                if fp.is_file() and "exports" not in fp.parts:
                    zf.write(fp, fp.relative_to(info.path))
        return output_path

    def import_domain(self, archive_path: Path,
                      merge_strategy: str = "replace") -> DomainInfo:
        archive_path = Path(archive_path)
        if not archive_path.exists():
            raise FileNotFoundError(archive_path)
        with zipfile.ZipFile(archive_path, "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise ValueError("archive has no manifest.json at root")
            manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
            domain = manifest_data.get("domain")
            if not domain:
                raise ValueError("archive manifest has no 'domain'")
            dest = self.root / domain
            if merge_strategy == "replace":
                if dest.exists():
                    import shutil
                    shutil.rmtree(dest)
                dest.mkdir(parents=True)
                zf.extractall(dest)
            elif merge_strategy == "merge":
                if not dest.exists():
                    dest.mkdir(parents=True)
                    zf.extractall(dest)
                else:
                    for name in zf.namelist():
                        if name.endswith(".jsonl"):
                            local = dest / name
                            new_content = zf.read(name).decode("utf-8")
                            if local.exists():
                                with open(local, "a", encoding="utf-8") as f:
                                    f.write(new_content if new_content.endswith("\n")
                                            else new_content + "\n")
                            else:
                                local.write_text(new_content, encoding="utf-8")
                        elif name.startswith("samples/"):
                            local = dest / name
                            if not local.exists():
                                local.parent.mkdir(parents=True, exist_ok=True)
                                local.write_bytes(zf.read(name))
                    self._merge_manifest(dest, manifest_data)
        return self._load_manifest(dest)

    def _merge_manifest(self, dest: Path, incoming: dict) -> None:
        mf = dest / "manifest.json"
        if not mf.exists():
            mf.write_text(json.dumps(incoming, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        existing = json.loads(mf.read_text(encoding="utf-8"))
        existing_runs = {r["run_id"] for r in existing.get("runs", [])}
        for r in incoming.get("runs", []):
            if r["run_id"] not in existing_runs:
                existing.setdefault("runs", []).append(r)
        existing_active = set(existing.get("active_assertions", []))
        for aid in incoming.get("active_assertions", []):
            if aid not in existing_active:
                existing.setdefault("active_assertions", []).append(aid)
        existing.setdefault("related_cases", [])
        for c in incoming.get("related_cases", []):
            if c not in existing["related_cases"]:
                existing["related_cases"].append(c)
        existing["updated_at"] = _iso_now()
        mf.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- module-level singleton ----------

_store: Optional[DomainSessionStore] = None

def get_store() -> DomainSessionStore:
    global _store
    if _store is None:
        _store = DomainSessionStore()
    return _store
