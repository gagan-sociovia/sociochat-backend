# models.py - Core models for SocioChat
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    business_name = db.Column(db.String(255))
    industry = db.Column(db.String(120))
    password_hash = db.Column(db.String(256), nullable=False)
    email_verified = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(32), default="approved")  # Simplified: auto-approve
    # Role-based access control
    role = db.Column(db.String(32), nullable=False, default="user", index=True)

    # Subscriptions
    plan = db.Column(db.String(32), nullable=False, default="beta", index=True)
    subscription_expires_at = db.Column(db.DateTime, nullable=True)
    beta_expires_at = db.Column(db.DateTime, nullable=True)

    # Verification Flow
    verification_code_hash = db.Column(db.String(256))
    verification_expires_at = db.Column(db.DateTime)

    # Phone OTP Flow
    phone_otp_hash = db.Column(db.String(512))
    phone_otp_expires_at = db.Column(db.DateTime)
    phone_last_sent_at = db.Column(db.DateTime)
    phone_verified = db.Column(db.Boolean, default=False)

    rejection_reason = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))


class Workspace(db.Model):
    __tablename__ = "workspaces2"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    business_name = db.Column(db.String(255), nullable=True)
    business_type = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    website = db.Column(db.String(255), nullable=True)
    industry = db.Column(db.String(255), nullable=True)
    logo_path = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    owner = db.relationship("User", backref="workspaces2")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(255))
    action = db.Column(db.String(64))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    meta = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<AuditLog {self.action} by {self.actor}>'
