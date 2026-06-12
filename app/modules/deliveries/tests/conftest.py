"""Re-seed IAM reference rows + per-test POD storage tempdir."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _seed_iam_and_pod_dir(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[None]:
    from app.core.config import get_settings
    from app.core.db.session import get_sessionmaker
    from app.modules.iam.permissions import ALL_PERMISSIONS, ALL_ROLES

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        for perm in (*ALL_PERMISSIONS, "*"):
            await s.execute(
                text(
                    "INSERT INTO permissions (name) VALUES (:n) "
                    "ON CONFLICT (name) DO NOTHING",
                ),
                {"n": perm},
            )
        for role_spec in ALL_ROLES:
            await s.execute(
                text(
                    """
                    INSERT INTO roles (name, description, is_system)
                    VALUES (:n, :d, :s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        is_system = EXCLUDED.is_system
                    """,
                ),
                {
                    "n": role_spec.name,
                    "d": role_spec.description,
                    "s": role_spec.is_system,
                },
            )
            role_id = (
                await s.execute(
                    text("SELECT id FROM roles WHERE name = :n"),
                    {"n": role_spec.name},
                )
            ).scalar_one()
            for perm_name in role_spec.permissions:
                perm_id = (
                    await s.execute(
                        text("SELECT id FROM permissions WHERE name = :n"),
                        {"n": perm_name},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "VALUES (:r, :p) ON CONFLICT DO NOTHING",
                    ),
                    {"r": role_id, "p": perm_id},
                )

    pdir = tmp_path_factory.mktemp("delivery_pod")
    old = os.environ.get("DELIVERY_POD_DIR")
    os.environ["DELIVERY_POD_DIR"] = str(pdir)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DELIVERY_POD_DIR", None)
        else:
            os.environ["DELIVERY_POD_DIR"] = old
        get_settings.cache_clear()  # type: ignore[attr-defined]


_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000302020302020303030304030304050805"
    "0504040509070706080a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a"
    "0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0aff"
    "c00011080001000103012200021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"
    "ffc4001f0100030101010101010101010000000000000102030405060708090a0b"
    "ffc400b51100020102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"
    "ffda000c03010002110311003f00fbd2bf00ffd9"
)


@pytest.fixture
def tiny_jpeg() -> bytes:
    return _TINY_JPEG
