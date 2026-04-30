#!/usr/bin/env python3
"""
Unbounded Lab benchmark harness (W1.6).

Hits an OpenAI-compatible chat-completions endpoint, sweeps concurrency
levels, reports p50/p95/p99 latency, tokens/sec (decode and end-to-end),
prompt-eval rate, and peak GPU framebuffer usage if a Prometheus URL is
supplied.

Stdlib-only on purpose: the bench Job image is plain `python:3.12-slim`
and we don't want a pip install in the hot path.

Usage examples (from the cluster, via in-cluster Service):

  python lab_bench.py \\
      --engine vllm-openai \\
      --url http://vllm.lab-vllm-qwen-moe:8000/v1 \\
      --model Qwen/Qwen3-30B-A3B-GPTQ-Int4 \\
      --prompt-len 512 --gen-len 128 \\
      --concurrency 1,4,16 --runs 20 --warmup 3 \\
      --out /results/results.json

  python lab_bench.py \\
      --engine ollama \\
      --url http://ollama.lab-ollama-qwen-moe:11434 \\
      --model qwen3:30b-a3b-q4_K_M \\
      --prompt-len 512 --gen-len 128 \\
      --concurrency 1,4 --runs 20 --warmup 3 \\
      --out /results/ollama.json

The "ollama" engine talks to /api/chat (Ollama-native). The "vllm-openai"
engine talks to OpenAI /v1/chat/completions. Both engines take the same
prompts so results are directly comparable.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any


# A short repeated phrase so the prompt is *roughly* `--prompt-len` tokens.
# We don't tokenize per-engine here; the 1 token ≈ 0.75 word rule suffices
# for a load harness. Real token-level fidelity would require pulling the
# tokenizer per-model and we explicitly don't want that dependency.
_FILLER = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. "
)


def make_prompt(target_tokens: int) -> str:
    """Build a prompt of approximately `target_tokens` tokens.

    Approximation: ~0.75 tokens per word, ~6 chars per word incl. spaces.
    We round up; benchmark methodology requires we record the actual
    target, not the realised count, so the consumer can check.
    """
    target_chars = max(64, int(target_tokens * 4))  # ~4 chars/token
    body = _FILLER * (target_chars // len(_FILLER) + 1)
    body = body[:target_chars]
    return (
        "You are a benchmark target. Reply with a long technical answer. "
        "Context (ignore content, this is filler to reach the target prompt length): "
        + body
        + "\n\nQuestion: explain Kubernetes StatefulSets in detail."
    )


@dataclasses.dataclass
class RunResult:
    ok: bool
    latency_s: float          # end-to-end wall time of the request
    prompt_tokens: int        # as reported by the engine if available
    completion_tokens: int
    error: str | None = None


def _http_post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_openai(
    base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float,
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
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
    )


def call_ollama(
    base_url: str, model: str, prompt: str, max_tokens: int,
    auth_header: dict, timeout: float,
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
    return RunResult(
        ok=True, latency_s=dt,
        prompt_tokens=int(out.get("prompt_eval_count", 0)),
        completion_tokens=int(out.get("eval_count", 0)),
    )


def query_prom_peak(prom_url: str, query: str,
                    start: float, end: float) -> float | None:
    """Range-query Prometheus and return the max sample value, or None.

    Errors are logged to stderr (not swallowed) so a misconfigured prom URL
    is visible in `kubectl logs` instead of producing a silent null in the
    JSON output.
    """
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


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_phase(
    *, caller, base_url: str, model: str, prompt: str,
    max_tokens: int, auth_header: dict, timeout: float,
    concurrency: int, n_runs: int,
) -> list[RunResult]:
    results: list[RunResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [
            ex.submit(caller, base_url, model, prompt, max_tokens,
                      auth_header, timeout)
            for _ in range(n_runs)
        ]
        for f in as_completed(futs):
            results.append(f.result())
    return results


def summarise(results: list[RunResult]) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    if not ok:
        return {
            "ok_count": 0, "failed_count": len(failed),
            "errors_sample": [r.error for r in failed[:3]],
        }
    lat = [r.latency_s for r in ok]
    completion_tokens = [r.completion_tokens for r in ok if r.completion_tokens]
    prompt_tokens = [r.prompt_tokens for r in ok if r.prompt_tokens]
    # Per-request decode rate, then summarise. (Not the same as aggregate
    # tokens/sec under load -- we report both.)
    per_req_decode_tps = [
        r.completion_tokens / r.latency_s
        for r in ok if r.completion_tokens and r.latency_s > 0
    ]
    return {
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
        "per_req_decode_tokens_per_s_p50": percentile(per_req_decode_tps, 50),
        "per_req_decode_tokens_per_s_p95": percentile(per_req_decode_tps, 95),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Unbounded Lab benchmark harness")
    ap.add_argument("--engine", required=True,
                    choices=["vllm-openai", "ollama"])
    ap.add_argument("--url", required=True,
                    help="Base URL: for vllm-openai include /v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt-len", type=int, default=512,
                    help="Approximate prompt length in tokens")
    ap.add_argument("--gen-len", type=int, default=128,
                    help="max_tokens / num_predict")
    ap.add_argument("--concurrency", default="1,4",
                    help="Comma-separated concurrency levels")
    ap.add_argument("--runs", type=int, default=20,
                    help="Total requests per concurrency level")
    ap.add_argument("--warmup", type=int, default=3,
                    help="Warmup requests run + discarded before each phase")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--user", default=os.environ.get("LAB_API_USER", ""))
    ap.add_argument("--pass", dest="passwd",
                    default=os.environ.get("LAB_API_PASS", ""))
    ap.add_argument("--prom-url", default="",
                    help="Optional Prometheus URL for peak GPU FB query")
    ap.add_argument("--prom-gpu-uuid", default="",
                    help="Restrict GPU FB peak query to this DCGM UUID")
    ap.add_argument("--prom-skew-s", type=float, default=60.0,
                    help="Pad each phase window by this many seconds on "
                         "both sides when querying Prometheus, so phases "
                         "shorter than the scrape interval still capture "
                         "samples. Default 60s (~2x DCGM scrape interval).")
    ap.add_argument("--out", default="/dev/stdout")
    ap.add_argument("--label", default="",
                    help="Free-form label written to JSON (e.g. 'w1.2-baseline')")
    args = ap.parse_args()

    # Auth
    auth_header: dict = {}
    if args.user and args.passwd:
        import base64
        token = base64.b64encode(
            f"{args.user}:{args.passwd}".encode()
        ).decode()
        auth_header["Authorization"] = f"Basic {token}"

    caller = call_openai if args.engine == "vllm-openai" else call_ollama
    prompt = make_prompt(args.prompt_len)
    concurrency_levels = [int(x) for x in args.concurrency.split(",") if x]

    started = datetime.now(timezone.utc).isoformat()
    started_unix = time.time()

    phases: list[dict[str, Any]] = []
    for c in concurrency_levels:
        # Warmup (results discarded)
        if args.warmup > 0:
            run_phase(
                caller=caller, base_url=args.url, model=args.model,
                prompt=prompt, max_tokens=args.gen_len,
                auth_header=auth_header, timeout=args.timeout,
                concurrency=c, n_runs=args.warmup,
            )

        phase_start = time.time()
        results = run_phase(
            caller=caller, base_url=args.url, model=args.model,
            prompt=prompt, max_tokens=args.gen_len,
            auth_header=auth_header, timeout=args.timeout,
            concurrency=c, n_runs=args.runs,
        )
        phase_end = time.time()
        wallclock_s = phase_end - phase_start

        summary = summarise(results)
        ok_results = [r for r in results if r.ok]
        total_completion = sum(r.completion_tokens for r in ok_results)
        aggregate_tps = total_completion / wallclock_s if wallclock_s else 0.0

        peak_gpu_power_w = None
        if args.prom_url:
            # DCGM on Spark GB10 (Tegra integrated GPU) does NOT export
            # FB_USED/FREE/TOTAL — those fields are silently absent. The
            # working "GPU was busy" signal on this hardware is
            # DCGM_FI_DEV_POWER_USAGE (watts). See
            # observability/values-dcgm-exporter.yaml header comment.
            q = "max(DCGM_FI_DEV_POWER_USAGE)"
            if args.prom_gpu_uuid:
                q = (
                    'max(DCGM_FI_DEV_POWER_USAGE{UUID="'
                    + args.prom_gpu_uuid + '"})'
                )
            # Pad the window by `--prom-skew-s` on each side so phases
            # shorter than the scrape interval still capture a sample.
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

    finished = datetime.now(timezone.utc).isoformat()
    out_obj = {
        "schema": "unbounded-lab-bench/v1",
        "label": args.label,
        "engine": args.engine,
        "url": args.url,
        "model": args.model,
        "prompt_len_target_tokens": args.prompt_len,
        "gen_len_max_tokens": args.gen_len,
        "started_utc": started,
        "finished_utc": finished,
        "duration_s": time.time() - started_unix,
        "phases": phases,
        "harness_version": "0.1.0",
    }

    out_text = json.dumps(out_obj, indent=2) + "\n"
    if args.out in ("-", "/dev/stdout"):
        sys.stdout.write(out_text)
    else:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            f.write(out_text)
        # Also echo to stdout so `kubectl logs` carries the JSON for free.
        sys.stdout.write(out_text)
        print(f"# wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
