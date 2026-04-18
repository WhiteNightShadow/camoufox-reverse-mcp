"""Domain-keyed session lifecycle tools."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..server import mcp, browser_manager
from ..domain_session import get_store, normalize_domain


@mcp.tool()
async def start_reverse_session(
    domain: Optional[str] = None,
    note: str = "",
    related_cases: Optional[list[str]] = None,
    record_tool_calls: bool = True,
    record_network: bool = True,
    record_instrumentation: bool = True,
    record_console: bool = False,
) -> dict:
    """Start a new analysis run in the domain-keyed session archive.

    Sessions are organized by eTLD+1 domain. Each call starts a new "run"
    inside that domain. Events are tagged with run_id; assertions and
    samples persist at the domain level across runs.

    Storage: ~/.camoufox-reverse/sessions/<domain>/

    Args:
        domain: eTLD+1 domain. If omitted, derived from current page URL.
        note: Free-form note about this run.
        related_cases: Case filenames that apply to this domain.
        record_*: Toggle what gets archived.
    """
    try:
        store = get_store()
        if not domain:
            try:
                page = await browser_manager.get_active_page()
                url = page.url
                if not url or url == "about:blank":
                    return {"error": "no active page or page is blank; "
                                     "call navigate() first or pass domain explicitly"}
                domain = normalize_domain(url)
            except Exception as e:
                return {"error": f"cannot auto-derive domain: {e}"}

        target_url = ""
        try:
            page = await browser_manager.get_active_page()
            target_url = page.url
        except Exception:
            pass

        recording = {"tool_calls": record_tool_calls, "network_events": record_network,
                     "instrumentation": record_instrumentation, "console": record_console}
        dom_path = store.root / domain
        is_new = not dom_path.exists()

        info, run = store.start_run(domain=domain, target_url=target_url,
                                     note=note, recording=recording,
                                     related_cases=related_cases)
        return {
            "status": "run_started", "domain": domain, "run_id": run.run_id,
            "path": str(info.path), "is_new_domain": is_new,
            "previous_run_count": len(info.runs) - 1,
            "existing_assertions": len(info.active_assertions),
            "related_cases": info.related_cases,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def stop_reverse_session(status: str = "closed") -> dict:
    """End the active run. Domain archive stays."""
    try:
        result = get_store().stop_run(status=status)
        if result is None:
            return {"status": "no_active_run"}
        info, run = result
        return {"status": "run_stopped", "domain": info.domain,
                "run_id": run.run_id, "run_status": run.status,
                "counters": run.counters, "domain_counters": info.domain_counters}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_session_snapshot(
    sections: Optional[list[str]] = None,
    run_id: str = "current",
    max_events_per_section: int = 50,
) -> dict:
    """Return a compact snapshot of the active domain session."""
    try:
        return get_store().snapshot(sections, run_id, max_events_per_section)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_sessions() -> dict:
    """List all domain session archives on disk."""
    try:
        return {"domains": get_store().list_domains()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def attach_domain_readonly(domain: str) -> dict:
    """Attach an existing domain archive as active WITHOUT starting a new run."""
    try:
        info = get_store().attach_domain(domain)
        return {"status": "attached_readonly", "domain": info.domain,
                "run_count": len(info.runs),
                "active_assertions": len(info.active_assertions)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_session(output_path: Optional[str] = None) -> dict:
    """Zip the entire active domain archive."""
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        out = store.export_domain(Path(output_path) if output_path else None)
        return {"status": "exported", "domain": store.active.domain,
                "archive_path": str(out),
                "size_kb": round(out.stat().st_size / 1024, 1)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def import_session(archive_path: str, merge_strategy: str = "replace",
                         auto_attach: bool = True) -> dict:
    """Import a domain session zip."""
    try:
        store = get_store()
        info = store.import_domain(Path(archive_path), merge_strategy)
        result = {"status": "imported", "domain": info.domain,
                  "run_count": len(info.runs),
                  "active_assertions": len(info.active_assertions),
                  "merge_strategy": merge_strategy}
        if auto_attach:
            store.attach_domain(info.domain)
            result["attached_readonly"] = True
        return result
    except Exception as e:
        return {"error": str(e)}


import json
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


@mcp.tool()
async def verify_against_session(
    signer_code: str,
    sample_filter: Optional[dict] = None,
    max_samples: int = 10,
    compare_params: Optional[list[str]] = None,
    run_id: str = "all",
) -> dict:
    """Offline verify a signing function against historical request samples.

    Replays N captured requests from the session archive through a
    user-supplied signing function, then compares each computed parameter
    to the historically observed value, character by character.

    Args:
        signer_code: JS evaluating to a function: (sample) => ({param: value}).
        sample_filter: Filter which requests to use.
            Keys: url_contains, method, has_params (list of param names).
        max_samples: Max samples to test.
        compare_params: Which params to compare. If None, auto-detect
            signature-like params.
        run_id: "all" | "current" | specific run_id.
    """
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}

        samples = _collect_samples(store, sample_filter or {},
                                   max_samples, compare_params, run_id)
        if not samples:
            return {"error": "no matching samples in session",
                    "filter_used": sample_filter}

        page = await browser_manager.get_active_page()
        try:
            await page.evaluate(f"window.__mcp_signer_fn = {signer_code};")
        except Exception as e:
            return {"error": f"signer_code failed to evaluate: {e}"}

        details = []
        passed = failed = 0
        first_divergence = None

        for s in samples:
            try:
                computed = await page.evaluate(
                    "(sample) => window.__mcp_signer_fn(sample)", s["input"])
            except Exception as e:
                details.append({"sample_seq": s["seq"], "passed": False,
                                "error": f"signer threw: {e}"})
                failed += 1
                continue

            diffs = _compare_params(s["expected"], computed, compare_params)
            if not diffs:
                passed += 1
                details.append({"sample_seq": s["seq"], "passed": True})
            else:
                failed += 1
                details.append({"sample_seq": s["seq"], "passed": False, "diffs": diffs})
                if first_divergence is None:
                    first_divergence = {"sample_seq": s["seq"], "diffs": diffs,
                                        "input": s["input"]}

        return {
            "total_samples": len(samples), "passed": passed, "failed": failed,
            "pass_rate": round(passed / len(samples), 3) if samples else 0,
            "first_divergence": first_divergence, "run_filter": run_id,
            "details": details,
        }
    except Exception as e:
        return {"error": str(e)}


def _collect_samples(store, filt: dict, max_n: int,
                     target_params: Optional[list[str]],
                     run_filter: str) -> list[dict]:
    info = store.active
    path = info.path / "network_events.jsonl"
    if not path.exists():
        return []

    url_substr = filt.get("url_contains")
    method = filt.get("method")
    has_params = filt.get("has_params") or []

    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "request":
                continue
            if run_filter == "current" and rec.get("run") != info.active_run_id:
                continue
            elif run_filter not in ("all", "current") and rec.get("run") != run_filter:
                continue
            url = rec.get("url", "")
            if url_substr and url_substr not in url:
                continue
            if method and rec.get("method") != method:
                continue
            qs = parse_qs(urlparse(url).query)
            qs_flat = {k: v[0] if v else "" for k, v in qs.items()}
            if has_params and not all(p in qs_flat for p in has_params):
                continue

            expected = {}
            if target_params:
                for p in target_params:
                    if p in qs_flat:
                        expected[p] = qs_flat[p]
            else:
                sig_keywords = ("sign", "token", "msg", "bogus", "gnarly")
                for k, v in qs_flat.items():
                    if k[:1].isupper() or any(sk in k.lower() for sk in sig_keywords):
                        expected[k] = v
            if not expected:
                continue

            input_qs = {k: v for k, v in qs_flat.items() if k not in expected}
            parsed = urlparse(url)
            stripped_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                      parsed.params, urlencode(input_qs), parsed.fragment))
            samples.append({"seq": rec.get("seq"), "run": rec.get("run"),
                            "input": {"url": stripped_url, "full_url": url,
                                      "method": rec.get("method"), "params": input_qs},
                            "expected": expected})
            if len(samples) >= max_n:
                break
    return samples


def _compare_params(expected: dict, computed: dict,
                    focus: Optional[list[str]]) -> list[dict]:
    diffs = []
    keys = focus if focus else list(expected.keys())
    for k in keys:
        exp = expected.get(k)
        act = (computed or {}).get(k)
        if exp != act:
            if isinstance(exp, str) and isinstance(act, str):
                first_diff = -1
                for i in range(min(len(exp), len(act))):
                    if exp[i] != act[i]:
                        first_diff = i
                        break
                if first_diff == -1 and len(exp) != len(act):
                    first_diff = min(len(exp), len(act))
                diffs.append({"param": k, "expected": exp, "actual": act,
                              "first_diff_char": first_diff,
                              "expected_length": len(exp), "actual_length": len(act)})
            else:
                diffs.append({"param": k, "expected": exp, "actual": act})
    return diffs
