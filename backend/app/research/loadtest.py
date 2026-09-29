"""Controlled research load harness.

Drives the existing ``submit_research`` / outbox / ``serve`` path. It does not
start a second research runner. The default model is ``FakeLLMProvider``.

These measurements describe one development run. They are not a production
capacity guarantee, and FakeLLM timings are not OpenAI timings.

Percentiles use one nearest-rank method for p50, p95, and p99:
``rank = ceil(percentile / 100 * n)``, 1-based, clamped to ``[1, n]``.
"""

from __future__ import annotations

import hashlib
import math
import os
import platform
import resource
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

LOAD_DATABASE = "quantpilot_load"
DISPOSABLE_PORT = 55432
OWNER = "load-test"
PERCENTILE_METHOD = "nearest-rank"
PERCENTILE_DEFINITION = "rank = ceil(percentile/100 * n), 1-based, clamped to [1, n]"

# Fixture queries already used by the research evaluation set. Not market data.
QUERIES: Dict[str, List[str]] = {
    "news": ["NVIDIA GPU demand news", "NVIDIA data-center GPU demand"],
    "company": [
        "What is AMD",
        "Meta Platforms canonical identity",
        "canonical Microsoft company facts",
    ],
    "technology": [
        "Microsoft custom accelerators",
        "data-center GPU technology",
        "Meta data center accelerators",
    ],
    "market": ["AMD event window returns"],
    "multi": [
        "AMD partnership with Meta",
        "AMD Meta announced partnership Instinct",
        "AMD Meta relationship",
    ],
    "no-result": ["European airport fog delays"],
    "partial-agent": ["AMD Instinct Meta data centers"],
}

# Shares of a 100-request run. Conflict is a separate scenario because it needs
# FakeLLMProvider(scenario="conflicting"), not the default reader.
DEFAULT_WEIGHTS: Dict[str, int] = {
    "news": 20,
    "company": 20,
    "technology": 20,
    "market": 20,
    "multi": 16,
    "no-result": 2,
    "partial-agent": 2,
}

_TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
_METRIC_COUNTERS = (
    "research_queue_published_total",
    "research_queue_publish_failures_total",
    "research_worker_messages_total",
    "research_worker_success_total",
    "research_worker_partial_total",
    "research_worker_failure_total",
    "research_worker_retries_total",
    "research_worker_dlq_total",
    "research_job_reclaimed_total",
    "research_outbox_publish_failures_total",
)


class LoadTargetError(RuntimeError):
    """The harness was pointed at a database it must not load-test."""


def nearest_rank(samples: Sequence[float], percentile: float) -> Optional[float]:
    """Nearest-rank percentile. Same function for p50, p95, and p99."""
    if not samples:
        return None
    if percentile <= 0 or percentile > 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    rank = min(rank, len(ordered))
    return ordered[rank - 1]


def percentiles(samples: Sequence[float]) -> Dict[str, Any]:
    n = len(samples)
    notes: List[str] = []
    if n == 0:
        notes.append("no samples")
    elif n < 100:
        notes.append(f"n={n} is below 100; p99 nearest-rank is reported but is not a stable tail")
    if 0 < n < 20:
        notes.append(f"n={n} is below 20; p95 is a coarse rank")
    return {
        "n": n,
        "method": PERCENTILE_METHOD,
        "definition": PERCENTILE_DEFINITION,
        "p50": nearest_rank(samples, 50) if n else None,
        "p95": nearest_rank(samples, 95) if n else None,
        "p99": nearest_rank(samples, 99) if n else None,
        "notes": notes,
    }


def allocate(n: int, weights: Dict[str, int]) -> Dict[str, int]:
    """Largest-remainder allocation. Counts sum to ``n``."""
    if n < 0:
        raise ValueError("n must be >= 0")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    raw = {kind: n * weight / total for kind, weight in weights.items()}
    counts = {kind: int(value) for kind, value in raw.items()}
    leftover = n - sum(counts.values())
    order = sorted(weights, key=lambda kind: (raw[kind] - counts[kind], kind), reverse=True)
    for kind in order[:leftover]:
        counts[kind] += 1
    return counts


def build_workload(n: int, *, seed: int = 48, weights: Optional[Dict[str, int]] = None) -> List[Dict[str, str]]:
    """Deterministic mixed requests. Consecutive items are not all the same kind."""
    del seed  # allocation is fully determined by n and weights; seed is reserved
    counts = allocate(n, weights or DEFAULT_WEIGHTS)
    buckets: Dict[str, List[Dict[str, str]]] = {}
    for kind, count in counts.items():
        choices = QUERIES[kind]
        buckets[kind] = [
            {"kind": kind, "query": choices[index % len(choices)]}
            for index in range(count)
        ]
    mixed: List[Dict[str, str]] = []
    while any(buckets.values()):
        for kind in counts:
            if buckets[kind]:
                mixed.append(buckets[kind].pop(0))
    return mixed


def assert_disposable_url(url: str) -> None:
    """Refuse developer SQLite, compose Postgres, and the integration database."""
    parsed = make_url(url)
    driver = parsed.drivername or ""
    database = parsed.database or ""
    if driver.startswith("sqlite") or "quantpilot.db" in database or "quantpilot.db" in url:
        raise LoadTargetError("refusing developer SQLite")
    if parsed.port != DISPOSABLE_PORT:
        raise LoadTargetError(
            f"refusing port {parsed.port}; load tests use qp-pg-test on {DISPOSABLE_PORT}"
        )
    if database != LOAD_DATABASE:
        raise LoadTargetError(
            f"refusing database {database!r}; load tests use disposable {LOAD_DATABASE}"
        )


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _sha256(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lake_file_count(root: str) -> int:
    lake = os.path.join(root, "backend", "data", "lake")
    if not os.path.isdir(lake):
        return 0
    return sum(len(files) for _dir, _dirs, files in os.walk(lake))


def developer_integrity() -> Dict[str, Any]:
    """Read-only fingerprint of developer data. The harness does not open it."""
    root = _repo_root()
    sqlite_path = os.path.join(root, "backend", "data", "quantpilot.db")
    neo4j_open = False
    try:
        with socket.create_connection(("127.0.0.1", 7687), timeout=0.2):
            neo4j_open = True
    except OSError:
        neo4j_open = False
    return {
        "sqlite_path": sqlite_path,
        "sqlite_sha256": _sha256(sqlite_path),
        "sqlite_bytes": os.path.getsize(sqlite_path) if os.path.isfile(sqlite_path) else None,
        "lake_file_count": _lake_file_count(root),
        "developer_neo4j_port_7687_open": neo4j_open,
        "note": "Load tests do not query developer Neo4j. The port check is a TCP probe only.",
    }


def _command(args: List[str], timeout: float = 8.0) -> Optional[str]:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def capture_environment() -> Dict[str, Any]:
    ram_bytes = None
    try:
        ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        ram_bytes = None
    if ram_bytes is None:
        raw = _command(["sysctl", "-n", "hw.memsize"])
        if raw and raw.isdigit():
            ram_bytes = int(raw)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_unit = "bytes" if platform.system() == "Darwin" else "kilobytes"
    return {
        "label": "DEVELOPMENT LOAD TEST / FAKELLM / FIXTURE-BASED",
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_brand": _command(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "ram_bytes": ram_bytes,
        "python": sys.version.split()[0],
        "process_max_rss": rss,
        "process_max_rss_unit": rss_unit,
        "docker_version": _command(["docker", "version", "--format", "{{.Server.Version}}"]),
        "worker_process": "in-process threads calling research.worker.serve",
        "worker_cpu_limit": None,
        "worker_memory_limit": None,
        "llm": "FakeLLMProvider",
        "embedding": "FakeEmbeddingProvider",
        "openai_calls": False,
    }


def _admin_url(url: str) -> str:
    parsed = make_url(url)
    return parsed.set(database="postgres").render_as_string(hide_password=False)


def recreate_load_database(url: str) -> None:
    """Drop and create ``quantpilot_load`` on the disposable server only."""
    assert_disposable_url(url)
    admin = create_engine(_admin_url(url), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": LOAD_DATABASE},
            ).scalar()
            if exists:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": LOAD_DATABASE},
                )
                conn.execute(text(f'DROP DATABASE "{LOAD_DATABASE}"'))
            conn.execute(text(f'CREATE DATABASE "{LOAD_DATABASE}"'))
    finally:
        admin.dispose()


