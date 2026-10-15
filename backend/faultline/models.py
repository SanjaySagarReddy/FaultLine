from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(80), index=True)
    scenario_version: Mapped[str] = mapped_column(String(20))
    scenario_hash: Mapped[str] = mapped_column(String(64))
    scenario_snapshot: Mapped[dict] = mapped_column(JSON)
    tool_manifest_hash: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str] = mapped_column(String(40))
    agent_config: Mapped[dict] = mapped_column(JSON)
    fault_plan: Mapped[list] = mapped_column(JSON)
    fault_plan_source: Mapped[str] = mapped_column(
        String(20), default="legacy_unknown", server_default="legacy_unknown"
    )
    limits: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    termination_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(60))
    call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    run: Mapped[Run] = relationship(back_populates="events")


class SimCustomer(Base):
    __tablename__ = "sim_customers"
    __table_args__ = (UniqueConstraint("run_id", "fixture_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    fixture_id: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")


class SimPayment(Base):
    __tablename__ = "sim_payments"
    __table_args__ = (UniqueConstraint("run_id", "fixture_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    fixture_id: Mapped[str] = mapped_column(String(80))
    customer_id: Mapped[str] = mapped_column(String(80))
    order_id: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefundProposal(Base):
    __tablename__ = "refund_proposals"
    __table_args__ = (UniqueConstraint("run_id", "proposal_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(String(80))
    payment_id: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))


class RefundAction(Base):
    __tablename__ = "refund_actions"
    __table_args__ = (UniqueConstraint("run_id", "idempotency_key"), UniqueConstraint("run_id", "payment_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    action_id: Mapped[str] = mapped_column(String(80))
    payment_id: Mapped[str] = mapped_column(String(80))
    proposal_id: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))


class Evaluation(Base):
    __tablename__ = "evaluations"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    evaluator_version: Mapped[str] = mapped_column(String(20))
    checks: Mapped[list] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)


def make_engine(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}}
    if url == "sqlite://":
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if url != "sqlite://":
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(url: str):
    return sessionmaker(bind=make_engine(url), expire_on_commit=False)
