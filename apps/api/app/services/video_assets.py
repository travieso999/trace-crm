"""The video library: chunked upload, MP4 validation, size limits and what a sequence step's video needs at send time.

Bytes are kept in Postgres, not on disk: the hosted API has no persistent filesystem, and this way they sit behind the
same access rules as all other data. Nothing here or in the routes returns them to a browser; they only ever leave as
an email attachment.
"""

import base64
import hashlib
import math
import re
import struct
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import EmailAccount, SequenceStep, VideoAsset, VideoAssetChunk
from app.models.base import utcnow
from app.providers import mime
from app.providers.base import Attachment, InlineImage
from app.services import app_settings
from app.services.rendering import VideoLink

VIDEO_TYPE = "video/mp4"
# under Vercel's 4.5 MB request limit
CHUNK_BYTES = 3 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 512 * 1024
ABANDONED_AFTER = timedelta(days=1)

MP4_BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"M4VH", b"M4VP",
              b"dash", b"mmp4"}
NOT_MP4_VIDEO = {b"qt  ", b"M4A ", b"M4B ", b"M4P ", b"heic", b"mif1", b"avif", b"crx "}
VIDEO_TRACK = re.compile(rb"hdlr.{8}vide", re.DOTALL)


class VideoError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# --- limits -----------------------------------------------------------------------------------------------------

def _mb(n: float) -> int:
    return int(n * 1_000_000)


def max_attachment_bytes(db: Session, provider: str) -> int:
    """The configured limit, never above what this provider can carry."""
    configured = _mb(app_settings.get_section(db, "video_attachments")["max_attachment_mb"])
    return min(configured, mime.max_attachment_bytes(provider))


def limits(db: Session) -> dict:
    cfg = app_settings.get_section(db, "video_attachments")
    return {
        "max_upload_bytes": max(max_attachment_bytes(db, p) for p in mime.MESSAGE_LIMITS),
        "warn_bytes": _mb(cfg["warn_attachment_mb"]),
        "library_max_bytes": _mb(cfg["library_max_mb"]),
        "library_used_bytes": db.scalar(select(func.coalesce(func.sum(VideoAsset.size_bytes), 0))) or 0,
        "chunk_bytes": CHUNK_BYTES,
        "providers": {p: {"message_limit_bytes": mime.message_limit(p), "max_attachment_bytes": max_attachment_bytes(db, p)}
                      for p in mime.MESSAGE_LIMITS},
    }


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def size_problem(db: Session, asset: VideoAsset, provider: str) -> str | None:
    limit = max_attachment_bytes(db, provider)
    if asset.size_bytes <= limit:
        return None
    return (f"“{asset.filename}” is {_size(asset.size_bytes)}, over the {_size(limit)} attachment limit for "
            f"{provider} mailboxes")


def warnings(db: Session, asset: VideoAsset, mailboxes: list[EmailAccount]) -> list[str]:
    out = []
    warn_at = _mb(app_settings.get_section(db, "video_attachments")["warn_attachment_mb"])
    if asset.size_bytes > warn_at:
        out.append(f"Large attachment ({_size(asset.size_bytes)}). Many company mail servers refuse messages over about "
                   "10 MB, and large attachments in cold email raise spam risk. A smaller file or the clickable preview "
                   "reaches more people.")
    encoded = mime.encoded_size(asset.size_bytes)
    out.append(f"Encoding makes the file about {_size(encoded)} inside the email, which is what mail providers count.")
    for provider in sorted({m.provider for m in mailboxes}):
        if problem := size_problem(db, asset, provider):
            out.append(problem)
        elif encoded > mime.message_limit(provider) * 0.8:
            out.append(f"This is close to the {_size(mime.message_limit(provider))} {provider} message limit.")
    if any(m.provider == "gmail" for m in mailboxes):
        out.append("Gmail keeps every sent copy with its attachment, and those count against the mailbox's storage.")
    return out


# --- upload -----------------------------------------------------------------------------------------------------

def clean_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r'[\x00-\x1f\x7f"<>:|?*]', "", base).strip()
    stem, dot, ext = base.rpartition(".")
    if not dot or ext.lower() != "mp4" or not stem.strip(" ."):
        raise VideoError("Only .mp4 files can be attached")
    return f"{stem[:200]}.mp4"