def make_engine(url: str) -> Engine:
    assert_disposable_url(url)
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_timeout=30,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _register_pgvector(dbapi_conn, _connection_record) -> None:
        try:
            from pgvector.psycopg2 import register_vector

            register_vector(dbapi_conn)
        except Exception:
            return

    return engine


def make_settings(bootstrap: str, prefix: str):
    from app.core.settings import Settings

    if not prefix.startswith("test."):
        raise LoadTargetError("Kafka topic prefix must start with test.")
    return Settings(
        KAFKA_BOOTSTRAP_SERVERS=bootstrap,
        KAFKA_CLIENT_ID="quantpilot-loadtest",
        KAFKA_TOPIC_NEWS_RAW=f"{prefix}.news.raw",
        KAFKA_TOPIC_MARKET_RAW=f"{prefix}.market.raw",
        KAFKA_TOPIC_COMPANY_RAW=f"{prefix}.company.raw",
        KAFKA_TOPIC_DLQ=f"{prefix}.ingestion.dlq",
        KAFKA_CONSUMER_GROUP_LAKE=f"{prefix}.lake-writer",
        KAFKA_TOPIC_RESEARCH_REQUEST=f"{prefix}.research.request",
        KAFKA_TOPIC_RESEARCH_COMPLETED=f"{prefix}.research.completed",
        KAFKA_TOPIC_RESEARCH_DLQ=f"{prefix}.research.dlq",
        KAFKA_CONSUMER_GROUP_RESEARCH=f"{prefix}.research-worker",
        LLM_PROVIDER="fake",
        EMBEDDING_PROVIDER="fake",
        OPENAI_API_KEY="",
        # Production default is 100. The harness raises it so a 100-request
        # level is not rejected by the cap. The queue-limit scenario sets a
        # small cap and checks durable Postgres state.
        RESEARCH_MAX_QUEUED_JOBS=10_000,
    )


def _engine_versions(engine: Engine) -> Dict[str, Any]:
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        max_connections = conn.execute(text("SHOW max_connections")).scalar()
        shared_buffers = conn.execute(text("SHOW shared_buffers")).scalar()
    return {
        "postgresql_version": version,
        "max_connections": int(max_connections) if str(max_connections).isdigit() else max_connections,
        "shared_buffers": shared_buffers,
        "pool_size": 20,
        "max_overflow": 40,
        "pool_timeout_seconds": 30,
    }


def _kafka_meta(settings) -> Dict[str, Any]:
    image = _command(
        ["docker", "ps", "--filter", "ancestor=apache/kafka:4.3.1", "--format", "{{.Image}} {{.Names}}"]
    )
    return {
        "bootstrap": settings.KAFKA_BOOTSTRAP_SERVERS,
        "image": image,
        "research_partitions": settings.KAFKA_RESEARCH_PARTITIONS,
        "replication_factor": settings.KAFKA_REPLICATION_FACTOR,
        "poll_timeout_seconds": settings.KAFKA_POLL_TIMEOUT_SECONDS,
        "delivery_timeout_ms": settings.KAFKA_DELIVERY_TIMEOUT_MS,
        "ordering": "per research_id partition key only; not global",
        "request_topic": settings.KAFKA_TOPIC_RESEARCH_REQUEST,
        "consumer_group": settings.KAFKA_CONSUMER_GROUP_RESEARCH,
    }


def prepare_topics(settings) -> Any:
    from confluent_kafka.admin import AdminClient

    from app.pipeline.kafka.config import admin_config
    from app.pipeline.kafka.topics import ensure_topics, topic_specs

    admin = AdminClient(admin_config(settings))
    specs = topic_specs(settings)
    names = [spec.name for spec in specs]
    if not all(name.startswith("test.") for name in names):
        raise LoadTargetError(f"refusing non-test topics {names}")
    ensure_topics(admin, specs)
    return admin


def delete_topics(admin, settings) -> None:
    from app.pipeline.kafka.topics import topic_specs

    names = [spec.name for spec in topic_specs(settings)]
    if not names or not all(name.startswith("test.") for name in names):
        return
    for future in admin.delete_topics(names, operation_timeout=30).values():
        future.result()
    stem = settings.KAFKA_TOPIC_RESEARCH_REQUEST.split(".research.")[0]
    groups = [
        group.group_id
        for group in admin.list_consumer_groups().result(timeout=10).valid
        if group.group_id.startswith(stem)
    ]
    if groups:
        for future in admin.delete_consumer_groups(groups, request_timeout=10).values():
            future.result()


def _seed(session_factory) -> None:
    from app.research.evaluate import _seed as seed_fixtures

    with session_factory() as db:
        seed_fixtures(db)
        db.commit()


def _bind_router(settings) -> Callable[[], None]:
    import app.routers.research as routes

    previous = routes.get_settings
    routes.get_settings = lambda: settings

    def restore() -> None:
        routes.get_settings = previous

    return restore


