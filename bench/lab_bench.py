#!/usr/bin/env python3
"""
Unbounded Lab benchmark harness.

Schema:  unbounded-lab-bench/v2  (W1.7)
Legacy:  unbounded-lab-bench/v1  (W1.6 — keep producing under --no-stream
                                   --repeats 1 for reproducibility)

Hits an OpenAI-compatible chat-completions endpoint or an Ollama-native
endpoint, sweeps concurrency levels, and reports:
  - aggregate decode tokens/s (cluster-side throughput)
  - per-request decode tokens/s (per-user UX throughput)
  - end-to-end latency p50 / p95 / p99
  - TTFT (time-to-first-token) p50 / p95 / p99      [streaming only]
  - TPOT / inter-token latency p50 / p95 in ms       [streaming only]
  - peak GPU power during the phase                  [if --prom-url]
  - per-phase truncation count + quality warning
  - knee_concurrency: lowest concurrency where throughput plateaus or
    per-request throughput collapses

Stdlib-only on purpose: the bench Job image is plain `python:3.12-slim`
and we don't want a pip install in the hot path.

Usage:

  # W1.7 sweep (streaming, 3 repeats, knee detection):
  python lab_bench.py \\
      --engine vllm-openai \\
      --url http://vllm.lab-vllm-qwen-moe:8000/v1 \\
      --model Qwen/Qwen3-30B-A3B-GPTQ-Int4 \\
      --concurrency 1,2,4,8,16,32,48,64 \\
      --repeats 3 --runs 12 --warmup 2 --stream \\
      --min-completion-tokens 32 \\
      --out /results/w1.7-vllm.json

  # W1.6 baseline (single sweep, non-streaming, v1-shape phases retained
  # alongside the v2 schema header):
  python lab_bench.py --engine vllm-openai --url ... --no-stream --repeats 1 ...
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import socket
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable


HARNESS_VERSION = "0.2.0"
SCHEMA = "unbounded-lab-bench/v2"


# A short repeated phrase so the prompt is *roughly* `--prompt-len` tokens.
# We don't tokenize per-engine here; the 1 token ≈ 4 chars rule suffices
# for a load harness.
_FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. "
)


def make_prompt(target_tokens: int) -> str:
    target_chars = max(64, int(target_tokens * 4))
    body = _FILLER * (target_chars // len(_FILLER) + 1)
    body = body[:target_chars]
    return (
        "You are a benchmark target. Reply with a long technical answer. "
        "Context (ignore content, this is filler to reach the target prompt length): "
        + body
        + "\n\nQuestion: explain Kubernetes StatefulSets in detail."
    )


# --------------------------------------------------------------------------
# RunResult
# --------------------------------------------------------------------------

@dataclasses.dataclass
class RunResult:
    ok: bool
    latency_s: float            # send -> last byte
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None
    # Streaming-only (None when --no-stream):
    ttft_s: float | None = None         # send -> first content chunk
    tpot_ms: float | None = None        # mean inter-token gap, ms
    # Validity:
    output_chars: int = 0
    output_valid_utf8: bool = True
    truncated: bool = False             # hit max_tokens floor


# --------------------------------------------------------------------------
# Engine callers
# --------------------------------------------------------------------------

def _http_post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, timeout: float = 5.0) -> dict | None:
    """GET a JSON URL, return dict or None on any failure (best-effort)."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"# GET {url} failed: {e!r}", file=sys.stderr)
        return None


def _open_post_stream(url: str, body: dict, headers: dict, timeout: float):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)


def call_openai(
    *, base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float, gen_len: int,
) -> RunResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    t0 = time.perf_counter()
    try:
        out = _http_post_json(
            base_url.rstrip("/") + "/chat/completions",
            body, auth_header, timeout,
        )
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=0, completion_tokens=0, error=repr(e))
    dt = time.perf_counter() - t0
    usage = out.get("usage") or {}
    completion = int(usage.get("completion_tokens", 0))
    content = ""
    try:
        content = out["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        pass
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=completion,
        output_chars=len(content),
        output_valid_utf8=_is_valid_utf8(content),
        truncated=(completion >= gen_len),
    )


