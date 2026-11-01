"""The video library. Uploads, thumbnails and links, but no route that returns a video's bytes: see services.video_assets."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.db import get_db
from app.models import Campaign, EmailImage, User, VideoAsset
from app.schemas.common import Message, ORMModel
from app.services import video_assets as svc

router = APIRouter(prefix="/api/video-assets", tags=["video"])
# under /u/ like unsubscribe links: public, and already routed to the API by the website
public_router = APIRouter(prefix="/u/thumb", tags=["video"], include_in_schema=False)

# the bytes behind a link never change, so mail proxies may keep them
PUBLIC_IMAGE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'", "Cross-Origin-Resource-Policy": "cross-origin",
                        "X-Robots-Tag": "noindex"}


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(ge=0)


class AssetPatch(BaseModel):
    filename: str | None = Field(default=None, max_length=500)
    link_url: str | None = None


class AssetOut(ORMModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    status: str
    link_url: str | None
    has_thumbnail: bool = False
    thumbnail_url: str | None = None
    warnings: list[str] = []
    created_at: datetime


class ThumbnailOut(BaseModel):
    id: uuid.UUID
    filename: str
    width: int | None
    height: int | None
    size_bytes: int
    url: str


def _out(db: Session, asset: VideoAsset, mailboxes=()) -> AssetOut:
    o = AssetOut.model_validate(asset)
    image = asset.thumbnail_image
    o.has_thumbnail = image is not None
    o.thumbnail_url = svc.image_path(image) if image else None
    o.warnings = svc.warnings(db, asset, list(mailboxes))
    return o


def _thumbnail_out(image: EmailImage) -> ThumbnailOut:
    return ThumbnailOut(id=image.id, filename=image.filename, width=image.width, height=image.height,
                        size_bytes=image.size_bytes, url=svc.image_path(image))


def _get(db: Session, asset_id: uuid.UUID) -> VideoAsset:
    asset = db.get(VideoAsset, asset_id)
    if asset is None:
        raise HTTPException(404, "Video not found")
    return asset


def _error(exc: svc.VideoError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _read_body(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > limit:
            raise HTTPException(413, "This upload is too large")
    return bytes(data)


@router.get("")
def list_assets(campaign_id: uuid.UUID | None = None, _: User = Depends(reader), db: Session = Depends(get_db)):
    """Ready videos, plus the limits. With ``campaign_id`` the warnings also cover that campaign's mailboxes."""
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    boxes = campaign.mailboxes if campaign else ()
    assets = db.scalars(select(VideoAsset).where(VideoAsset.status == "ready").order_by(VideoAsset.created_at.desc()))
    return {"items": [_out(db, a, boxes) for a in assets], "limits": svc.limits(db)}


@router.post("", status_code=201)
def start_upload(body: UploadIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    try:
        asset = svc.create_upload(db, body.filename, body.size_bytes, user.id)
    except svc.VideoError as exc:
        raise _error(exc) from exc
    audit(db, user, "video.upload_started", "video_asset", asset.id, request, filename=asset.filename, size=asset.size_bytes)
    db.commit()
    return {"id": str(asset.id), "chunk_bytes": svc.CHUNK_BYTES, "total_chunks": svc.total_chunks(asset)}


@router.put("/{asset_id}/chunks/{index}", response_model=Message)
async def upload_chunk(asset_id: uuid.UUID, index: int, request: Request, _: User = Depends(writer),
                       db: Session = Depends(get_db)):
    asset = _get(db, asset_id)
    data = await _read_body(request, svc.CHUNK_BYTES)
    try:
        svc.store_chunk(db, asset, index, data)
    except svc.VideoError as exc:
        raise _error(exc) from exc
    db.commit()
    return Message(message="Stored")


@router.post("/{asset_id}/complete")
def complete_upload(asset_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    try:
        asset, duplicate = svc.complete_upload(db, _get(db, asset_id))
    except svc.VideoError as exc:
        raise _error(exc) from exc
    audit(db, user, "video.uploaded", "video_asset", asset.id, request, filename=asset.filename, duplicate=duplicate)
    db.commit()
    return {"asset": _out(db, asset), "duplicate": duplicate}


@router.put("/{asset_id}/thumbnail", response_model=AssetOut)
async def put_thumbnail(asset_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    """The thumbnail captured from the video in the browser when it is uploaded."""
    asset = _get(db, asset_id)
    data = await _read_body(request, svc.MAX_THUMBNAIL_BYTES)
    try:
        svc.set_thumbnail(db, asset, data, user.id)
    except svc.VideoError as exc:
        raise _error(exc) from exc
    db.commit()
    return _out(db, asset)


@router.post("/thumbnails", response_model=ThumbnailOut, status_code=201)
async def upload_custom_thumbnail(request: Request, filename: str | None = None, play_button: bool = True,
                                  user: User = Depends(writer), db: Session = Depends(get_db)):
    """A custom thumbnail for one campaign email. The step points at the result when the sequence is saved."""
    data = await _read_body(request, svc.MAX_IMAGE_BYTES)
    try:
        image = svc.store_image(db, data, filename, user.id, widescreen=True, play_button=play_button)
    except svc.VideoError as exc:
        raise _error(exc) from exc
    audit(db, user, "video.thumbnail_uploaded", "email_image", image.id, request, filename=image.filename)
    db.commit()
    return _thumbnail_out(image)


@router.get("/thumbnails/{image_id}", response_model=ThumbnailOut)
def get_custom_thumbnail(image_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    image = db.get(EmailImage, image_id)
    if image is None:
        raise HTTPException(404, "Thumbnail not found")
    return _thumbnail_out(image)


@public_router.get("/{token}")
def public_thumbnail(token: str, db: Session = Depends(get_db)):
    """What recipients' mail clients load. The link is signed, so only images already placed in an email can be
    reached this way, and only images: there is no equivalent for a video's bytes."""
    image = svc.image_for_token(db, token)
    if image is None:
        raise HTTPException(404, "Not found")
    return Response(image.data, media_type=image.content_type, headers=PUBLIC_IMAGE_HEADERS)


@router.patch("/{asset_id}", response_model=AssetOut)
def patch_asset(asset_id: uuid.UUID, body: AssetPatch, request: Request, user: User = Depends(writer),
                db: Session = Depends(get_db)):
    asset = _get(db, asset_id)
    data = body.model_dump(exclude_unset=True)
    try:
        if data.get("filename") is not None:
            asset.filename = svc.clean_filename(data["filename"])
        if "link_url" in data:
            asset.link_url = svc.clean_link(data["link_url"])
    except svc.VideoError as exc:
        raise _error(exc) from exc
    audit(db, user, "video.updated", "video_asset", asset.id, request, fields=sorted(data))
    db.commit()
    return _out(db, asset)


@router.delete("/{asset_id}", response_model=Message)
def delete_asset(asset_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    asset = _get(db, asset_id)
    name = asset.filename
    try:
        svc.delete_asset(db, asset)
    except svc.VideoError as exc:
        raise _error(exc) from exc
    audit(db, user, "video.deleted", "video_asset", asset_id, request, filename=name)
    db.commit()
    return Message(message="Video deleted")