def _submit_one(session_factory, settings, item: Dict[str, str], key: str) -> Dict[str, Any]:
    from fastapi import HTTPException

    from app.routers.research import ResearchBody, create_research

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    db = session_factory()
    try:
        body = ResearchBody(query=item["query"], idempotency_key=key)
        try:
            view = create_research(body, db=db, idempotency_key=key, correlation_id=key)
        except HTTPException as exc:
            db.rollback()
            return {
                "key": key,
                "kind": item["kind"],
                "query": item["query"],
                "research_id": None,
                "status_code": exc.status_code,
                "error": "queue_full" if exc.status_code == 429 else f"http_{exc.status_code}",
                "latency_s": time.perf_counter() - started,
                "submitted_at": started_at.isoformat(),
            }
        return {
            "key": key,
            "kind": item["kind"],
            "query": item["query"],
            "research_id": view["research_id"],
            "status_code": 202,
            "error": None,
            "latency_s": time.perf_counter() - started,
            "submitted_at": started_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        name = type(exc).__name__
        return {
            "key": key,
            "kind": item["kind"],
            "query": item["query"],
            "research_id": None,
            "status_code": None,
            "error": name,
            "latency_s": time.perf_counter() - started,
            "submitted_at": started_at.isoformat(),
        }
    finally:
        db.close()


def submit_many(
    session_factory,
    settings,
    items: Sequence[Dict[str, str]],
    *,
    concurrency: int,
    key_prefix: str,
    keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    if keys is None:
        keys = [f"{key_prefix}-{index:04d}" for index in range(len(items))]
    if len(keys) != len(items):
        raise ValueError("keys and items must be the same length")
    workers = max(1, min(concurrency, len(items) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_submit_one, session_factory, settings, item, key)
            for item, key in zip(items, keys)
        ]
        return [future.result() for future in futures]


class _WorkerHandle:
    def __init__(self) -> None:
        self.threads: List[threading.Thread] = []
        self.consumers: List[Any] = []
        self.errors: List[str] = []
        self._lock = threading.Lock()
        self._stopping = False

    def start(self, session_factory, settings, *, workers: int, llm, embed) -> None:
        from app.pipeline.kafka.config import producer_config
        from app.pipeline.kafka.consumer import build_consumer
        from app.pipeline.kafka.producer import EventProducer
        from app.pipeline.retry import RetryPolicy
        from app.research.worker import serve

        def run(index: int) -> None:
            consumer = build_consumer(settings, settings.KAFKA_CONSUMER_GROUP_RESEARCH)
            producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
            with self._lock:
                self.consumers.append(consumer)
            consumer.subscribe([settings.KAFKA_TOPIC_RESEARCH_REQUEST])
            try:
                serve(
                    session_factory,
                    consumer,
                    producer,
                    settings,
                    llm=llm,
                    embed=embed,
                    worker_id=f"load-{index}",
                    idle_timeout_s=4.0,
                )
            except Exception as exc:
                if not self._stopping:
                    with self._lock:
                        self.errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    consumer.close()
                except Exception:
                    pass
                try:
                    producer.close()
                except Exception:
                    pass

        for index in range(workers):
            thread = threading.Thread(target=run, args=(index,), name=f"load-worker-{index}", daemon=True)
            thread.start()
            self.threads.append(thread)
        time.sleep(0.3)

    def stop(self, timeout: float = 12.0) -> None:
        """Wait for serve() to idle-exit. Closing a consumer from this thread can segfault librdkafka."""
        self._stopping = True
        for thread in self.threads:
            thread.join(timeout=timeout)
        if any(thread.is_alive() for thread in self.threads):
            for consumer in list(self.consumers):
                try:
                    consumer.close()
                except Exception:
                    pass
            for thread in self.threads:
                thread.join(timeout=5)


def _ids(submissions: Sequence[Dict[str, Any]]) -> List[uuid.UUID]:
    return [uuid.UUID(row["research_id"]) for row in submissions if row.get("research_id")]


def wait_terminal(session_factory, job_ids: Sequence[uuid.UUID], deadline: float) -> List[str]:
    from app.domain.models import ResearchJob

    pending = {str(job_id) for job_id in job_ids}
    while pending and time.monotonic() < deadline:
        with session_factory() as db:
            rows = db.execute(
                select(ResearchJob.id, ResearchJob.status).where(ResearchJob.id.in_(list(job_ids)))
            ).all()
        for job_id, status in rows:
            if status in _TERMINAL:
                pending.discard(str(job_id))
        if pending:
            time.sleep(0.05)
    return sorted(pending)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _seconds(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if later is None or earlier is None:
        return None
    return max((later - earlier).total_seconds(), 0.0)


def collect_jobs(session_factory, submissions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    from app.domain.models import AgentRun, Report, ResearchClaim, ResearchJob, ResearchOutbox

    by_id = {row["research_id"]: row for row in submissions if row.get("research_id")}
    job_ids = [uuid.UUID(value) for value in by_id]
    submission = [row["latency_s"] for row in submissions if row.get("error") is None and row.get("latency_s") is not None]
    rejected = [row for row in submissions if row.get("error")]
    if not job_ids:
        return {
            "submissions": len(submissions),
            "successful_submissions": 0,
            "rejected_submissions": len(rejected),
            "rejection_errors": _count([row["error"] for row in rejected]),
            "submission_latency_s": percentiles(submission),
            "statuses": {},
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "incomplete": 0,
        }
    with session_factory() as db:
        jobs = db.scalars(select(ResearchJob).where(ResearchJob.id.in_(job_ids))).all()
        outbox = db.scalars(
            select(ResearchOutbox).where(
                ResearchOutbox.research_job_id.in_(job_ids),
                ResearchOutbox.kind == "request",
            )
        ).all()
        claim_rows = db.execute(
            select(ResearchClaim.research_job_id, ResearchClaim.statement, func.count())
            .where(ResearchClaim.research_job_id.in_(job_ids))
            .group_by(ResearchClaim.research_job_id, ResearchClaim.statement)
        ).all()
        report_rows = db.execute(
            select(Report.research_job_id, func.count())
            .where(Report.research_job_id.in_(job_ids))
            .group_by(Report.research_job_id)
        ).all()
        key_rows = db.execute(
            select(ResearchJob.idempotency_key, func.count())
            .where(ResearchJob.id.in_(job_ids))
            .group_by(ResearchJob.idempotency_key)
        ).all()
        tokens = db.execute(
            select(func.coalesce(func.sum(AgentRun.tokens_in), 0), func.coalesce(func.sum(AgentRun.tokens_out), 0))
            .where(AgentRun.research_job_id.in_(job_ids))
        ).one()
        pending_outbox = db.scalar(
            select(func.count()).select_from(ResearchOutbox).where(
                ResearchOutbox.research_job_id.in_(job_ids),
                ResearchOutbox.status == "pending",
            )
        )
    outbox_by_job = {str(row.research_job_id): row for row in outbox}
    queue_wait: List[float] = []
    publish: List[float] = []
    execution: List[float] = []
    end_to_end: List[float] = []
    post_publish: List[float] = []
    statuses: Dict[str, int] = {}
    retries = 0
    reclaimed = 0
    finished_at: List[datetime] = []
    for job in jobs:
        statuses[job.status] = statuses.get(job.status, 0) + 1
        retries += max((job.attempts or 0) - 1, 0)
        if (job.attempts or 0) > 1:
            reclaimed += 1
        envelope = outbox_by_job.get(str(job.id))
        submitted_at = _aware(datetime.fromisoformat(by_id[str(job.id)]["submitted_at"]))
        published_at = _aware(envelope.published_at) if envelope is not None else None
        created_at = _aware(envelope.created_at) if envelope is not None else None
        started_at = _aware(job.started_at)
        done = _aware(job.finished_at)
        publish_s = _seconds(published_at, created_at)
        wait_s = _seconds(started_at, published_at)
        exec_s = _seconds(done, started_at)
        e2e_s = _seconds(done, submitted_at)
        if publish_s is not None:
            publish.append(publish_s)
        if wait_s is not None:
            queue_wait.append(wait_s)
            post_publish.append(wait_s)
        if exec_s is not None and job.status in _TERMINAL:
            execution.append(exec_s)
        if e2e_s is not None and job.status in _TERMINAL:
            end_to_end.append(e2e_s)
        if done is not None and job.status in _TERMINAL:
            finished_at.append(done)
    duplicate_claims = sum(1 for _job, _statement, count in claim_rows if count > 1)
    duplicate_reports = sum(1 for _job, count in report_rows if count > 1)
    duplicate_jobs = sum(1 for _key, count in key_rows if count > 1)
    submit_start = min(
        (_aware(datetime.fromisoformat(row["submitted_at"])) for row in by_id.values()),
        default=None,
    )
    last_done = max(finished_at) if finished_at else None
    wall = _seconds(last_done, submit_start)
    completed = statuses.get("succeeded", 0) + statuses.get("partial", 0)
    failed = statuses.get("failed", 0)
    incomplete = sum(count for status, count in statuses.items() if status not in _TERMINAL)
    return {
        "submissions": len(submissions),
        "successful_submissions": len(by_id),
        "rejected_submissions": len(rejected),
        "rejection_errors": _count([row["error"] for row in rejected]),
        "submission_latency_s": percentiles(submission),
        "queue_wait_s": percentiles(queue_wait),
        "queue_wait_definition": "started_at - outbox.published_at (time from Kafka publish to worker claim)",
        "outbox_publish_s": percentiles(publish),
        "outbox_publish_definition": "outbox.published_at - outbox.created_at",
        "execution_s": percentiles(execution),
        "execution_definition": "finished_at - started_at, including graph and persistence",
        "end_to_end_s": percentiles(end_to_end),
        "end_to_end_definition": "finished_at - in-process create_research start. Network RTT is not included.",
        "statuses": statuses,
        "completed": completed,
        "partial": statuses.get("partial", 0),
        "failed": failed,
        "incomplete": incomplete,
        "job_attempt_retries": retries,
        "jobs_with_attempts_above_one": reclaimed,
        "pending_outbox": int(pending_outbox or 0),
        "duplicate_claim_statements": duplicate_claims,
        "duplicate_reports": duplicate_reports,
        "duplicate_jobs": duplicate_jobs,
        "idempotency_violations": duplicate_jobs + duplicate_claims + duplicate_reports,
        "wall_clock_s": wall,
        "completion_throughput_jobs_per_s": (completed / wall) if wall else None,
        "completion_throughput_definition": "terminal succeeded+partial jobs / (last finished_at - first submit start)",
        "tokens_in": int(tokens[0] or 0),
        "tokens_out": int(tokens[1] or 0),
        "cost": None,
    }


def _count(values: Iterable[Optional[str]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        if not value:
            continue
        out[value] = out.get(value, 0) + 1
    return out


def _db_stats(engine: Engine) -> Dict[str, Any]:
    with engine.connect() as conn:
        activity = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            )
        ).scalar()
        locks = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar()
        stats = conn.execute(
            text(
                "SELECT xact_commit, xact_rollback, conflicts, deadlocks "
                "FROM pg_stat_database WHERE datname = current_database()"
            )
        ).one()
    return {
        "active_connections": int(activity or 0),
        "lock_waits": int(locks or 0),
        "xact_commit": int(stats[0] or 0),
        "xact_rollback": int(stats[1] or 0),
        "conflicts": int(stats[2] or 0),
        "deadlocks": int(stats[3] or 0),
    }


def _docker_targets() -> List[str]:
    raw = _command(["docker", "ps", "--format", "{{.Names}}"]) or ""
    names = []
    for name in raw.splitlines():
        lowered = name.lower()
        if "kafka" in lowered or "qp-pg" in lowered or lowered.endswith("postgres-1"):
            names.append(name)
    return names


def _docker_sample(names: Sequence[str]) -> List[Dict[str, str]]:
    if not names:
        return []
    raw = _command(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}", *names],
        timeout=15,
    )
    if not raw:
        return []
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
    return rows


class ResourceSampler:
    def __init__(self, engine: Engine, interval_s: float = 2.0) -> None:
        self.engine = engine
        self.interval_s = interval_s
        self.samples: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.targets = _docker_targets()

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    db = _db_stats(self.engine)
                except Exception as exc:
                    db = {"error": type(exc).__name__}
                self.samples.append(
                    {
                        "postgres": db,
                        "docker": _docker_sample(self.targets),
                    }
                )
                self._stop.wait(self.interval_s)

        self._thread = threading.Thread(target=loop, name="load-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 5)
        return summarize_resources(self.samples, self.interval_s, self.targets)


def _parse_cpu(value: str) -> Optional[float]:
    text_value = value.strip().rstrip("%")
    try:
        return float(text_value)
    except ValueError:
        return None


def summarize_resources(samples: Sequence[Dict[str, Any]], interval_s: float, targets: Sequence[str]) -> Dict[str, Any]:
    peak_connections = 0
    lock_waits = 0
    docker_peak: Dict[str, Dict[str, Any]] = {}
    for sample in samples:
        postgres = sample.get("postgres") or {}
        peak_connections = max(peak_connections, int(postgres.get("active_connections") or 0))
        lock_waits = max(lock_waits, int(postgres.get("lock_waits") or 0))
        for row in sample.get("docker") or []:
            name = row["name"]
            cpu = _parse_cpu(row.get("cpu") or "")
            current = docker_peak.setdefault(name, {"peak_cpu_percent": None, "memory_samples": []})
            if cpu is not None:
                current["peak_cpu_percent"] = max(cpu, current["peak_cpu_percent"] or 0)
            current["memory_samples"].append(row.get("memory"))
    for name, row in docker_peak.items():
        memories = [item for item in row.pop("memory_samples") if item]
        row["last_memory"] = memories[-1] if memories else None
    return {
        "sampling_interval_s": interval_s,
        "sample_count": len(samples),
        "docker_targets": list(targets),
        "peak_postgres_connections": peak_connections,
        "peak_lock_waits": lock_waits,
        "containers": docker_peak,
    }


def _metric_snapshot() -> Dict[str, float]:
    from app.research.metrics import RESEARCH_METRICS

    return {name: float(RESEARCH_METRICS.total(name)) for name in _METRIC_COUNTERS}


def _metric_delta(before: Dict[str, float]) -> Dict[str, float]:
    after = _metric_snapshot()
    return {name: after.get(name, 0.0) - before.get(name, 0.0) for name in _METRIC_COUNTERS}


def _cardinality() -> Dict[str, Any]:
    from app.pipeline.metrics import FORBIDDEN_LABELS
    from app.research.metrics import RESEARCH_METRICS

    keys = RESEARCH_METRICS.label_keys()
    forbidden = sorted(set(keys) & set(FORBIDDEN_LABELS))
    return {
        "label_keys": keys,
        "forbidden_label_keys": forbidden,
        "outbox_pending_gauge": RESEARCH_METRICS.value(
            "research_outbox_pending", operation="outbox", status="pending", component="research"
        ),
    }


def _watermarks(settings) -> Dict[str, Any]:
    from confluent_kafka import Consumer, TopicPartition

    from app.pipeline.kafka.config import consumer_config

    consumer = Consumer(consumer_config(settings, f"{settings.KAFKA_CONSUMER_GROUP_RESEARCH}.lag"))
    try:
        counts = {}
        for topic in (
            settings.KAFKA_TOPIC_RESEARCH_REQUEST,
            settings.KAFKA_TOPIC_RESEARCH_COMPLETED,
            settings.KAFKA_TOPIC_RESEARCH_DLQ,
        ):
            meta = consumer.list_topics(topic=topic, timeout=5).topics.get(topic)
            partitions = sorted((meta.partitions or {}).keys()) if meta is not None else []
            per_partition = {}
            for partition in partitions:
                _low, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=5, cached=False)
                per_partition[str(partition)] = int(high)
            counts[topic] = {"partitions": per_partition, "messages": sum(per_partition.values())}
        return {
            "ordering": "per research_id key and partition only; not global",
            "topics": counts,
        }
    finally:
        consumer.close()


def _group_lag(admin, settings) -> Dict[str, Any]:
    """Committed lag for the worker group. Does not join the group."""
    from confluent_kafka import Consumer, ConsumerGroupTopicPartitions, TopicPartition

    from app.pipeline.kafka.config import consumer_config

    group = settings.KAFKA_CONSUMER_GROUP_RESEARCH
    topic = settings.KAFKA_TOPIC_RESEARCH_REQUEST
    try:
        described = admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group)])
        result = described[group].result(timeout=10)
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "total_lag": None}
    partitions = list(getattr(result, "topic_partitions", None) or [])
    consumer = Consumer(consumer_config(settings, f"{group}.lag-read"))
    try:
        meta = consumer.list_topics(topic=topic, timeout=5).topics.get(topic)
        known = sorted((meta.partitions or {}).keys()) if meta is not None else []
        committed = {
            tp.partition: tp.offset
            for tp in partitions
            if tp.topic == topic and tp.offset is not None and tp.offset >= 0
        }
        lag = {}
        total = 0
        for partition in known:
            _low, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=5, cached=False)
            # A missing commit means the group has not read this partition.
            offset = committed.get(partition, 0)
            value = max(int(high) - int(offset), 0)
            lag[str(partition)] = value
            total += value
        return {
            "available": True,
            "partitions": lag,
            "total_lag": total,
            "method": "high watermark minus committed group offset; uncommitted partition uses offset 0",
        }
    finally:
        consumer.close()