def call_openai_stream(
    *, base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float, gen_len: int,
) -> RunResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.perf_counter()
    t_first: float | None = None
    chunk_count = 0
    content_parts: list[str] = []
    completion_tokens = 0
    prompt_tokens = 0
    try:
        resp = _open_post_stream(
            base_url.rstrip("/") + "/chat/completions",
            body, auth_header, timeout,
        )
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=0, completion_tokens=0, error=repr(e))
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # usage frame at end (vLLM emits with stream_options)
                if obj.get("usage"):
                    u = obj["usage"]
                    completion_tokens = int(u.get("completion_tokens", completion_tokens))
                    prompt_tokens = int(u.get("prompt_tokens", prompt_tokens))
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    if t_first is None:
                        t_first = time.perf_counter()
                    chunk_count += 1
                    content_parts.append(piece)
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens, error=repr(e))
    t_last = time.perf_counter()
    dt = t_last - t0
    content = "".join(content_parts)
    if completion_tokens == 0:
        # Fallback: no usage frame -> approximate by chunk count.
        completion_tokens = chunk_count
    ttft = (t_first - t0) if t_first is not None else None
    tpot_ms = None
    if t_first is not None and completion_tokens > 1:
        tpot_ms = ((t_last - t_first) / (completion_tokens - 1)) * 1000.0
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        ttft_s=ttft, tpot_ms=tpot_ms,
        output_chars=len(content),
        output_valid_utf8=_is_valid_utf8(content),
        truncated=(completion_tokens >= gen_len),
    )


def call_ollama(
    *, base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float, gen_len: int,
) -> RunResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.0},
    }
    t0 = time.perf_counter()
    try:
        out = _http_post_json(
            base_url.rstrip("/") + "/api/chat",
            body, auth_header, timeout,
        )
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=0, completion_tokens=0, error=repr(e))
    dt = time.perf_counter() - t0
    completion = int(out.get("eval_count", 0))
    content = ""
    try:
        content = out.get("message", {}).get("content", "") or ""
    except AttributeError:
        pass
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=int(out.get("prompt_eval_count", 0)),
        completion_tokens=completion,
        output_chars=len(content),
        output_valid_utf8=_is_valid_utf8(content),
        truncated=(completion >= gen_len),
    )


def call_ollama_stream(
    *, base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float, gen_len: int,
) -> RunResult:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {"num_predict": max_tokens, "temperature": 0.0},
    }
    t0 = time.perf_counter()
    t_first: float | None = None
    completion_tokens = 0
    prompt_tokens = 0
    content_parts: list[str] = []
    try:
        resp = _open_post_stream(
            base_url.rstrip("/") + "/api/chat",
            body, auth_header, timeout,
        )
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=0, completion_tokens=0, error=repr(e))
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    if t_first is None:
                        t_first = time.perf_counter()
                    content_parts.append(piece)
                if obj.get("done"):
                    completion_tokens = int(obj.get("eval_count", 0)) or completion_tokens
                    prompt_tokens = int(obj.get("prompt_eval_count", 0)) or prompt_tokens
                    break
    except Exception as e:  # noqa: BLE001
        return RunResult(ok=False, latency_s=time.perf_counter() - t0,
                         prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens, error=repr(e))
    t_last = time.perf_counter()
    dt = t_last - t0
    content = "".join(content_parts)
    if completion_tokens == 0:
        completion_tokens = len(content_parts)
    ttft = (t_first - t0) if t_first is not None else None
    tpot_ms = None
    if t_first is not None and completion_tokens > 1:
        tpot_ms = ((t_last - t_first) / (completion_tokens - 1)) * 1000.0
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        ttft_s=ttft, tpot_ms=tpot_ms,
        output_chars=len(content),
        output_valid_utf8=_is_valid_utf8(content),
        truncated=(completion_tokens >= gen_len),
    )


def _is_valid_utf8(s: str) -> bool:
    try:
        s.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


# --------------------------------------------------------------------------
# Prometheus
# --------------------------------------------------------------------------

