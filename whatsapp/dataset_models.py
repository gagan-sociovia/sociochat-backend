"""
Dataset Models
==============

SQLAlchemy models for the Datasets feature.

Tables:
    - Dataset     → Named collection of rows (contacts, leads, etc.)
    - DatasetRow  → Individual row of data within a dataset
"""

from datetime import datetime
from models import db


class Dataset(db.Model):
    """A named dataset scoped to a workspace."""

    __tablename__ = "datasets"
    __table_args__ = (
        db.UniqueConstraint("workspace_id", "name", name="uq_dataset_workspace_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, nullable=False, index=True)

    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True, default="")

    # Column schema: ordered list of column names, e.g. ["name", "phone", "email"]
    columns = db.Column(db.JSON, nullable=False, default=list)

    # Column mapping: original -> display name, e.g. {"phone_number": "phone"}
    column_mapping = db.Column(db.JSON, nullable=False, default=dict)

    # Source tracking
    source_type = db.Column(
        db.String(64), nullable=False, default="manual"
    )  # manual, csv, google_sheets, crm, sociovia, hubspot, pipedrive
    source_config = db.Column(
        db.JSON, nullable=False, default=dict
    )  # e.g. {"sheet_url": "...", "sheet_name": "Sheet1"} or {"crm_url": "...", "workspace_id": "41"}

    # Sync state
    last_sync_at = db.Column(db.DateTime, nullable=True)
    sync_status = db.Column(db.String(32), nullable=True, default="idle")  # idle, syncing, done, error
    sync_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    rows = db.relationship(
        "DatasetRow", backref="dataset", lazy="dynamic", cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description or "",
            "columns": self.columns or [],
            "column_mapping": self.column_mapping or {},
            "source_type": self.source_type,
            "source_config": self.source_config or {},
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "sync_status": self.sync_status,
            "sync_error": self.sync_error,
            "total_rows": self.rows.count() if self.rows else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DatasetRow(db.Model):
    """A single row of data within a dataset."""

    __tablename__ = "dataset_rows"

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.Integer, db.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Flexible JSONB data: {"name": "John", "phone": "+1234567890", "email": "john@example.com"}
    data = db.Column(db.JSON, nullable=False, default=dict)

    row_order = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self):
        return {
            "id": self.id,
            "dataset_id": self.dataset_id,
            "data": self.data or {},
            "row_order": self.row_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