class LagSampler:
    """Samples committed request-topic lag without joining the worker group."""

    def __init__(self, admin, settings, interval_s: float = 0.5) -> None:
        self.admin = admin
        self.settings = settings
        self.interval_s = interval_s
        self.samples: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _once(self, consumer, partitions: Sequence[int]) -> Dict[str, Any]:
        from confluent_kafka import ConsumerGroupTopicPartitions, TopicPartition

        group = self.settings.KAFKA_CONSUMER_GROUP_RESEARCH
        topic = self.settings.KAFKA_TOPIC_RESEARCH_REQUEST
        try:
            described = self.admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group)])
            result = described[group].result(timeout=10)
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {exc}", "total_lag": None}
        committed = {
            tp.partition: tp.offset
            for tp in (getattr(result, "topic_partitions", None) or [])
            if tp.topic == topic and tp.offset is not None and tp.offset >= 0
        }
        lag = {}
        total = 0
        for partition in partitions:
            _low, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=5, cached=False)
            value = max(int(high) - int(committed.get(partition, 0)), 0)
            lag[str(partition)] = value
            total += value
        return {"available": True, "partitions": lag, "total_lag": total}

    def start(self) -> None:
        from confluent_kafka import Consumer

        from app.pipeline.kafka.config import consumer_config

        topic = self.settings.KAFKA_TOPIC_RESEARCH_REQUEST
        consumer = Consumer(
            consumer_config(self.settings, f"{self.settings.KAFKA_CONSUMER_GROUP_RESEARCH}.lag-read")
        )
        meta = consumer.list_topics(topic=topic, timeout=5).topics.get(topic)
        partitions = sorted((meta.partitions or {}).keys()) if meta is not None else []

        def loop() -> None:
            try:
                while not self._stop.is_set():
                    self.samples.append(self._once(consumer, partitions))
                    self._stop.wait(self.interval_s)
            finally:
                consumer.close()

        self._thread = threading.Thread(target=loop, name="load-lag", daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        totals = [sample.get("total_lag") for sample in self.samples if isinstance(sample.get("total_lag"), int)]
        end = self.samples[-1] if self.samples else {}
        return {
            "sampling_interval_s": self.interval_s,
            "sample_count": len(self.samples),
            "peak_lag": max(totals) if totals else None,
            "end_lag": end.get("total_lag"),
            "end_partitions": end.get("partitions"),
            "available": any(sample.get("available") for sample in self.samples),
            "method": "high watermark minus committed group offset; uncommitted partition uses offset 0",
        }


def _llm(scenario: str = "valid", fail_agents: Optional[set] = None):
    from app.research.llm import FakeLLMProvider

    return FakeLLMProvider(scenario=scenario, fail_agents=fail_agents)


def _embed():
    from app.retrieval.embeddings import FakeEmbeddingProvider

    return FakeEmbeddingProvider()


class RateLimitedLLM:
    """Fails a deterministic fraction of FakeLLM calls with a retryable timeout."""

    def __init__(self, percent: int) -> None:
        from app.research.llm import FakeLLMProvider

        self.inner = FakeLLMProvider()
        self.percent = percent
        self.calls = 0
        self.injected = 0
        self._lock = threading.Lock()

    def generate_structured(self, **kwargs):
        from app.research.llm import LLMError

        with self._lock:
            self.calls += 1
            call = self.calls
            period = max(1, int(round(100 / self.percent))) if self.percent else 0
            fail = bool(period) and call % period == 0
            if fail:
                self.injected += 1
        if fail:
            raise LLMError("injected transient timeout", retryable=True, error_type="timeout")
        return self.inner.generate_structured(**kwargs)


class FailOncePublisher:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.failures = 0
        self._failed = False

    def publish(self, message):
        from app.pipeline.kafka.producer import DeliveryFailed

        if not self._failed:
            self._failed = True
            self.failures += 1
            raise DeliveryFailed("injected", "one publish failure", retriable=True)
        return self.inner.publish(message)

    def close(self) -> None:
        self.inner.close()


class GatePublisher:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.open = False
        self.blocked = 0

    def publish(self, message):
        from app.pipeline.kafka.producer import DeliveryFailed

        if not self.open:
            self.blocked += 1
            raise DeliveryFailed("paused", "kafka publication paused", retriable=True)
        return self.inner.publish(message)

    def close(self) -> None:
        self.inner.close()


def run_level(
    session_factory,
    settings,
    admin,
    *,
    requests: int,
    concurrency: int,
    workers: int,
    label: str,
    llm=None,
    weights: Optional[Dict[str, int]] = None,
    timeout_s: Optional[float] = None,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    items = build_workload(requests, weights=weights)
    prefix = f"{label}-{uuid.uuid4().hex[:8]}"
    before = _metric_snapshot()
    db_before = _db_stats(engine) if engine is not None else None
    sampler = ResourceSampler(engine) if engine is not None else None
    if sampler is not None:
        sampler.start()
    lag_sampler = LagSampler(admin, settings)
    lag_sampler.start()
    provider = llm if llm is not None else _llm()
    handle = _WorkerHandle()
    submit_started = time.perf_counter()
    try:
        handle.start(session_factory, settings, workers=workers, llm=provider, embed=_embed())
        submissions = submit_many(
            session_factory,
            settings,
            items,
            concurrency=concurrency,
            key_prefix=prefix,
        )
        submit_elapsed = time.perf_counter() - submit_started
        job_ids = _ids(submissions)
        limit = timeout_s if timeout_s is not None else max(180.0, requests * 3.0)
        incomplete = wait_terminal(session_factory, job_ids, time.monotonic() + limit)
        if not incomplete:
            time.sleep(1.2)
    finally:
        handle.stop()
        lag = lag_sampler.stop()
        resources = sampler.stop() if sampler is not None else None
    collected = collect_jobs(session_factory, submissions)
    collected["label"] = label
    collected["concurrency"] = concurrency
    collected["workers"] = workers
    collected["requests"] = requests
    collected["workload_counts"] = _count(item["kind"] for item in items)
    collected["submission_wall_clock_s"] = submit_elapsed
    successful = collected["successful_submissions"]
    collected["submission_throughput_requests_per_s"] = (
        successful / submit_elapsed if submit_elapsed else None
    )
    collected["submission_throughput_definition"] = (
        "successful create_research calls / submit wall clock. This is not completion throughput."
    )
    collected["incomplete_ids"] = len(incomplete)
    collected["worker_errors"] = handle.errors
    collected["metrics_delta"] = _metric_delta(before)
    collected["kafka"] = {
        "watermarks": _watermarks(settings),
        "consumer_lag": lag,
    }
    if engine is not None and db_before is not None:
        db_after = _db_stats(engine)
        collected["database"] = {
            "before": db_before,
            "after": db_after,
            "xact_rollback_delta": db_after["xact_rollback"] - db_before["xact_rollback"],
            "deadlock_delta": db_after["deadlocks"] - db_before["deadlocks"],
            "conflict_delta": db_after["conflicts"] - db_before["conflicts"],
            "connection_errors": collected["rejection_errors"].get("OperationalError", 0),
            "xact_rollback_note": (
                "pg_stat_database.xact_rollback also increments when a session closes an uncommitted read. "
                "It is not a count of failed research transactions."
            ),
        }
    collected["resources"] = resources
    collected["llm"] = type(provider).__name__
    collected["injected_llm_failures"] = getattr(provider, "injected", None)
    return _round_tree(collected)


def scenario_idempotency(session_factory, settings, admin, *, requests: int = 40, duplicate_fraction: float = 0.2) -> Dict[str, Any]:
    items = build_workload(requests)
    prefix = f"idem-{uuid.uuid4().hex[:8]}"
    keys = [f"{prefix}-{index:04d}" for index in range(requests)]
    first = submit_many(session_factory, settings, items, concurrency=10, key_prefix=prefix, keys=keys)
    duplicate_n = max(1, int(round(requests * duplicate_fraction)))
    dup_items = items[:duplicate_n]
    dup_keys = keys[:duplicate_n]
    second = submit_many(
        session_factory, settings, dup_items, concurrency=duplicate_n, key_prefix=prefix, keys=dup_keys
    )
    same = sum(
        1
        for left, right in zip(first, second)
        if left.get("research_id") and left["research_id"] == right.get("research_id")
    )
    handle = _WorkerHandle()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        incomplete = wait_terminal(session_factory, _ids(first), time.monotonic() + 180)
    finally:
        handle.stop()
    collected = collect_jobs(session_factory, first)
    return _round_tree(
        {
            "logical_requests": requests,
            "duplicate_submissions": duplicate_n,
            "duplicate_fraction": duplicate_fraction,
            "idempotency_hits": same,
            "incomplete": len(incomplete),
            "duplicate_jobs": collected["duplicate_jobs"],
            "duplicate_claim_statements": collected["duplicate_claim_statements"],
            "duplicate_reports": collected["duplicate_reports"],
            "idempotency_violations": collected["idempotency_violations"],
            "statuses": collected["statuses"],
            "kafka_lag": _group_lag(admin, settings),
        }
    )


def scenario_same_and_different_keys(session_factory, settings) -> Dict[str, Any]:
    item = {"kind": "news", "query": "NVIDIA GPU demand news"}
    same_key = f"same-{uuid.uuid4().hex[:8]}"
    same = submit_many(
        session_factory,
        settings,
        [item] * 8,
        concurrency=8,
        key_prefix="same",
        keys=[same_key] * 8,
    )
    different_keys = [f"diff-{uuid.uuid4().hex[:8]}-{index}" for index in range(8)]
    different = submit_many(
        session_factory,
        settings,
        [dict(item) for _ in range(8)],
        concurrency=8,
        key_prefix="diff",
        keys=different_keys,
    )
    same_ids = {row["research_id"] for row in same if row.get("research_id")}
    different_ids = {row["research_id"] for row in different if row.get("research_id")}
    return {
        "same_key_submissions": len(same),
        "same_key_distinct_jobs": len(same_ids),
        "same_key_errors": _count(row["error"] for row in same),
        "different_key_submissions": len(different),
        "different_key_distinct_jobs": len(different_ids),
        "different_key_errors": _count(row["error"] for row in different),
    }


def scenario_replay(session_factory, settings, *, jobs: int = 10) -> Dict[str, Any]:
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.producer import EventProducer, OutboundMessage
    from app.pipeline.retry import RetryPolicy
    from app.research.envelope import ResearchEvent, encode_event

    items = build_workload(jobs)
    level_settings = settings
    prefix = f"replay-{uuid.uuid4().hex[:8]}"
    submissions = submit_many(session_factory, level_settings, items, concurrency=5, key_prefix=prefix)
    handle = _WorkerHandle()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        wait_terminal(session_factory, _ids(submissions), time.monotonic() + 180)
    finally:
        handle.stop()
    before = collect_jobs(session_factory, submissions)
    from app.domain.models import ResearchOutbox

    job_ids = _ids(submissions)
    before_claims = _claim_rows(session_factory, job_ids)
    with session_factory() as db:
        rows = db.scalars(
            select(ResearchOutbox).where(
                ResearchOutbox.research_job_id.in_(job_ids),
                ResearchOutbox.kind == "request",
                ResearchOutbox.status == "published",
            )
        ).all()
        payloads = [(str(row.research_job_id), row.payload, row.topic) for row in rows]
    producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    try:
        for research_id, payload, topic in payloads:
            event = ResearchEvent.model_validate(payload)
            producer.publish(
                OutboundMessage(
                    topic=topic,
                    key=research_id,
                    value=encode_event(event, settings.KAFKA_MAX_MESSAGE_BYTES),
                )
            )
    finally:
        producer.close()
    handle = _WorkerHandle()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        time.sleep(3)
        wait_terminal(session_factory, job_ids, time.monotonic() + 60)
    finally:
        handle.stop()
    after = collect_jobs(session_factory, submissions)
    after_claims = _claim_rows(session_factory, job_ids)
    return _round_tree(
        {
            "replayed_messages": len(payloads),
            "statuses_before": before["statuses"],
            "statuses_after": after["statuses"],
            "duplicate_claim_statements": after["duplicate_claim_statements"],
            "duplicate_reports": after["duplicate_reports"],
            "duplicate_jobs": after["duplicate_jobs"],
            "claim_rows_before": before_claims,
            "claim_rows_after": after_claims,
            "delivery": "at-least-once; not exactly-once",
        }
    )


def _claim_rows(session_factory, job_ids: Sequence[uuid.UUID]) -> int:
    from app.domain.models import ResearchClaim

    with session_factory() as db:
        return int(
            db.scalar(
                select(func.count()).select_from(ResearchClaim).where(ResearchClaim.research_job_id.in_(list(job_ids)))
            )
            or 0
        )


def scenario_crash(session_factory, settings) -> Dict[str, Any]:
    """Acquire a lease, expire it, and let another worker finish the job.

    Production ``RESEARCH_LEASE_SECONDS`` is left unchanged. The test writes
    ``lease_until`` into the past so it does not wait out the production lease.
    """
    from app.domain.models import ResearchClaim, ResearchJob
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.consumer import build_consumer
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy
    from app.research.outbox import publish_pending
    from app.research.worker import _policy, accept_message

    item = [{"kind": "news", "query": "NVIDIA GPU demand news"}]
    submissions = submit_many(session_factory, settings, item, concurrency=1, key_prefix=f"crash-{uuid.uuid4().hex[:8]}")
    job_id = _ids(submissions)[0]
    producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    with session_factory() as db:
        publish_pending(
            db,
            producer,
            worker_id="load-crash-publish",
            batch=settings.RESEARCH_OUTBOX_BATCH,
            lease_seconds=settings.RESEARCH_LEASE_SECONDS,
            max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
            policy=_policy(settings),
        )
        db.commit()
    producer.close()
    consumer = build_consumer(settings, settings.KAFKA_CONSUMER_GROUP_RESEARCH)
    consumer.subscribe([settings.KAFKA_TOPIC_RESEARCH_REQUEST])
    message = None
    deadline = time.monotonic() + 20
    while message is None and time.monotonic() < deadline:
        polled = consumer.poll(0.5)
        if polled is not None and not polled.error():
            message = polled
    if message is None:
        consumer.close()
        return {"measured": False, "reason": "request message was not delivered"}
    with session_factory() as db:
        outcome, claimed = accept_message(db, message.value(), settings, worker_id="load-crashed")
        db.commit()
    consumer.close()  # offset is not committed; the message can be delivered again
    with session_factory() as db:
        job = db.get(ResearchJob, job_id)
        job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        lease_seconds = settings.RESEARCH_LEASE_SECONDS
    handle = _WorkerHandle()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        incomplete = wait_terminal(session_factory, [job_id], time.monotonic() + 90)
    finally:
        handle.stop()
    collected = collect_jobs(session_factory, submissions)
    with session_factory() as db:
        claim_count = db.scalar(
            select(func.count()).select_from(ResearchClaim).where(ResearchClaim.research_job_id == job_id)
        )
        status = db.get(ResearchJob, job_id).status
    return {
        "measured": True,
        "first_outcome": outcome,
        "claimed_job": str(claimed) if claimed else None,
        "production_lease_seconds_unchanged": lease_seconds,
        "final_status": status,
        "incomplete": len(incomplete),
        "claim_rows": int(claim_count or 0),
        "duplicate_claim_statements": collected["duplicate_claim_statements"],
        "duplicate_reports": collected["duplicate_reports"],
        "duplicate_jobs": collected["duplicate_jobs"],
        "attempts": collected["jobs_with_attempts_above_one"],
    }


def scenario_outbox_backlog(session_factory, settings, *, requests: int = 20) -> Dict[str, Any]:
    from app.domain.enums import ResearchJobStatus
    from app.domain.models import ResearchJob, ResearchOutbox
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy
    from app.research.outbox import publish_pending
    from app.research.worker import _policy

    items = build_workload(requests)
    submissions = submit_many(
        session_factory, settings, items, concurrency=10, key_prefix=f"backlog-{uuid.uuid4().hex[:8]}"
    )
    job_ids = _ids(submissions)
    inner = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    gate = GatePublisher(inner)
    with session_factory() as db:
        publish_pending(
            db,
            gate,
            worker_id="load-paused",
            batch=settings.RESEARCH_OUTBOX_BATCH,
            lease_seconds=settings.RESEARCH_LEASE_SECONDS,
            max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
            policy=_policy(settings),
        )
        db.commit()
        pending = db.scalar(
            select(func.count()).select_from(ResearchOutbox).where(
                ResearchOutbox.research_job_id.in_(job_ids),
                ResearchOutbox.status == "pending",
            )
        )
        publish_pending_jobs = db.scalar(
            select(func.count()).select_from(ResearchJob).where(
                ResearchJob.id.in_(job_ids),
                ResearchJob.status == ResearchJobStatus.PUBLISH_PENDING.value,
            )
        )
    gate.open = True
    handle = _WorkerHandle()
    # The worker builds its own producer. The gate already recorded the pause.
    # Drain with a normal worker so publication is restored.
    gate.close()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        incomplete = wait_terminal(session_factory, job_ids, time.monotonic() + 180)
    finally:
        handle.stop()
    collected = collect_jobs(session_factory, submissions)
    return _round_tree(
        {
            "requests": requests,
            "blocked_publish_calls": gate.blocked,
            "pending_outbox_while_paused": int(pending or 0),
            "publish_pending_jobs_while_paused": int(publish_pending_jobs or 0),
            "incomplete": len(incomplete),
            "pending_outbox_after": collected["pending_outbox"],
            "statuses": collected["statuses"],
            "duplicate_claim_statements": collected["duplicate_claim_statements"],
            "duplicate_jobs": collected["duplicate_jobs"],
            "duplicate_reports": collected["duplicate_reports"],
        }
    )


def scenario_queue_limit(session_factory, settings) -> Dict[str, Any]:
    from app.domain.enums import ResearchJobStatus
    from app.domain.models import ResearchJob

    previous = settings.RESEARCH_MAX_QUEUED_JOBS
    with session_factory() as db:
        active = db.scalar(
            select(func.count()).select_from(ResearchJob).where(
                ResearchJob.status.in_(
                    [
                        ResearchJobStatus.PUBLISH_PENDING.value,
                        ResearchJobStatus.QUEUED.value,
                        ResearchJobStatus.RUNNING.value,
                    ]
                )
            )
        )
    cap = int(active or 0) + 2
    settings.RESEARCH_MAX_QUEUED_JOBS = cap
    try:
        items = [{"kind": "news", "query": "NVIDIA GPU demand news"} for _ in range(3)]
        submissions = submit_many(
            session_factory, settings, items, concurrency=1, key_prefix=f"cap-{uuid.uuid4().hex[:8]}"
        )
    finally:
        settings.RESEARCH_MAX_QUEUED_JOBS = previous
    codes = [row["status_code"] for row in submissions]
    return {
        "active_before": int(active or 0),
        "cap": cap,
        "cap_source": "durable count of publish_pending+queued+running",
        "status_codes": codes,
        "accepted": codes.count(202),
        "rejected_429": codes.count(429),
        "harness_cap_restored_to": previous,
        "production_code_default": 100,
    }


def scenario_publish_failure(session_factory, settings) -> Dict[str, Any]:
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy
    from app.research.outbox import publish_pending
    from app.research.worker import _policy

    items = build_workload(10)
    submissions = submit_many(
        session_factory, settings, items, concurrency=5, key_prefix=f"pubfail-{uuid.uuid4().hex[:8]}"
    )
    inner = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    flaky = FailOncePublisher(inner)
    with session_factory() as db:
        first = publish_pending(
            db,
            flaky,
            worker_id="load-pubfail",
            batch=settings.RESEARCH_OUTBOX_BATCH,
            lease_seconds=settings.RESEARCH_LEASE_SECONDS,
            max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
            policy=_policy(settings),
        )
        db.commit()
    handle = _WorkerHandle()
    try:
        handle.start(session_factory, settings, workers=1, llm=_llm(), embed=_embed())
        incomplete = wait_terminal(session_factory, _ids(submissions), time.monotonic() + 180)
    finally:
        flaky.close()
        handle.stop()
    collected = collect_jobs(session_factory, submissions)
    return _round_tree(
        {
            "injected_publish_failures": flaky.failures,
            "published_on_failing_batch": first,
            "incomplete": len(incomplete),
            "statuses": collected["statuses"],
            "pending_outbox": collected["pending_outbox"],
            "duplicate_claim_statements": collected["duplicate_claim_statements"],
            "duplicate_jobs": collected["duplicate_jobs"],
        }
    )


def scenario_db_rollback(session_factory, settings) -> Dict[str, Any]:
    """One statement timeout aborts the transaction. No job row is kept."""
    from app.domain.models import ResearchJob
    from app.research.submit import submit_research
    from app.retrieval.package import ResearchRequest

    key = f"dbfail-{uuid.uuid4().hex[:8]}"
    request = ResearchRequest(query="NVIDIA GPU demand news")
    db = session_factory()
    timed_out: Any = False
    found = None
    try:
        db.execute(text("SET LOCAL statement_timeout = '1ms'"))
        try:
            db.execute(text("SELECT pg_sleep(0.2)"))
        except Exception as exc:
            timed_out = type(exc).__name__
            db.rollback()
        if timed_out:
            submit_research(
                db,
                request,
                owner_key=OWNER,
                idempotency_key=key,
                correlation_id=key,
                settings=settings,
            )
            db.rollback()
        found = db.scalar(select(ResearchJob).where(ResearchJob.idempotency_key == key))
    finally:
        db.close()
    return {
        "measured": bool(timed_out),
        "timeout_error": timed_out or None,
        "job_row_after_rollback": found is not None,
        "note": "Injected as one statement_timeout plus rollback. No container was killed.",
    }


def scenario_failures(session_factory, settings, admin, *, percent: int, requests: int = 40) -> Dict[str, Any]:
    provider = RateLimitedLLM(percent)
    result = run_level(
        session_factory,
        settings,
        admin,
        requests=requests,
        concurrency=5,
        workers=1,
        label=f"fail-{percent}",
        llm=provider,
        timeout_s=max(180.0, requests * 5.0),
    )
    result["injected_llm_failures"] = provider.injected
    result["injection"] = (
        f"every {max(1, int(round(100 / percent)))}th FakeLLM generate_structured call "
        "raises retryable LLMError timeout"
    )
    result["sample_note"] = (
        f"{requests} requests. Smaller than the 100-request latency levels. "
        "p99 is reported with the nearest-rank small-sample note."
    )
    return result


def _round_tree(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _round_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_tree(item) for item in value]
    return value


def _latency_jumps(matrix: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Places where completion p95 at least doubled. Not a capacity claim."""
    jumps = []
    for previous, current in zip(matrix, matrix[1:]):
        prev_p95 = (previous.get("end_to_end_s") or {}).get("p95")
        cur_p95 = (current.get("end_to_end_s") or {}).get("p95")
        if not prev_p95 or not cur_p95 or prev_p95 <= 0:
            continue
        if cur_p95 >= prev_p95 * 2:
            jumps.append(
                {
                    "from_concurrency": previous.get("concurrency"),
                    "to_concurrency": current.get("concurrency"),
                    "p95_from_s": prev_p95,
                    "p95_to_s": cur_p95,
                    "observation": "p95 completion latency at least doubled between these levels",
                }
            )
    return jumps


def _dominant_stage(level: Dict[str, Any]) -> Optional[str]:
    stages = {
        "submission": (level.get("submission_latency_s") or {}).get("p50"),
        "outbox_publish": (level.get("outbox_publish_s") or {}).get("p50"),
        "post_publish_wait": (level.get("queue_wait_s") or {}).get("p50"),
        "execution": (level.get("execution_s") or {}).get("p50"),
    }
    present = {name: seconds for name, seconds in stages.items() if isinstance(seconds, (int, float))}
    if not present:
        return None
    return max(present, key=present.get)


def run_load(
    *,
    database_url: str,
    kafka_bootstrap: str,
    requests: int = 100,
    concurrency: Optional[Sequence[int]] = None,
    scaling_workers: Optional[Sequence[int]] = None,
    scaling_concurrency: int = 10,
    include_scenarios: bool = True,
    include_matrix: bool = True,
) -> Dict[str, Any]:
    import logging

    from app.core.migrations import upgrade_to_head

    previous_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    assert_disposable_url(database_url)
    if not kafka_bootstrap:
        raise LoadTargetError("Kafka bootstrap is required")
    levels = list(concurrency or [1, 5, 10, 25, 50])
    integrity_before = developer_integrity()
    environment = capture_environment()
    prefix = f"test.{uuid.uuid4().hex[:12]}"
    recreate_load_database(database_url)
    engine = make_engine(database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = make_settings(kafka_bootstrap, prefix)
    restore = _bind_router(settings)
    admin = None
    report: Dict[str, Any] = {}
    try:
        upgrade_to_head(engine)
        admin = prepare_topics(settings)
        _seed(session_factory)
        environment["postgresql"] = _engine_versions(engine)
        environment["kafka"] = _kafka_meta(settings)
        environment["dataset"] = "research evaluation fixtures (example.com sources)"
        environment["request_weights"] = DEFAULT_WEIGHTS
        environment["percentile"] = {"method": PERCENTILE_METHOD, "definition": PERCENTILE_DEFINITION}
        print("LOAD ENVIRONMENT", flush=True)
        for key in ("os", "cpu_count", "cpu_brand", "ram_bytes", "python", "docker_version", "llm"):
            print(f"  {key}: {environment.get(key)}", flush=True)
        print(f"  postgresql: {environment['postgresql']['postgresql_version']}", flush=True)
        print(f"  kafka: {environment['kafka']}", flush=True)
        print(f"  workers: in-process, no cgroup limit", flush=True)
        matrix = []
        if include_matrix:
            for level in levels:
                print(f"LEVEL workers=1 concurrency={level} requests={requests}", flush=True)
                matrix.append(
                    run_level(
                        session_factory,
                        settings,
                        admin,
                        requests=requests,
                        concurrency=level,
                        workers=1,
                        label=f"c{level}",
                        engine=engine,
                    )
                )
            if 100 not in levels and matrix and matrix[-1].get("concurrency") == 50:
                last = matrix[-1]
                previous = matrix[-2] if len(matrix) > 1 else {}
                last_p95 = (last.get("end_to_end_s") or {}).get("p95") or 0
                prev_p95 = (previous.get("end_to_end_s") or {}).get("p95") or 0
                healthy = (
                    last.get("failed") == 0
                    and last.get("incomplete_ids") == 0
                    and last.get("rejected_submissions") == 0
                    and not last.get("worker_errors")
                )
                jumped = bool(prev_p95) and last_p95 >= prev_p95 * 3
                if healthy and not jumped:
                    print(
                        f"LEVEL workers=1 concurrency=100 requests={requests} "
                        "(concurrency 50 had no failures and p95 did not triple)",
                        flush=True,
                    )
                    matrix.append(
                        run_level(
                            session_factory,
                            settings,
                            admin,
                            requests=requests,
                            concurrency=100,
                            workers=1,
                            label="c100",
                            engine=engine,
                        )
                    )
                else:
                    print(
                        "SKIP concurrency 100 "
                        f"(healthy={healthy}, p95_tripled={jumped})",
                        flush=True,
                    )
        scaling = []
        for count in list(scaling_workers or []):
            print(f"SCALE workers={count} concurrency={scaling_concurrency} requests={requests}", flush=True)
            scaling.append(
                run_level(
                    session_factory,
                    settings,
                    admin,
                    requests=requests,
                    concurrency=scaling_concurrency,
                    workers=count,
                    label=f"w{count}",
                    engine=engine,
                )
            )
        scenarios: Dict[str, Any] = {}
        if include_scenarios:
            print("SCENARIO idempotency", flush=True)
            scenarios["idempotency"] = scenario_idempotency(session_factory, settings, admin)
            print("SCENARIO key contention", flush=True)
            scenarios["key_contention"] = scenario_same_and_different_keys(session_factory, settings)
            print("SCENARIO replay", flush=True)
            scenarios["replay"] = scenario_replay(session_factory, settings)
            print("SCENARIO crash", flush=True)
            scenarios["crash"] = scenario_crash(session_factory, settings)
            print("SCENARIO outbox backlog", flush=True)
            scenarios["outbox_backlog"] = scenario_outbox_backlog(session_factory, settings)
            print("SCENARIO queue limit", flush=True)
            scenarios["queue_limit"] = scenario_queue_limit(session_factory, settings)
            print("SCENARIO publish failure", flush=True)
            scenarios["publish_failure"] = scenario_publish_failure(session_factory, settings)
            print("SCENARIO database rollback", flush=True)
            scenarios["database_rollback"] = scenario_db_rollback(session_factory, settings)
            print("SCENARIO failure 5%", flush=True)
            scenarios["failure_5_percent"] = scenario_failures(session_factory, settings, admin, percent=5)
            print("SCENARIO failure 10%", flush=True)
            scenarios["failure_10_percent"] = scenario_failures(session_factory, settings, admin, percent=10)
            print("SCENARIO partial agent", flush=True)
            scenarios["partial_agent"] = run_level(
                session_factory,
                settings,
                admin,
                requests=10,
                concurrency=2,
                workers=1,
                label="partial",
                llm=_llm(fail_agents={"news"}),
                weights={"news": 1},
                engine=engine,
                timeout_s=120,
            )
            print("SCENARIO conflict", flush=True)
            scenarios["conflict"] = run_level(
                session_factory,
                settings,
                admin,
                requests=10,
                concurrency=2,
                workers=1,
                label="conflict",
                llm=_llm(scenario="conflicting"),
                weights={"multi": 1},
                engine=engine,
                timeout_s=120,
            )
        report = {
            "label": "DEVELOPMENT LOAD TEST / FAKELLM / FIXTURE-BASED",
            "capacity_claim": False,
            "disclaimer": (
                "These results characterize the tested development environment "
                "and are not production capacity guarantees."
            ),
            "environment": environment,
            "matrix": matrix,
            "scaling": scaling,
            "scenarios": scenarios,
            "cardinality": _cardinality(),
            "dominant_stage_by_level": [
                {"label": level["label"], "stage": _dominant_stage(level)} for level in matrix
            ],
            "latency_jumps": _latency_jumps(matrix),
            "integrity_before": integrity_before,
        }
    finally:
        logging.disable(previous_disable)
        restore()
        if admin is not None:
            try:
                delete_topics(admin, settings)
            except Exception as exc:
                report["topic_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        engine.dispose()
        try:
            recreate_dropped = True
            admin_engine = create_engine(_admin_url(database_url), isolation_level="AUTOCOMMIT")
            with admin_engine.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": LOAD_DATABASE},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{LOAD_DATABASE}"'))
            admin_engine.dispose()
            report["load_database_dropped"] = recreate_dropped
        except Exception as exc:
            report["load_database_dropped"] = False
            report["load_database_error"] = f"{type(exc).__name__}: {exc}"
    report["integrity_after"] = developer_integrity()
    logging.disable(previous_disable)
    return report


def default_database_url() -> str:
    return f"postgresql://quantpilot:quantpilot@localhost:{DISPOSABLE_PORT}/{LOAD_DATABASE}"