def clean_link(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 2000 or re.search(r"[\s\"'<>]", url):
        raise VideoError("The video link must be a full http(s) address")
    return url


def _check_brand(head: bytes) -> None:
    if len(head) < 16 or head[4:8] != b"ftyp":
        raise VideoError("This file is not an MP4 video")
    major = head[8:12]
    size = struct.unpack(">I", head[:4])[0]
    compatible = {head[i:i + 4] for i in range(16, min(size, len(head)) - 3, 4)}
    if major in NOT_MP4_VIDEO or not ({major} | compatible) & MP4_BRANDS:
        raise VideoError("This file is not an MP4 video (MOV and audio-only files are not supported)")


def validate_mp4(data: bytes) -> None:
    """Walk the top-level boxes: an MP4 video has ftyp first, then moov and mdat, and every box fits inside the file.
    This catches renamed files and truncated uploads; the codec inside is not inspected."""
    _check_brand(data[:4096])
    seen, pos = set(), 0
    moov = b""
    while pos < len(data):
        if pos + 8 > len(data):
            raise VideoError("This MP4 file is damaged or incomplete")
        size, kind = struct.unpack(">I", data[pos:pos + 4])[0], data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > len(data):
                raise VideoError("This MP4 file is damaged or incomplete")
            size, header = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = len(data) - pos  # the box runs to the end of the file
        if size < header or pos + size > len(data):
            raise VideoError("This MP4 file is damaged or incomplete")
        seen.add(kind)
        if kind == b"moov":
            moov = data[pos + header:pos + size]
        pos += size
    if b"moov" not in seen or b"mdat" not in seen:
        raise VideoError("This MP4 file is damaged or incomplete")
    if not VIDEO_TRACK.search(moov):
        raise VideoError("This MP4 file has no video track")


def create_upload(db: Session, filename: str | None, size_bytes: int, user_id: uuid.UUID | None) -> VideoAsset:
    name = clean_filename(filename)
    db.execute(delete(VideoAsset).where(VideoAsset.status == "uploading", VideoAsset.created_at < utcnow() - ABANDONED_AFTER))
    lim = limits(db)
    if size_bytes <= 0:
        raise VideoError("This file is empty")
    if size_bytes > lim["max_upload_bytes"]:
        raise VideoError(f"This file is {_size(size_bytes)}; the attachment limit is {_size(lim['max_upload_bytes'])}. "
                         "Compress the video or use the clickable preview instead.", 413)
    if lim["library_used_bytes"] + size_bytes > lim["library_max_bytes"]:
        raise VideoError(f"The video library is limited to {_size(lim['library_max_bytes'])}. Delete an unused video "
                         "or raise the limit in Settings.", 413)
    asset = VideoAsset(filename=name, size_bytes=size_bytes, status="uploading", created_by=user_id)
    db.add(asset)
    db.flush()
    return asset


def total_chunks(asset: VideoAsset) -> int:
    return math.ceil(asset.size_bytes / CHUNK_BYTES)


def expected_chunk_size(asset: VideoAsset, index: int) -> int:
    return CHUNK_BYTES if index < total_chunks(asset) - 1 else asset.size_bytes - CHUNK_BYTES * index


def store_chunk(db: Session, asset: VideoAsset, index: int, data: bytes) -> None:
    if asset.status != "uploading":
        raise VideoError("This video has already been uploaded", 409)
    if not 0 <= index < total_chunks(asset):
        raise VideoError("Unknown part of this upload")
    if len(data) != expected_chunk_size(asset, index):
        raise VideoError("This part of the upload has the wrong size; try again")
    if index == 0:
        _check_brand(data[:4096])  # no point receiving the rest of a file that is not an MP4
    db.merge(VideoAssetChunk(asset_id=asset.id, index=index, data=data))
    db.flush()


def complete_upload(db: Session, asset: VideoAsset) -> tuple[VideoAsset, bool]:
    """Validate the whole file. Returns the ready asset and whether it was already in the library (same bytes)."""
    if asset.status == "ready":
        return asset, False
    present = db.scalar(select(func.count()).select_from(VideoAssetChunk).where(VideoAssetChunk.asset_id == asset.id))
    if present != total_chunks(asset):
        raise VideoError(f"The upload is incomplete ({present} of {total_chunks(asset)} parts)", 409)
    data = _join(db, asset.id)
    try:
        validate_mp4(data)
    except VideoError:
        db.delete(asset)
        db.commit()  # the rejected file must not stay stored; the caller's error path won't commit
        raise
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(VideoAsset).where(VideoAsset.sha256 == digest, VideoAsset.status == "ready"))
    if existing:
        db.delete(asset)
        db.flush()
        return existing, True
    asset.sha256, asset.status = digest, "ready"
    db.flush()
    return asset, False


