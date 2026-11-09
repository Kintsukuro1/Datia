"""Arnés de evaluación text-to-SQL contra la API real.

No reemplaza una evaluación humana: mide que el pipeline produzca SQL válido,
filas y las columnas esperadas. Si el caso trae `expected_rows`, compara el
resultado completo (execution accuracy).

Uso:
    python scripts/eval_text_to_sql.py --username admin --password admin123
    python scripts/eval_text_to_sql.py --dry-run

Requiere el backend levantado y un LLM local disponible para el modo real.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx


def _normalize_rows(rows):
    return sorted(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        for row in (rows or [])
    )


def _load_golden(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("El golden set no tiene 'cases'.")
    for index, case in enumerate(cases):
        if not case.get("question"):
            raise ValueError(f"El caso {index} no tiene 'question'.")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--golden", default=str(Path(__file__).parent / "eval" / "golden_demo.json"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--connection-id", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Solo valida el golden set; no llama al backend.")
    args = parser.parse_args()

    golden = _load_golden(Path(args.golden))
    if args.dry_run:
        print(json.dumps({
            "golden": str(args.golden),
            "cases": len(golden["cases"]),
            "status": "ok",
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.username or not args.password:
        print("Faltan --username/--password para el modo real.", file=sys.stderr)
        return 2

    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        login = client.post("/api/v1/auth/login", json={
            "username": args.username,
            "password": args.password,
        })
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        connection_id = args.connection_id or golden.get("connection_id", 1)

        results = []
        latencies = []
        for case in golden["cases"]:
            started = time.perf_counter()
            res = client.post(
                "/api/v1/chat/query",
                headers=headers,
                json={"question": case["question"], "connection_id": connection_id},
            )
            latencies.append((time.perf_counter() - started) * 1000)
            payload = {}
            try:
                payload = res.json()
            except ValueError:
                pass
            trace = payload.get("traceability") or {}
            columns = payload.get("data_columns") or []
            rows = payload.get("data_rows") or []
            expected_columns = case.get("expected_columns") or []
            columns_ok = all(col in columns for col in expected_columns)
            validation = (trace.get("validation_status") or "").upper()
            case_ok = (
                res.status_code == 200
                and validation.startswith("APROBADO")
                and columns_ok
                and (not case.get("expected_rows") or _normalize_rows(rows) == _normalize_rows(case["expected_rows"]))
            )
            results.append({
                "id": case.get("id", case["question"]),
                "ok": case_ok,
                "http_status": res.status_code,
                "validation_status": trace.get("validation_status"),
                "rows_returned": len(rows),
                "columns_ok": columns_ok,
                "error": None if case_ok else (payload.get("detail") or payload.get("summary_text") or ""),
            })

        ordered = sorted(latencies)
        p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        report = {
            "cases": len(results),
            "ok": sum(1 for r in results if r["ok"]),
            "valid_sql_rate": round(sum(1 for r in results if (r["validation_status"] or "").upper().startswith("APROBADO")) / len(results), 3),
            "non_empty_rate": round(sum(1 for r in results if r["rows_returned"] > 0) / len(results), 3),
            "p95_latency_ms": round(ordered[p95_index], 2),
            "results": results,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] == report["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
