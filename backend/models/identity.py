"""Users and fixed application roles."""

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models.base import ID_TYPE, TimestampMixin


class Role(db.Model):
    __tablename__ = "roles"
    __table_args__ = (
        db.CheckConstraint("name IN ('owner', 'staff')", name="ck_roles_name"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    name = db.Column(db.String(30), nullable=False, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    users = db.relationship("User", back_populates="role")


class User(db.Model, TimestampMixin):
    __tablename__ = "users"

    id = db.Column(ID_TYPE, primary_key=True)
    role_id = db.Column(ID_TYPE, db.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    username = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    full_name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    last_login_at = db.Column(db.DateTime(timezone=True))

    role = db.relationship("Role", back_populates="users")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role.name,
        }
