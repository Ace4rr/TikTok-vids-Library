"""Папки и теги — FR-3.2/3.3: CRUD, переименование, слияние тегов."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from toklib_core import search as search_mod
from toklib_core.db import Folder, Tag, Video, VideoFolder, VideoTag
from toklib_core.enums import TagKind
from toklib_core.schemas import (
    FolderCreateRequest,
    FolderOut,
    FolderPatchRequest,
    TagCreateRequest,
    TagMergeRequest,
    TagOut,
)

from ..deps import CtxDep, SessionDep, require_auth

router = APIRouter(tags=["taxonomy"], dependencies=[Depends(require_auth)])


def _slug(name: str) -> str:
    slug = re.sub(r"\s+", "-", name.strip().lower())
    return re.sub(r"[^\wа-яё-]", "", slug, flags=re.UNICODE)[:128]


# ---------- Folders ----------


@router.get("/api/folders", response_model=list[FolderOut])
def list_folders(session: SessionDep) -> list[FolderOut]:
    rows = session.query(Folder).order_by(Folder.parent_id.is_(True), Folder.name).all()
    return [FolderOut.model_validate(f) for f in rows]


@router.post("/api/folders", response_model=FolderOut, status_code=201)
def create_folder(body: FolderCreateRequest, session: SessionDep) -> FolderOut:
    if not body.name.strip():
        raise HTTPException(422, "пустое имя папки")
    if body.parent_id is not None:
        parent = session.get(Folder, body.parent_id)
        if parent is None:
            raise HTTPException(404, "родительская папка не найдена")
        if parent.parent_id is not None:
            raise HTTPException(422, "вложенность ≤ 2 уровней (FR-3.2)")
    folder = Folder(name=body.name.strip(), parent_id=body.parent_id, icon=body.icon, slug=_slug(body.name))
    session.add(folder)
    session.commit()
    return FolderOut.model_validate(folder)


@router.patch("/api/folders/{folder_id}", response_model=FolderOut)
def patch_folder(folder_id: int, body: FolderPatchRequest, session: SessionDep) -> FolderOut:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(404, "папка не найдена")
    if body.name is not None and body.name.strip():
        folder.name = body.name.strip()
        folder.slug = _slug(folder.name)  # Р5: переименование не двигает файлы
    if body.icon is not None:
        folder.icon = body.icon
    if body.parent_id is not None:
        if body.parent_id == folder.id:
            raise HTTPException(422, "папка не может быть родителем самой себя")
        parent = session.get(Folder, body.parent_id)
        if parent is None:
            raise HTTPException(404, "родительская папка не найдена")
        if parent.parent_id is not None:
            raise HTTPException(422, "вложенность ≤ 2 уровней (FR-3.2)")
        folder.parent_id = body.parent_id
    session.commit()
    return FolderOut.model_validate(folder)


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: int, session: SessionDep) -> dict:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(404, "папка не найдена")
    session.query(VideoFolder).filter(VideoFolder.folder_id == folder_id).delete()
    session.delete(folder)
    session.commit()
    return {"deleted": True}


# ---------- Tags ----------


@router.get("/api/tags", response_model=list[TagOut])
def list_tags(session: SessionDep) -> list[TagOut]:
    rows = (
        session.query(Tag, sa_func.count(VideoTag.video_id).label("cnt"))
        .outerjoin(VideoTag, VideoTag.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(sa_func.count(VideoTag.video_id).desc(), Tag.name)
        .all()
    )
    return [TagOut(id=tag.id, name=tag.name, kind=str(tag.kind.value), video_count=int(cnt)) for tag, cnt in rows]


@router.post("/api/tags", response_model=TagOut, status_code=201)
def create_tag(body: TagCreateRequest, session: SessionDep) -> TagOut:
    name = body.name.strip().lstrip("#")
    if not name:
        raise HTTPException(422, "пустое имя тега")
    existing = session.query(Tag).filter(Tag.name == name).one_or_none()
    if existing:
        return TagOut(id=existing.id, name=existing.name, kind=str(existing.kind.value))
    kind = TagKind.USER
    if body.kind in (TagKind.AI.value, TagKind.SYSTEM.value):
        kind = TagKind(body.kind)
    tag = Tag(name=name, kind=kind)
    session.add(tag)
    session.commit()
    return TagOut(id=tag.id, name=tag.name, kind=str(tag.kind.value))


@router.patch("/api/tags/{tag_id}", response_model=TagOut)
def rename_tag(tag_id: int, body: TagCreateRequest, session: SessionDep, ctx: CtxDep) -> TagOut:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "тег не найден")
    name = body.name.strip().lstrip("#")
    if not name:
        raise HTTPException(422, "пустое имя тега")
    tag.name = name
    session.commit()
    for link in session.query(VideoTag).filter(VideoTag.tag_id == tag_id).all():
        video = session.get(Video, link.video_id)
        if video is not None:
            search_mod.index_video_orm(ctx.engine, video)
    return TagOut(id=tag.id, name=tag.name, kind=str(tag.kind.value))


@router.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, session: SessionDep) -> dict:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "тег не найден")
    session.delete(tag)
    session.commit()
    return {"deleted": True}


@router.post("/api/tags/merge")
def merge_tags(body: TagMergeRequest, session: SessionDep, ctx: CtxDep) -> dict:
    """Слияние тегов (FR-3.3): связи from переводятся на to, from удаляется."""
    src = session.get(Tag, body.from_id)
    dst = session.get(Tag, body.to_id)
    if src is None or dst is None:
        raise HTTPException(404, "тег не найден")
    if src.id == dst.id:
        raise HTTPException(422, "нельзя слить тег с самим собой")
    moved = 0
    for link in session.query(VideoTag).filter(VideoTag.tag_id == src.id).all():
        exists = (
            session.query(VideoTag)
            .filter(VideoTag.video_id == link.video_id, VideoTag.tag_id == dst.id)
            .one_or_none()
        )
        if exists is None:
            link.tag_id = dst.id
            moved += 1
        else:
            session.delete(link)
    session.delete(src)
    session.commit()
    for link in session.query(VideoTag).filter(VideoTag.tag_id == dst.id).all():
        video = session.get(Video, link.video_id)
        if video is not None:
            search_mod.index_video_orm(ctx.engine, video)
    return {"merged": moved, "into": dst.id}
