"""JWT authentication and owner-only staff administration."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.identity import Role, User

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _current_user() -> User | None:
    identity = get_jwt_identity()
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None
    user = db.session.get(User, user_id)
    return user if user and user.is_active else None


def roles_required(*allowed_roles: str):
    def decorator(view):
        @wraps(view)
        @jwt_required()
        def wrapped(*args, **kwargs):
            user = _current_user()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if user.role.name not in allowed_roles:
                return jsonify({"error": "You do not have permission to access this resource"}), 403
            g.current_user = user
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _json_body() -> dict:
    if not request.is_json:
        return {}
    payload = request.get_json()
    return payload if isinstance(payload, dict) else {}


@auth_bp.post("/login")
def login():
    payload = _json_body()
    email = payload.get("email")
    password = payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email.strip() or not password:
        return jsonify({"error": "Email and password are required"}), 400

    normalized_email = email.strip().lower()
    user = User.query.filter(func.lower(User.email) == normalized_email).first()
    if not user or not user.is_active or not user.check_password(password):
        return jsonify({"error": "Invalid email or password"}), 401

    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role.name},
    )
    return jsonify({"access_token": token, "user": user.to_public_dict()})


@auth_bp.post("/register")
def register_public_user():
    payload = _json_body()
    full_name = payload.get("full_name")
    username = payload.get("username")
    email = payload.get("email")
    password = payload.get("password")

    if not all(isinstance(value, str) for value in (full_name, username, email, password)):
        return jsonify({"error": "full_name, username, email, and password are required"}), 400

    full_name = full_name.strip()
    username = username.strip().lower()
    email = email.strip().lower()
    if not full_name or not username or not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"error": "Provide a full name, username, and valid email"}), 400
    if len(full_name) > 160 or len(username) > 80 or len(email) > 254:
        return jsonify({"error": "A supplied field exceeds its maximum length"}), 400
    if len(password) < 12:
        return jsonify({"error": "Password must be at least 12 characters"}), 400

    existing = User.query.filter(
        (func.lower(User.email) == email) | (func.lower(User.username) == username)
    ).first()
    if existing:
        return jsonify({"error": "Email or username is already in use"}), 409

    staff_role = Role.query.filter_by(name="staff").first()
    if staff_role is None:
        current_app.logger.error("Required staff role is missing; apply database migrations")
        return jsonify({"error": "User roles are not initialized"}), 503

    user = User(
        role=staff_role,
        username=username,
        email=email,
        full_name=full_name,
    )
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Email or username is already in use"}), 409
    return jsonify({"message": "Registration successful", "user": user.to_public_dict()}), 201


@auth_bp.get("/me")
@jwt_required()
def me():
    user = _current_user()
    if user is None:
        return jsonify({"error": "Account is inactive or no longer exists"}), 401
    return jsonify({"user": user.to_public_dict()})


@auth_bp.post("/users")
@roles_required("owner")
def create_staff_user():
    payload = _json_body()
    full_name = payload.get("full_name")
    username = payload.get("username")
    email = payload.get("email")
    password = payload.get("password")

    if not all(isinstance(value, str) for value in (full_name, username, email, password)):
        return jsonify({"error": "full_name, username, email, and password are required"}), 400

    full_name = full_name.strip()
    username = username.strip().lower()
    email = email.strip().lower()
    if not full_name or not username or not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"error": "Provide a full name, username, and valid email"}), 400
    if len(full_name) > 160 or len(username) > 80 or len(email) > 254:
        return jsonify({"error": "A supplied field exceeds its maximum length"}), 400
    if len(password) < 12:
        return jsonify({"error": "Password must be at least 12 characters"}), 400

    existing = User.query.filter(
        (func.lower(User.email) == email) | (func.lower(User.username) == username.lower())
    ).first()
    if existing:
        return jsonify({"error": "Email or username is already in use"}), 409

    staff_role = Role.query.filter_by(name="staff").first()
    if staff_role is None:
        current_app.logger.error("Required staff role is missing; apply database migrations")
        return jsonify({"error": "User roles are not initialized"}), 503

    user = User(
        role=staff_role,
        username=username,
        email=email,
        full_name=full_name,
    )
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Email or username is already in use"}), 409
    return jsonify({"user": user.to_public_dict()}), 201
