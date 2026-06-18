#!/usr/bin/env python3
"""
Seed the initial superadmin account.
Only runs interactively; prompts for username and password.
Usage: docker compose exec backend python scripts/seed_superadmin.py
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys

# Ensure /app is on sys.path when run from scripts/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import AsyncSessionLocal


async def seed_superadmin():
    settings = get_settings()

    print("=" * 60)
    print("  NOD — Superadmin Account Seeding")
    print("=" * 60)
    print()

    # Check if superadmin already exists
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.role == "superadmin")
        )
        existing = result.scalars().all()
        if existing:
            usernames = [u.username for u in existing]
            print(f"⚠️  Superadmin account(s) already exist: {', '.join(usernames)}")
            choice = input("Create another superadmin? (y/N): ").strip().lower()
            if choice != "y":
                print("Aborted.")
                return

        # Prompt for credentials
        print()
        username = input("Superadmin username: ").strip()
        if not username:
            print("ERROR: Username cannot be empty.")
            sys.exit(1)

        # Check username uniqueness
        result = await session.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            print(f"ERROR: Username '{username}' already exists.")
            sys.exit(1)

        email = input("Superadmin email: ").strip()
        if not email:
            print("ERROR: Email cannot be empty.")
            sys.exit(1)

        password = getpass.getpass("Superadmin password (min 8 chars): ")
        if len(password) < 8:
            print("ERROR: Password must be at least 8 characters.")
            sys.exit(1)

        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            print("ERROR: Passwords do not match.")
            sys.exit(1)

        full_name = input("Full name (optional): ").strip()

        # Create superadmin
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role="superadmin",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        await session.commit()

        print()
        print("✅ Superadmin account created successfully!")
        print(f"   Username: {username}")
        print(f"   Role: superadmin")
        print()


if __name__ == "__main__":

    # Non-interactive mode via env vars (for CI/CD, VM without TTY)
    env_username = os.getenv("NOD_SUPERADMIN_USER")
    env_password = os.getenv("NOD_SUPERADMIN_PASS")
    env_email = os.getenv("NOD_SUPERADMIN_EMAIL", "admin@nod.local")

    if env_username and env_password:
        print(f"Non-interactive mode: creating superadmin '{env_username}'")

        async def noninteractive_seed():
            from app.core.security import hash_password
            from app.db.models import User
            from app.db.session import AsyncSessionLocal
            from sqlalchemy import select

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User).where(User.role == "superadmin")
                )
                if result.scalars().first():
                    print("Superadmin already exists — skipping.")
                    return

                result = await session.execute(
                    select(User).where(User.username == env_username)
                )
                if result.scalar_one_or_none():
                    print(f"Username '{env_username}' already exists — skipping.")
                    return

                user = User(
                    username=env_username,
                    email=env_email,
                    hashed_password=hash_password(env_password),
                    full_name=env_username,
                    role="superadmin",
                    is_active=True,
                    must_change_password=False,
                )
                session.add(user)
                await session.commit()
                print(f"Superadmin '{env_username}' created successfully.")

        asyncio.run(noninteractive_seed())
    else:
        # Interactive mode (original behavior)
        asyncio.run(seed_superadmin())
