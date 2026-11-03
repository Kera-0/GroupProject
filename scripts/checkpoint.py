#!/usr/bin/env python3
"""Reproduce checkpoint-1 checks using only Python's standard library."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kv_cache.allocators import ContiguousCache, OutOfMemory, PagedCache


def run_case(name, kind, config):
    kwargs = {key: value for key, value in config.items() if key != "block_tokens"}
    cache = (PagedCache(**kwargs, block_tokens=config["block_tokens"])
             if kind == "paged" else ContiguousCache(**kwargs))
    events = []

    def operation(action, request, value=None):
        start = perf_counter_ns()
        status = "ok"
        try:
            if action == "reserve":
                cache.reserve(request, value)
            elif action == "append":
                cache.append(request, value)
            else:
                cache.free(request)
        except OutOfMemory:
            status = "out_of_memory"
        elapsed = perf_counter_ns() - start
        cache.check_invariants()
        events.append({"operation": action, "request_id": request,
                       "status": status, "duration_ns": elapsed, **cache.snapshot()})
        return status == "ok"

    def append_values(request, length):
        # Each next token payload depends on the previous stored token.
        previous = b""
        expected = []
        for i in range(length):
            value = hashlib.shake_256(previous + request.encode() + bytes([i])).digest(cache.width)
            if not operation("append", request, value):
                return False
            expected.append(value)
            previous = cache.read(request, i)
        assert [cache.read(request, i) for i in range(length)] == expected
        return True

    if name == "memory":
        for request, length in [("a", 3), ("b", 5)]:
            assert operation("reserve", request, 8)
            assert append_values(request, length)
        snapshot = cache.snapshot()
        assert snapshot["live_bytes"] == 64
        assert snapshot["allocated_bytes"] == (96 if kind == "paged" else 128)
    elif name == "capacity":
        # Fully written requests stay resident until the final cleanup.
        for i in range(33):
            request = str(i)
            if not operation("reserve", request, 16):
                break
            if not append_values(request, 4):
                operation("free", request)
                break
        snapshot = cache.snapshot()
        assert snapshot["active_requests"] == (8 if kind == "paged" else 2)
        assert snapshot["allocation_failures"] == 1
    else:
        for request in "abc":
            assert operation("reserve", request, 4)
            assert append_values(request, 4)
        for request in "ac":
            operation("free", request)
        before_attempt = cache.snapshot()
        admitted = operation("reserve", "d", 6)
        if admitted:
            assert append_values("d", 6)
        snapshot = cache.snapshot()
        assert admitted == (kind == "paged")
        assert before_attempt["free_bytes"] == 64
        if kind == "contiguous":
            assert before_attempt["largest_free_span_bytes"] == 32
    for request in list(cache.sequences):
        operation("free", request)
    assert cache.snapshot()["free_bytes"] == len(cache.pool)
    result = {"case": name, "allocator": kind, "config": config,
              "checkpoint_snapshot": snapshot, "events": events,
              "cleanup_snapshot": cache.snapshot()}
    if name == "holes":
        result["before_attempt"] = before_attempt
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/checkpoint-1")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60)
    (output / "tests.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    if tests.returncode:
        sys.stderr.write(tests.stdout + tests.stderr)
        raise SystemExit(tests.returncode)
    config_path = ROOT / "configs/checkpoint-1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cases = [run_case(name, kind, config[name])
             for name in ("memory", "capacity", "holes")
             for kind in ("contiguous", "paged")]
    try:
        ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        ram = None
    report = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "logical_cpus": os.cpu_count(), "physical_ram_bytes": ram},
        "notes": ["Diagnostic fixed traces, not a scheduled serving benchmark.",
                  "duration_ns measures one allocator API call including data write, excluding invariant checks.",
                  "allocated_bytes is assigned pool payload, not RSS or newly allocated OS memory.",
                  "active_requests is resident sequences, not a measured dynamic batch."],
        "cases": cases,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows = ["# Технические проверки первой точки", "",
            "Автоматически построено `python3 scripts/checkpoint.py`.", "",
            "| Сценарий | Аллокатор | Занято, байт | Полезно, байт | Резерв/хвост, байт | Резидентных запросов | Отказов |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for case in cases:
        s = case["checkpoint_snapshot"]
        values = [s[key] for key in ("allocated_bytes", "live_bytes", "slack_bytes", "active_requests", "allocation_failures")]
        rows.append("| " + " | ".join(map(str, [case["case"], case["allocator"]] + values)) + " |")
    rows.extend(["", "Это проверки на аналитически известных примерах; выводов о производительности LLM по ним нет.",
                 "Полный журнал операций, их длительности и окружение: [results.json](results.json).",
                 "Тесты: [tests.log](tests.log). Контрольные суммы: [sha256.json](sha256.json).", ""])
    (output / "summary.md").write_text("\n".join(rows), encoding="utf-8")
    sources = ["kv_cache/__init__.py", "kv_cache/allocators.py", "tests/test_allocators.py",
               "scripts/checkpoint.py", "configs/checkpoint-1.json"]
    hashes = {"source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
              "artifact_sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                  for name in ("tests.log", "results.json", "summary.md")}}
    (output / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print("PASS: unit tests and 6 analytical checks; artifacts: " + str(output))


if __name__ == "__main__":
    main()
