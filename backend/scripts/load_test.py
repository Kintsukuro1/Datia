"""Prueba de carga mínima contra un backend ya levantado.

No usa Locust ni k6: `httpx` ya es dependencia y alcanza para medir health y un
endpoint autenticado. El chat no se incluye porque depende del LLM local y su
latencia no representa la del backend.

Ejemplos:
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --users 10 --seconds 10
    python scripts/load_test.py --base-url http://127.0.0.1:8000 --users 5 --seconds 10 \
        --username admin --password admin123 --path /api/v1/auth/me
"""
import argparse
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def _login(client: httpx.Client, username: str, password: str) -> str:
    res = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    res.raise_for_status()
    return res.json()["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--path", default="/health")
    parser.add_argument("--username")
    parser.add_argument("--password")
    args = parser.parse_args()

    stop = threading.Event()
    latencies: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    with httpx.Client(base_url=args.base_url, timeout=30.0) as setup:
        token = None
        if args.username and args.password:
            token = _login(setup, args.username, args.password)

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def worker() -> None:
        with httpx.Client(base_url=args.base_url, timeout=30.0, headers=headers) as client:
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    res = client.get(args.path)
                    elapsed = (time.perf_counter() - started) * 1000
                    if res.status_code >= 400:
                        raise RuntimeError(f"HTTP {res.status_code}")
                    with lock:
                        latencies.append(elapsed)
                except Exception as exc:
                    with lock:
                        errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=max(1, args.users)) as pool:
        futures = [pool.submit(worker) for _ in range(max(1, args.users))]
        time.sleep(max(0.1, args.seconds))
        stop.set()
        for future in futures:
            future.result()

    if not latencies:
        print(json.dumps({"error": "sin requests exitosas", "errors": errors[:5]}, ensure_ascii=False))
        return 1

    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    report = {
        "path": args.path,
        "users": args.users,
        "seconds": args.seconds,
        "requests_ok": len(latencies),
        "errors": len(errors),
        "rps": round(len(latencies) / max(0.001, args.seconds), 2),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "max_ms": round(max(ordered), 2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