def set_thumbnail(asset: VideoAsset, data: bytes) -> None:
    if len(data) > MAX_THUMBNAIL_BYTES:
        raise VideoError("The thumbnail image must be 512 KB or smaller", 413)
    if data.startswith(b"\xff\xd8\xff"):
        asset.thumbnail_type = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        asset.thumbnail_type = "image/png"
    else:
        raise VideoError("The thumbnail must be a JPEG or PNG image")
    asset.thumbnail = data


def delete_asset(db: Session, asset: VideoAsset) -> None:
    """Refused while an email that can still send uses it. Finished or deleted campaigns simply lose the reference."""
    from app.models import Campaign

    rows = db.execute(select(SequenceStep, Campaign).join(Campaign, Campaign.id == SequenceStep.campaign_id)
                      .where(SequenceStep.video_asset_id == asset.id)).all()
    live = sorted({c.name for _, c in rows if c.deleted_at is None and c.status not in ("completed", "stopped")})
    if live:
        raise VideoError(f"This video is used by: {', '.join(live)}. Remove it from those emails first.", 409)
    for step, _ in rows:
        step.video_asset_id, step.video_preview, step.video_attach = None, False, False
    db.flush()
    db.delete(asset)
    db.flush()


# --- reading the bytes (for sending only) -----------------------------------------------------------------------

def _join(db: Session, asset_id: uuid.UUID) -> bytes:
    return b"".join(db.scalars(select(VideoAssetChunk.data).where(VideoAssetChunk.asset_id == asset_id)
                               .order_by(VideoAssetChunk.index)))


# A campaign sends the same file to every prospect; an asset's bytes never change, so keep the latest one in memory
# rather than transferring it from the database for each email.
_loaded: tuple[uuid.UUID, Attachment] | None = None


def load_attachment(db: Session, asset_id: uuid.UUID) -> Attachment:
    global _loaded
    asset = db.get(VideoAsset, asset_id)
    if asset is None or asset.status != "ready":
        raise VideoError("The attached video is no longer available")
    if _loaded and _loaded[0] == asset_id:
        return Attachment(asset.filename, VIDEO_TYPE, _loaded[1].data)  # the name can change, the bytes cannot
    data = _join(db, asset_id)
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise VideoError("The stored video does not match what was uploaded")
    attachment = Attachment(asset.filename, VIDEO_TYPE, data)
    _loaded = (asset_id, attachment)
    return attachment


# --- what a step's video means when rendering and sending -------------------------------------------------------

def thumbnail_cid(asset: VideoAsset) -> str:
    return f"video-{asset.id.hex}@trace.local"


def video_link(asset: VideoAsset | None, *, on_screen: bool = False) -> VideoLink | None:
    """The clickable preview: an inline image in real email (no public hosting, so nothing is exposed and recipients'
    clients needn't fetch anything), a data: image for previews in the app."""
    if asset is None or not asset.link_url:
        return None
    if not asset.thumbnail_type:
        return VideoLink(asset.link_url)
    if on_screen:
        return VideoLink(asset.link_url, f"data:{asset.thumbnail_type};base64,{base64.b64encode(asset.thumbnail).decode()}")
    return VideoLink(asset.link_url, f"cid:{thumbnail_cid(asset)}")


def inline_images(asset: VideoAsset | None) -> list[InlineImage]:
    if asset is None or not asset.link_url or not asset.thumbnail_type:
        return []
    return [InlineImage(thumbnail_cid(asset), asset.thumbnail_type, asset.thumbnail)]


def attachment_info(asset: VideoAsset) -> dict:
    return {"filename": asset.filename, "size_bytes": asset.size_bytes, "content_type": VIDEO_TYPE,
            "encoded_bytes": mime.encoded_size(asset.size_bytes)}


def step_problem(db: Session, step: SequenceStep, providers: set[str]) -> str | None:
    """Why this email can't go out with the video it promises. An email is never sent without its video instead."""
    if not (step.video_preview or step.video_attach):
        return None
    asset = step.video_asset
    label = f"Step {step.position}"
    if asset is None or asset.status != "ready":
        return f"{label}: its video is not available; choose one again"
    if step.video_preview and not asset.link_url:
        return f"{label}: the clickable preview needs a video link. Add one to “{asset.filename}”"
    if step.video_attach:
        for provider in sorted(providers):
            if problem := size_problem(db, asset, provider):
                return f"{label}: {problem}"
    return None