def query_prom_peak(prom_url: str, query: str,
                    start: float, end: float) -> float | None:
    if not prom_url:
        return None
    step = max(1.0, (end - start) / 30.0)
    url = (prom_url.rstrip("/") + "/api/v1/query_range"
           f"?query={urllib.parse.quote(query)}"
           f"&start={start}&end={end}&step={step}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        print(f"# prom query failed: {url} -> {e!r}", file=sys.stderr)
        return None
    if data.get("status") != "success":
        print(f"# prom query non-success: {data!r}", file=sys.stderr)
        return None
    series = data.get("data", {}).get("result", [])
    peak = None
    for s in series:
        for _, v in s.get("values", []):
            try:
                f = float(v)
            except (ValueError, TypeError):
                continue
            if peak is None or f > peak:
                peak = f
    if peak is None:
        print(f"# prom query returned no samples: {query}", file=sys.stderr)
    return peak


def query_prom_instant(prom_url: str, query: str) -> list[dict] | None:
    if not prom_url:
        return None
    url = (prom_url.rstrip("/") + "/api/v1/query"
           f"?query={urllib.parse.quote(query)}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        print(f"# prom instant query failed: {url} -> {e!r}", file=sys.stderr)
        return None
    if data.get("status") != "success":
        return None
    return data.get("data", {}).get("result", [])


# --------------------------------------------------------------------------
# Metadata gathering
# --------------------------------------------------------------------------

def gather_meta(*, engine: str, url: str, prom_url: str) -> dict:
    """Best-effort run metadata. Failures log to stderr, never raise."""
    meta: dict[str, Any] = {
        "harness_git_sha": os.environ.get("LAB_BENCH_GIT_SHA") or None,
        "harness_version": HARNESS_VERSION,
        "schema": SCHEMA,
        "client_pod_name": os.environ.get("HOSTNAME") or socket.gethostname(),
        "client_node_name": os.environ.get("LAB_BENCH_NODE_NAME") or None,
        "engine_version": None,
        "engine_build": None,
        "kernel": None,
        "gpus": [],
    }
    # Engine version probe
    try:
        if engine == "vllm-openai":
            base = url.rstrip("/")
            # vLLM's OpenAI server exposes /version (not under /v1)
            root = base[:-3] if base.endswith("/v1") else base
            v = _http_get_json(root + "/version")
            if v:
                meta["engine_version"] = v.get("version") or v.get("vllm_version")
                meta["engine_build"] = v.get("git_revision") or v.get("commit")
        elif engine == "ollama":
            v = _http_get_json(url.rstrip("/") + "/api/version")
            if v:
                meta["engine_version"] = v.get("version")
    except Exception as e:  # noqa: BLE001
        print(f"# engine version probe failed: {e!r}", file=sys.stderr)

    # Kernel (best-effort; this is the bench-pod kernel, NOT the GPU-node
    # kernel. Documented as such.)
    try:
        with open("/proc/sys/kernel/osrelease") as f:
            meta["kernel"] = f.read().strip()
    except OSError:
        pass

    # GPU enumeration via DCGM-exporter labels (Prometheus)
    if prom_url:
        rows = query_prom_instant(prom_url, "DCGM_FI_DEV_GPU_TEMP")
        if rows:
            seen: set[tuple] = set()
            for r in rows:
                m = r.get("metric") or {}
                key = (m.get("Hostname"), m.get("UUID"), m.get("gpu"))
                if key in seen:
                    continue
                seen.add(key)
                meta["gpus"].append({
                    "hostname": m.get("Hostname"),
                    "uuid": m.get("UUID"),
                    "gpu_index": m.get("gpu"),
                    "model": m.get("modelName"),
                    "device": m.get("device"),
                })
    return meta


# --------------------------------------------------------------------------
# Stats helpers
# --------------------------------------------------------------------------

def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _opt_pct(xs: list[float | None], p: float) -> float | None:
    vs = [x for x in xs if x is not None]
    if not vs:
        return None
    return percentile([float(v) for v in vs], p)


# --------------------------------------------------------------------------
# Phase runner + summariser
# --------------------------------------------------------------------------

def run_phase(
    *, caller: Callable, base_url: str, model: str, prompt: str,
    max_tokens: int, gen_len: int, auth_header: dict, timeout: float,
    concurrency: int, n_runs: int,
) -> list[RunResult]:
    results: list[RunResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [
            ex.submit(
                caller,
                base_url=base_url, model=model, prompt=prompt,
                max_tokens=max_tokens, gen_len=gen_len,
                auth_header=auth_header, timeout=timeout,
            )
            for _ in range(n_runs)
        ]
        for f in as_completed(futs):
            results.append(f.result())
    return results


def summarise(results: list[RunResult], *, min_completion_tokens: int) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    if not ok:
        return {
            "ok_count": 0, "failed_count": len(failed),
            "errors_sample": [r.error for r in failed[:3]],
            "quality_warning": True,
        }
    lat = [r.latency_s for r in ok]
    completion_tokens = [r.completion_tokens for r in ok if r.completion_tokens]
    prompt_tokens = [r.prompt_tokens for r in ok if r.prompt_tokens]
    per_req_decode_tps = [
        r.completion_tokens / r.latency_s
        for r in ok if r.completion_tokens and r.latency_s > 0
    ]
    ttfts = [r.ttft_s for r in ok if r.ttft_s is not None]
    tpots = [r.tpot_ms for r in ok if r.tpot_ms is not None]
    truncated_count = sum(1 for r in ok if r.truncated)
    output_chars = [r.output_chars for r in ok]
    min_chars = min(output_chars) if output_chars else 0
    invalid_utf8 = sum(1 for r in ok if not r.output_valid_utf8)
    quality_warning = (
        any(r.completion_tokens < min_completion_tokens for r in ok)
        or invalid_utf8 > 0
        or len(failed) > 0
    )

    out: dict[str, Any] = {
        "ok_count": len(ok),
        "failed_count": len(failed),
        "errors_sample": [r.error for r in failed[:3]],
        "latency_s_p50": percentile(lat, 50),
        "latency_s_p95": percentile(lat, 95),
        "latency_s_p99": percentile(lat, 99),
        "latency_s_mean": statistics.fmean(lat),
        "completion_tokens_mean": (
            statistics.fmean(completion_tokens) if completion_tokens else 0.0
        ),
        "prompt_tokens_mean": (
            statistics.fmean(prompt_tokens) if prompt_tokens else 0.0
        ),
        "per_req_decode_tokens_per_s_p50": percentile(per_req_decode_tps, 50)
            if per_req_decode_tps else None,
        "per_req_decode_tokens_per_s_p95": percentile(per_req_decode_tps, 95)
            if per_req_decode_tps else None,
        "ttft_s_p50": _opt_pct(ttfts, 50) if ttfts else None,
        "ttft_s_p95": _opt_pct(ttfts, 95) if ttfts else None,
        "ttft_s_p99": _opt_pct(ttfts, 99) if ttfts else None,
        "tpot_ms_p50": _opt_pct(tpots, 50) if tpots else None,
        "tpot_ms_p95": _opt_pct(tpots, 95) if tpots else None,
        "truncated_count": truncated_count,
        "min_output_chars": min_chars,
        "invalid_utf8_count": invalid_utf8,
        "quality_warning": quality_warning,
    }
    return out


def run_sweep(
    *, caller: Callable, args, prompt: str, auth_header: dict,
) -> list[dict]:
    """Run one full concurrency sweep; return list of phase dicts."""
    concurrency_levels = [int(x) for x in args.concurrency.split(",") if x]
    phases: list[dict] = []
    for c in concurrency_levels:
        if args.warmup > 0:
            run_phase(
                caller=caller, base_url=args.url, model=args.model,
                prompt=prompt, max_tokens=args.gen_len, gen_len=args.gen_len,
                auth_header=auth_header, timeout=args.timeout,
                concurrency=c, n_runs=args.warmup,
            )
        phase_start = time.time()
        results = run_phase(
            caller=caller, base_url=args.url, model=args.model,
            prompt=prompt, max_tokens=args.gen_len, gen_len=args.gen_len,
            auth_header=auth_header, timeout=args.timeout,
            concurrency=c, n_runs=args.runs,
        )
        phase_end = time.time()
        wallclock_s = phase_end - phase_start
        summary = summarise(results, min_completion_tokens=args.min_completion_tokens)
        ok_results = [r for r in results if r.ok]
        total_completion = sum(r.completion_tokens for r in ok_results)
        aggregate_tps = total_completion / wallclock_s if wallclock_s else 0.0

        peak_gpu_power_w = None
        if args.prom_url:
            q = "max(DCGM_FI_DEV_POWER_USAGE)"
            if args.prom_gpu_uuid:
                q = ('max(DCGM_FI_DEV_POWER_USAGE{UUID="'
                     + args.prom_gpu_uuid + '"})')
            peak_gpu_power_w = query_prom_peak(
                args.prom_url, q,
                phase_start - args.prom_skew_s,
                phase_end + args.prom_skew_s,
            )

        phases.append({
            "concurrency": c,
            "runs": args.runs,
            "warmup_runs": args.warmup,
            "wallclock_s": wallclock_s,
            "aggregate_decode_tokens_per_s": aggregate_tps,
            "peak_gpu_power_w": peak_gpu_power_w,
            **summary,
        })
    return phases


# --------------------------------------------------------------------------
# Aggregate + knee
# --------------------------------------------------------------------------

_AGG_METRICS = (
    "aggregate_decode_tokens_per_s",
    "latency_s_p50",
    "latency_s_p95",
    "latency_s_p99",
    "ttft_s_p50",
    "ttft_s_p95",
    "tpot_ms_p50",
    "tpot_ms_p95",
    "per_req_decode_tokens_per_s_p50",
    "peak_gpu_power_w",
)


def _stat_block(values: list[float | None]) -> dict[str, float | None]:
    vs = [float(v) for v in values if v is not None]
    if not vs:
        return {"min": None, "median": None, "max": None, "n": 0}
    return {
        "min": min(vs),
        "median": statistics.median(vs),
        "max": max(vs),
        "n": len(vs),
    }


def aggregate_repeats(repeats: list[list[dict]]) -> list[dict]:
    """Reduce N sweeps -> one list of per-c blocks with min/median/max."""
    if not repeats:
        return []
    by_c: dict[int, list[dict]] = {}
    for sweep in repeats:
        for ph in sweep:
            by_c.setdefault(ph["concurrency"], []).append(ph)
    out: list[dict] = []
    for c in sorted(by_c.keys()):
        block: dict[str, Any] = {"concurrency": c, "n_runs_per_repeat": by_c[c][0].get("runs")}
        for m in _AGG_METRICS:
            block[m] = _stat_block([ph.get(m) for ph in by_c[c]])
        # carry through count fields summed
        block["truncated_count_total"] = sum(
            int(ph.get("truncated_count") or 0) for ph in by_c[c]
        )
        block["failed_count_total"] = sum(
            int(ph.get("failed_count") or 0) for ph in by_c[c]
        )
        block["quality_warning_any"] = any(
            bool(ph.get("quality_warning")) for ph in by_c[c]
        )
        out.append(block)
    return out


def detect_knee(
    by_concurrency: list[dict], *,
    plateau_ratio: float, collapse_ratio: float,
) -> tuple[int | None, str | None]:
    """Walk the median sweep, return (knee_c, reason) or (None, None).

    plateau:  agg_tps[c] < plateau_ratio * agg_tps[prev_c]   -> "throughput_plateau"
    collapse: per_req_p50[c] < collapse_ratio * per_req_p50[c=1]  -> "per_request_collapse"
    """
    if not by_concurrency:
        return None, None
    base_per_req = None
    prev_agg = None
    for blk in by_concurrency:
        agg = (blk.get("aggregate_decode_tokens_per_s") or {}).get("median")
        per_req = (blk.get("per_req_decode_tokens_per_s_p50") or {}).get("median")
        c = blk["concurrency"]
        if base_per_req is None and per_req is not None:
            base_per_req = per_req
        if prev_agg is not None and agg is not None and prev_agg > 0:
            if agg < plateau_ratio * prev_agg:
                return c, "throughput_plateau"
        if (base_per_req is not None and per_req is not None
                and per_req < collapse_ratio * base_per_req):
            return c, "per_request_collapse"
        if agg is not None:
            prev_agg = agg
    return None, None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Unbounded Lab benchmark harness")
    ap.add_argument("--engine", required=True, choices=["vllm-openai", "ollama"])
    ap.add_argument("--url", required=True,
                    help="Base URL: for vllm-openai include /v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt-len", type=int, default=512)
    ap.add_argument("--gen-len", type=int, default=128)
    ap.add_argument("--concurrency", default="1,4")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=1,
                    help="Repeat the entire concurrency sweep N times.")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--user", default=os.environ.get("LAB_API_USER", ""))
    ap.add_argument("--pass", dest="passwd",
                    default=os.environ.get("LAB_API_PASS", ""))
    ap.add_argument("--prom-url", default="")
    ap.add_argument("--prom-gpu-uuid", default="")
    ap.add_argument("--prom-skew-s", type=float, default=60.0)
    ap.add_argument("--out", default="/dev/stdout")
    ap.add_argument("--label", default="")
    # v2 toggles:
    stream_grp = ap.add_mutually_exclusive_group()
    stream_grp.add_argument("--stream", dest="stream", action="store_true",
                            default=True,
                            help="Stream responses; capture TTFT and TPOT (default).")
    stream_grp.add_argument("--no-stream", dest="stream", action="store_false",
                            help="Non-streaming (v1-compatible). TTFT/TPOT will be null.")
    ap.add_argument("--min-completion-tokens", type=int, default=16,
                    help="If any successful run returns fewer completion "
                         "tokens than this, the phase is flagged "
                         "quality_warning=true.")
    ap.add_argument("--knee-plateau-ratio", type=float, default=1.05,
                    help="Knee detected if aggregate_tps[c] < ratio * aggregate_tps[prev_c]")
    ap.add_argument("--knee-collapse-ratio", type=float, default=0.5,
                    help="Knee detected if per_req_tps_p50[c] < ratio * per_req_tps_p50[c=1]")
    args = ap.parse_args()

    auth_header: dict = {}
    if args.user and args.passwd:
        import base64
        token = base64.b64encode(
            f"{args.user}:{args.passwd}".encode()
        ).decode()
        auth_header["Authorization"] = f"Basic {token}"

    # Pick caller
    if args.engine == "vllm-openai":
        caller = call_openai_stream if args.stream else call_openai
    else:
        caller = call_ollama_stream if args.stream else call_ollama

    prompt = make_prompt(args.prompt_len)
    started = datetime.now(timezone.utc).isoformat()
    started_unix = time.time()

    meta = gather_meta(engine=args.engine, url=args.url, prom_url=args.prom_url)

    repeats: list[list[dict]] = []
    for i in range(max(1, args.repeats)):
        print(f"# sweep {i+1}/{args.repeats}", file=sys.stderr)
        sweep = run_sweep(
            caller=caller, args=args, prompt=prompt, auth_header=auth_header,
        )
        repeats.append(sweep)

    by_c = aggregate_repeats(repeats)
    knee_c, knee_reason = detect_knee(
        by_c,
        plateau_ratio=args.knee_plateau_ratio,
        collapse_ratio=args.knee_collapse_ratio,
    )

    finished = datetime.now(timezone.utc).isoformat()

    out_obj: dict[str, Any] = {
        "schema": SCHEMA,
        "harness_version": HARNESS_VERSION,
        "label": args.label,
        "engine": args.engine,
        "url": args.url,
        "model": args.model,
        "prompt_len_target_tokens": args.prompt_len,
        "gen_len_max_tokens": args.gen_len,
        "stream": args.stream,
        "concurrency_levels": [int(x) for x in args.concurrency.split(",") if x],
        "runs_per_phase": args.runs,
        "warmup_per_phase": args.warmup,
        "repeats_count": args.repeats,
        "min_completion_tokens": args.min_completion_tokens,
        "knee_plateau_ratio": args.knee_plateau_ratio,
        "knee_collapse_ratio": args.knee_collapse_ratio,
        "started_utc": started,
        "finished_utc": finished,
        "duration_s": time.time() - started_unix,
        "meta": meta,
        "repeats": [{"iter": i, "phases": ph} for i, ph in enumerate(repeats)],
        "aggregate": {"by_concurrency": by_c},
        "knee_concurrency": knee_c,
        "knee_reason": knee_reason,
    }
    # Back-compat: when repeats == 1, also expose flat `phases` so existing
    # v1 jq queries (W1.6 demo) keep working.
    if args.repeats == 1 and repeats:
        out_obj["phases"] = repeats[0]

    out_text = json.dumps(out_obj, indent=2) + "\n"
    if args.out in ("-", "/dev/stdout"):
        sys.stdout.write(out_text)
    else:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            f.write(out_text)
        sys.stdout.write(out_text)
        print(f"# wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
