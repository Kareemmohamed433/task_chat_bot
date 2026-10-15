"""Capacity & Performance Benchmark for AI Task Assistant.

Evaluates performance under the target workload:
- 100 employees
- 2,000 messages/day (~0.07 req/sec average, burst 5-10 concurrent reqs)
Measures throughput, p50, p95, p99 latency, SQLite write contention, and error rates.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import statistics
import concurrent.futures
from datetime import datetime
from app.config import settings
# Force deterministic mode for reproducible capacity benchmark without hitting external rate limits
settings.intent_mode = "deterministic"
os.environ["INTENT_MODE"] = "deterministic"

from starlette.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.seed import seed_employees
from app.models import Task


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import get_db

def run_benchmark():
    print("=" * 60)
    print("AI Task Assistant - Capacity & Performance Benchmark")
    print("Mode: Deterministic (Mocked LLM) | DB: Isolated benchmark.db")
    print("=" * 60)

    bench_db_path = "benchmark.db"
    if os.path.exists(bench_db_path):
        os.remove(bench_db_path)

    bench_engine = create_engine(f"sqlite:///{bench_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=bench_engine)
    BenchSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=bench_engine)

    db = BenchSessionLocal()
    seed_employees(db)
    db.close()

    def override_get_db():
        session = BenchSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Workload mix representative of 100 employees:
    # 40% task listing / checking status
    # 30% policy questions
    # 20% task creations
    # 10% task completions
    test_cases = [
        {"user_id": 4, "message": "وش مهامي اليوم؟"},
        {"user_id": 3, "message": "متى تبدأ ساعات العمل الرسمية؟"},
        {"user_id": 4, "message": "ما هي شروط الإجازة الاعتيادية؟"},
        {"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 14 متابعة فحص الموقع"},
        {"user_id": 4, "message": "أنجزت المهمة رقم 1"},
    ]

    concurrency_levels = [1, 5, 10, 20]
    results = {}

    for c in concurrency_levels:
        num_requests = 100 if c <= 5 else 200
        latencies = []
        errors = 0

        def send_request(req_idx):
            payload = test_cases[req_idx % len(test_cases)].copy()
            if "سجل مهمة" in payload["message"]:
                payload["idempotency_key"] = f"bench-{c}-{req_idx}-{time.time_ns()}"
                payload["message"] = f"سجل مهمة لخالد بكرة الساعة {10 + (req_idx % 8)} متابعة فحص الموقع {req_idx}"
            
            t0 = time.perf_counter()
            try:
                res = client.post("/chat", json=payload)
                elapsed = (time.perf_counter() - t0) * 1000.0  # ms
                if res.status_code == 200:
                    return elapsed, True
                else:
                    return elapsed, False
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000.0
                return elapsed, False

        start_time = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:
            futures = [executor.submit(send_request, i) for i in range(num_requests)]
            for f in concurrent.futures.as_completed(futures):
                elapsed, ok = f.result()
                latencies.append(elapsed)
                if not ok:
                    errors += 1

        total_wall_time = time.perf_counter() - start_time
        throughput = num_requests / total_wall_time
        latencies.sort()

        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        err_rate = (errors / num_requests) * 100.0

        results[c] = {
            "requests": num_requests,
            "concurrency": c,
            "wall_time_sec": total_wall_time,
            "throughput_rps": throughput,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "error_rate_pct": err_rate,
        }

        print(f"Concurrency {c:2d} | Throughput: {throughput:6.1f} req/s | p50: {p50:5.1f}ms | p95: {p95:5.1f}ms | p99: {p99:5.1f}ms | Err: {err_rate:4.1f}%")

    print("\nBenchmark Complete.")
    return results


if __name__ == "__main__":
    run_benchmark()
