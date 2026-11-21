"""SQLModel mappings for the task workflow on the existing SQLite database."""
from __future__ import annotations

from pathlib import Path

from sqlmodel import Field, SQLModel, Session

from tasks import DEFAULT_DB, _engine


class Task(SQLModel, table=True):
    __tablename__ = 'tasks'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = 1
    message_id: str
    source_provider: str = 'demo'
    thread_id: str = ''
    title: str
    description: str = ''
    owner: str = ''
    due_date: str | None = None
    due_time: str | None = None
    due_timezone: str = 'Pacific/Saipan'
    priority: str = 'medium'
    status: str = 'new'
    project_label: str = ''
    customer_label: str = ''
    source_url: str | None = None
    review_state: str = 'reviewed'
    revision: int = 1
    assigned_agent: str = ''
    agent_status: str = 'unassigned'
    agentsteers_job_id: str = ''
    agentsteers_result: str = ''
    created_at: str
    updated_at: str


class TaskAttachment(SQLModel, table=True):
    __tablename__ = 'task_attachments'
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key='tasks.id')
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes | None = None
    skipped_reason: str = ''


class TaskImageReview(SQLModel, table=True):
    __tablename__ = 'task_image_reviews'
    task_id: int = Field(primary_key=True, foreign_key='tasks.id')
    user_id: int
    result_json: str
    created_at: str


def engine(path: str | Path = DEFAULT_DB):
    return _engine(str(path))


def session(path: str | Path = DEFAULT_DB) -> Session:
    return Session(engine(path))
