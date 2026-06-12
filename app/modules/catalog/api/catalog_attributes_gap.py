"""Write/CRUD endpoints for the catalog attribute catalog (AdminCatalogClient
Attributes + Categories tabs). Backs the buttons the list-only stub left dead:

    POST   /catalog/attributes                      create (+ nested options)
    PATCH  /catalog/attributes/{attribute_id}        update
    POST   /catalog/attributes/{attribute_id}/options add an option
    GET    /catalog/categories/{category_id}/attributes  list links for a category
    POST   /catalog/category-attributes              link attribute -> category
    PATCH  /catalog/category-attributes/{link_id}     update a link
    DELETE /catalog/category-attributes/{link_id}     remove a link

Tables created by scripts/seed_catalog_attributes.sql (also auto-created here on
first write, so the endpoints never 500 on a fresh DB). text() SQL only.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.security.rbac import requires_permission

router = APIRouter(tags=["admin-catalog-attributes-gap"])
_PERM = "catalog.product.write"

# One statement per element — asyncpg rejects multi-statement execute().
_DDL = (
    "CREATE TABLE IF NOT EXISTS attribute_definitions ("
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug text UNIQUE NOT NULL, "
    "name text NOT NULL, description text, data_type text NOT NULL DEFAULT 'STRING', "
    "is_active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), "
    "updated_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS attribute_options ("
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    "attribute_id uuid NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE, "
    "value_code text NOT NULL, display_label text NOT NULL, position int NOT NULL DEFAULT 0, "
    "is_active boolean NOT NULL DEFAULT true, UNIQUE (attribute_id, value_code))",
    "CREATE TABLE IF NOT EXISTS category_attributes ("
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), category_id uuid NOT NULL, "
    "attribute_id uuid NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE, "
    "is_required boolean NOT NULL DEFAULT false, is_variant_axis boolean NOT NULL DEFAULT false, "
    "inherit_to_descendants boolean NOT NULL DEFAULT true, "
    "created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (category_id, attribute_id))",
)

_VALID_TYPES = {"STRING", "ENUM", "INTEGER", "DECIMAL", "BOOLEAN"}


class OptionIn(BaseModel):
    value_code: str
    display_label: str
    position: int | None = None
    is_active: bool | None = None


class AttributeCreateIn(BaseModel):
    slug: str
    name: str
    description: str | None = None
    data_type: str = "STRING"
    options: list[OptionIn] | None = None


class AttributeUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    data_type: str | None = None
    is_active: bool | None = None


class CategoryAttributeLinkIn(BaseModel):
    # category_id comes from the URL path (FE posts to
    # /catalog/categories/{id}/attributes); kept optional for back-compat.
    attribute_id: str
    category_id: str | None = None
    is_required: bool = False
    is_variant_axis: bool = False
    inherit_to_descendants: bool = True


class CategoryAttributeUpdateIn(BaseModel):
    is_required: bool | None = None
    is_variant_axis: bool | None = None
    inherit_to_descendants: bool | None = None


async def _ensure(session) -> None:
    for stmt in _DDL:
        await session.execute(text(stmt))


def _opt_wire(o: dict) -> dict[str, Any]:
    return {
        "id": o["id"], "attribute_id": o["attribute_id"],
        "value_code": o["value_code"], "display_label": o["display_label"],
        "position": o["position"], "is_active": bool(o["is_active"]),
    }


async def _attr_wire(session, attr_id: str) -> dict[str, Any]:
    a = (
        await session.execute(
            text(
                "SELECT id::text AS id, slug, name, description, data_type, is_active, "
                "created_at, updated_at FROM attribute_definitions WHERE id=:id"
            ),
            {"id": attr_id},
        )
    ).mappings().first()
    if not a:
        raise ValidationError("Attribute not found.")
    opts = (
        await session.execute(
            text(
                "SELECT id::text AS id, attribute_id::text AS attribute_id, value_code, "
                "display_label, position, is_active FROM attribute_options "
                "WHERE attribute_id=:id ORDER BY position, display_label"
            ),
            {"id": attr_id},
        )
    ).mappings().all()
    return {
        "id": a["id"], "slug": a["slug"], "name": a["name"],
        "description": a["description"], "data_type": a["data_type"] or "STRING",
        "is_active": bool(a["is_active"]),
        "created_at": a["created_at"].isoformat() if a["created_at"] else None,
        "updated_at": a["updated_at"].isoformat() if a["updated_at"] else None,
        "options": [_opt_wire(dict(o)) for o in opts],
    }


@router.post(
    "/catalog/attributes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def create_attribute(
    body: AttributeCreateIn, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    dt = (body.data_type or "STRING").upper()
    if dt not in _VALID_TYPES:
        raise ValidationError(f"Invalid data_type: {body.data_type}", details={"allowed": sorted(_VALID_TYPES)})
    async with uow.transactional() as session:
        await _ensure(session)
        row = (
            await session.execute(
                text(
                    "INSERT INTO attribute_definitions (slug, name, description, data_type) "
                    "VALUES (:slug,:name,:desc,:dt) "
                    "ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name, "
                    "description=EXCLUDED.description, data_type=EXCLUDED.data_type, "
                    "updated_at=now() RETURNING id::text AS id"
                ),
                {"slug": body.slug, "name": body.name, "desc": body.description, "dt": dt},
            )
        ).mappings().first()
        attr_id = row["id"]
        for i, opt in enumerate(body.options or []):
            await session.execute(
                text(
                    "INSERT INTO attribute_options (attribute_id, value_code, display_label, position, is_active) "
                    "VALUES (:aid,:vc,:dl,:pos,:act) ON CONFLICT (attribute_id, value_code) DO NOTHING"
                ),
                {
                    "aid": attr_id, "vc": opt.value_code, "dl": opt.display_label,
                    "pos": opt.position if opt.position is not None else i + 1,
                    "act": opt.is_active if opt.is_active is not None else True,
                },
            )
        return await _attr_wire(session, attr_id)


@router.patch(
    "/catalog/attributes/{attribute_id}",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def update_attribute(
    attribute_id: str, body: AttributeUpdateIn, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    sets, params = [], {"id": attribute_id}
    if body.name is not None:
        sets.append("name=:name"); params["name"] = body.name
    if body.description is not None:
        sets.append("description=:desc"); params["desc"] = body.description
    if body.data_type is not None:
        dt = body.data_type.upper()
        if dt not in _VALID_TYPES:
            raise ValidationError(f"Invalid data_type: {body.data_type}")
        sets.append("data_type=:dt"); params["dt"] = dt
    if body.is_active is not None:
        sets.append("is_active=:act"); params["act"] = body.is_active
    async with uow.transactional() as session:
        await _ensure(session)
        if sets:
            sets.append("updated_at=now()")
            res = await session.execute(
                text(f"UPDATE attribute_definitions SET {', '.join(sets)} WHERE id=:id RETURNING id"),
                params,
            )
            if res.first() is None:
                raise ValidationError("Attribute not found.")
        return await _attr_wire(session, attribute_id)


@router.post(
    "/catalog/attributes/{attribute_id}/options",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def add_option(
    attribute_id: str, body: OptionIn, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    async with uow.transactional() as session:
        await _ensure(session)
        pos = body.position
        if pos is None:
            pos = ((await session.execute(
                text("SELECT COALESCE(max(position),0)+1 FROM attribute_options WHERE attribute_id=:aid"),
                {"aid": attribute_id},
            )).scalar()) or 1
        row = (
            await session.execute(
                text(
                    "INSERT INTO attribute_options (attribute_id, value_code, display_label, position, is_active) "
                    "VALUES (:aid,:vc,:dl,:pos,:act) "
                    "ON CONFLICT (attribute_id, value_code) DO UPDATE SET "
                    "display_label=EXCLUDED.display_label, position=EXCLUDED.position, "
                    "is_active=EXCLUDED.is_active "
                    "RETURNING id::text AS id, attribute_id::text AS attribute_id, value_code, "
                    "display_label, position, is_active"
                ),
                {"aid": attribute_id, "vc": body.value_code, "dl": body.display_label,
                 "pos": pos, "act": body.is_active if body.is_active is not None else True},
            )
        ).mappings().first()
        return _opt_wire(dict(row))


@router.get(
    "/catalog/categories/{category_id}/attributes",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def list_category_attributes(
    category_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> list[dict[str, Any]]:
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text AS id, category_id::text AS category_id, "
                        "attribute_id::text AS attribute_id, is_required, is_variant_axis, "
                        "inherit_to_descendants, created_at FROM category_attributes "
                        "WHERE category_id=:cid ORDER BY created_at"
                    ),
                    {"cid": category_id},
                )
            ).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "id": r["id"], "category_id": r["category_id"], "attribute_id": r["attribute_id"],
            "is_required": bool(r["is_required"]), "is_variant_axis": bool(r["is_variant_axis"]),
            "inherit_to_descendants": bool(r["inherit_to_descendants"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


def _link_wire(r: dict) -> dict[str, Any]:
    return {
        "id": r["id"], "category_id": r["category_id"], "attribute_id": r["attribute_id"],
        "is_required": bool(r["is_required"]), "is_variant_axis": bool(r["is_variant_axis"]),
        "inherit_to_descendants": bool(r["inherit_to_descendants"]),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


@router.post(
    "/catalog/categories/{category_id}/attributes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def link_category_attribute(
    category_id: str,
    body: CategoryAttributeLinkIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """Link an attribute to a category. ``category_id`` from the URL path
    (matches the FE contract); body carries attribute_id + link flags."""
    cid = category_id or body.category_id
    async with uow.transactional() as session:
        await _ensure(session)
        row = (
            await session.execute(
                text(
                    "INSERT INTO category_attributes (category_id, attribute_id, is_required, "
                    "is_variant_axis, inherit_to_descendants) VALUES (:cid,:aid,:req,:axis,:inh) "
                    "ON CONFLICT (category_id, attribute_id) DO UPDATE SET "
                    "is_required=EXCLUDED.is_required, is_variant_axis=EXCLUDED.is_variant_axis, "
                    "inherit_to_descendants=EXCLUDED.inherit_to_descendants "
                    "RETURNING id::text AS id, category_id::text AS category_id, "
                    "attribute_id::text AS attribute_id, is_required, is_variant_axis, "
                    "inherit_to_descendants, created_at"
                ),
                {"cid": cid, "aid": body.attribute_id, "req": body.is_required,
                 "axis": body.is_variant_axis, "inh": body.inherit_to_descendants},
            )
        ).mappings().first()
        return _link_wire(dict(row))


@router.patch(
    "/catalog/category-attributes/{link_id}",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def update_category_attribute(
    link_id: str, body: CategoryAttributeUpdateIn, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> dict[str, Any]:
    sets, params = [], {"id": link_id}
    if body.is_required is not None:
        sets.append("is_required=:req"); params["req"] = body.is_required
    if body.is_variant_axis is not None:
        sets.append("is_variant_axis=:axis"); params["axis"] = body.is_variant_axis
    if body.inherit_to_descendants is not None:
        sets.append("inherit_to_descendants=:inh"); params["inh"] = body.inherit_to_descendants
    async with uow.transactional() as session:
        await _ensure(session)
        clause = ", ".join(sets) if sets else "is_required=is_required"
        row = (
            await session.execute(
                text(
                    f"UPDATE category_attributes SET {clause} WHERE id=:id "
                    "RETURNING id::text AS id, category_id::text AS category_id, "
                    "attribute_id::text AS attribute_id, is_required, is_variant_axis, "
                    "inherit_to_descendants, created_at"
                ),
                params,
            )
        ).mappings().first()
        if not row:
            raise ValidationError("Link not found.")
        return _link_wire(dict(row))


@router.delete(
    "/catalog/category-attributes/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def unlink_category_attribute(
    link_id: str, uow: Annotated[UnitOfWork, Depends(get_uow)]
) -> Response:
    async with uow.transactional() as session:
        await _ensure(session)
        await session.execute(
            text("DELETE FROM category_attributes WHERE id=:id"), {"id": link_id}
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
