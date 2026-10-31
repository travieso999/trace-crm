"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ImagePlus, Paperclip, Trash2, Upload, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog, ErrorState, Field, Spinner } from "@/components/common";
import { ApiError, api, errorMessage } from "@/lib/api";
import { dateTime, fileSize } from "@/lib/format";
import type { AttachmentInfo, Step, VideoAsset, VideoLibrary } from "@/lib/types";

const MAX_THUMBNAIL = 512 * 1024;

/** Filename and size, as they will show on the email. */
export function AttachmentChip({ attachment }: { attachment: AttachmentInfo }) {
  return (
    <div className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
      <Paperclip className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 truncate font-medium">{attachment.filename}</span>
      <span className="shrink-0 text-muted-foreground">{fileSize(attachment.size_bytes)}</span>
    </div>
  );
}

export function WarningList({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="grid list-disc gap-1 rounded-md bg-amber-500/12 py-1.5 pl-6 pr-2 text-xs text-amber-800 dark:text-amber-300" role="status">
      {warnings.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  );
}

/** A frame of the video with a play button, as a JPEG. Null when this browser can't decode the file. */
async function captureThumbnail(file: File): Promise<Blob | null> {
  const url = URL.createObjectURL(file);
  try {
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = url;
    const ready = (event: "loadeddata" | "seeked") =>
      new Promise<boolean>((resolve) => {
        video.addEventListener(event, () => resolve(true), { once: true });
        video.addEventListener("error", () => resolve(false), { once: true });
        setTimeout(() => resolve(false), 15_000);
      });
    if (!(await ready("loadeddata"))) return null;
    const seeked = ready("seeked");
    video.currentTime = Math.min(1, (video.duration || 0) / 10);
    if (!(await seeked) || !video.videoWidth) return null;
    const scale = Math.min(1, 960 / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const r = Math.min(canvas.width, canvas.height) * 0.12;
    const [cx, cy] = [canvas.width / 2, canvas.height / 2];
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.moveTo(cx - r * 0.35, cy - r * 0.5);
    ctx.lineTo(cx + r * 0.55, cy);
    ctx.lineTo(cx - r * 0.35, cy + r * 0.5);
    ctx.fill();
    for (const quality of [0.8, 0.6, 0.4]) {
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
      if (blob && blob.size <= MAX_THUMBNAIL) return blob;
    }
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function putChunk(id: string, index: number, part: Blob) {
  try {
    await api(`/api/video-assets/${id}/chunks/${index}`, { method: "PUT", raw: part });
  } catch (e) {
    if (e instanceof ApiError && e.status < 500) throw e;
    await api(`/api/video-assets/${id}/chunks/${index}`, { method: "PUT", raw: part }); // one retry for a dropped connection
  }
}

async function uploadVideo(file: File, onProgress: (done: number) => void): Promise<{ asset: VideoAsset; duplicate: boolean }> {
  const start = await api<{ id: string; chunk_bytes: number; total_chunks: number }>("/api/video-assets", {
    body: { filename: file.name, size_bytes: file.size },
  });
  for (let i = 0; i < start.total_chunks; i++) {
    await putChunk(start.id, i, file.slice(i * start.chunk_bytes, (i + 1) * start.chunk_bytes));
    onProgress((i + 1) / start.total_chunks);
  }
  const done = await api<{ asset: VideoAsset; duplicate: boolean }>(`/api/video-assets/${start.id}/complete`, { method: "POST" });
  if (!done.asset.has_thumbnail) {
    const thumb = await captureThumbnail(file);
    if (thumb) done.asset = await api<VideoAsset>(`/api/video-assets/${done.asset.id}/thumbnail`, { method: "PUT", raw: thumb });
  }
  return done;
}

/** The reusable library: upload once, then use the same video in any campaign email. */
function LibraryDialog({ open, campaignId, onOpenChange, onChoose }: {
  open: boolean; campaignId: string; onOpenChange: (o: boolean) => void; onChoose: (a: VideoAsset) => void;
}) {
  const qc = useQueryClient();
  const input = React.useRef<HTMLInputElement>(null);
  const [progress, setProgress] = React.useState<number | null>(null);
  const [toDelete, setToDelete] = React.useState<VideoAsset | null>(null);
  const q = useQuery({
    queryKey: ["video-assets", campaignId],
    queryFn: () => api<VideoLibrary>("/api/video-assets", { query: { campaign_id: campaignId } }),
    enabled: open,
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const max = q.data?.limits.max_upload_bytes;
      if (!file.name.toLowerCase().endsWith(".mp4")) throw new Error("Only .mp4 files can be attached");
      if (max && file.size > max) throw new Error(`This file is ${fileSize(file.size)}; the attachment limit is ${fileSize(max)}. Compress the video first.`);
      setProgress(0);
      return uploadVideo(file, setProgress);
    },
    onSuccess: (r) => {
      toast.success(r.duplicate ? "This video is already in the library, so the existing copy is used" : "Video uploaded");
      qc.invalidateQueries({ queryKey: ["video-assets"] });
      onChoose(r.asset);
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
    onSettled: () => setProgress(null),
  });
  const remove = useMutation({
    mutationFn: (a: VideoAsset) => api(`/api/video-assets/${a.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Video deleted");
      qc.invalidateQueries({ queryKey: ["video-assets"] });
      setToDelete(null);
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      setToDelete(null);
    },
  });
  const limits = q.data?.limits;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Attach video</DialogTitle>
          <DialogDescription>
            Upload an MP4 once and reuse it in any campaign email. Stored privately: the file only leaves the app as an email attachment.
            {limits && ` Up to ${fileSize(limits.max_upload_bytes)} per file.`}
          </DialogDescription>
        </DialogHeader>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : (
          <div className="grid gap-3 text-sm">
            <input
              ref={input}
              type="file"
              accept="video/mp4,.mp4"
              className="sr-only"
              aria-label="Choose an MP4 file"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) upload.mutate(file);
              }}
            />
            <Button type="button" variant="outline" disabled={upload.isPending} onClick={() => input.current?.click()}>
              <Upload /> {progress === null ? "Upload MP4" : `Uploading… ${Math.round(progress * 100)}%`}
            </Button>
            {q.data.items.length === 0 ? (
              <p className="text-muted-foreground">No videos yet.</p>
            ) : (
              <ul className="divide-y rounded-lg border">
                {q.data.items.map((a) => (
                  <li key={a.id} className="flex items-center justify-between gap-2 px-3 py-2">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{a.filename}</span>
                      <span className="text-xs text-muted-foreground">
                        {fileSize(a.size_bytes)} · {dateTime(a.created_at)}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      <Button type="button" size="sm" variant="outline" onClick={() => { onChoose(a); onOpenChange(false); }}>
                        Use
                      </Button>
                      <Button type="button" size="icon-sm" variant="ghost" aria-label={`Delete ${a.filename}`} onClick={() => setToDelete(a)}>
                        <Trash2 />
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {limits && (
              <p className="text-xs text-muted-foreground">
                Library: {fileSize(limits.library_used_bytes)} of {fileSize(limits.library_max_bytes)} used.
              </p>
            )}
          </div>
        )}
        <ConfirmDialog
          open={!!toDelete}
          onOpenChange={(o) => !o && setToDelete(null)}
          title={`Delete “${toDelete?.filename}”?`}
          description="The stored file is removed permanently. Emails that can still send must stop using it first."
          confirmLabel="Delete"
          pending={remove.isPending}
          onConfirm={() => toDelete && remove.mutate(toDelete)}
        />
      </DialogContent>
    </Dialog>
  );
}

/** The video options of one campaign email: which video, and how it is included. */
export function VideoSection({ step, index, readOnly, campaignId, onChange }: {
  step: Step; index: number; readOnly: boolean; campaignId: string; onChange: (s: Step) => void;
}) {
  const qc = useQueryClient();
  const [libraryOpen, setLibraryOpen] = React.useState(false);
  const thumbInput = React.useRef<HTMLInputElement>(null);
  const q = useQuery({
    queryKey: ["video-assets", campaignId],
    queryFn: () => api<VideoLibrary>("/api/video-assets", { query: { campaign_id: campaignId } }),
    enabled: !!step.video_asset_id,
  });
  const asset = q.data?.items.find((a) => a.id === step.video_asset_id);
  const [link, setLink] = React.useState<string | null>(null);
  const linkValue = link ?? asset?.link_url ?? "";
  const refresh = () => qc.invalidateQueries({ queryKey: ["video-assets"] }).then(() => qc.invalidateQueries({ queryKey: ["preview"] }));
  const saveLink = useMutation({
    mutationFn: () => api<VideoAsset>(`/api/video-assets/${asset!.id}`, { method: "PATCH", body: { link_url: linkValue.trim() || null } }),
    onSuccess: () => {
      setLink(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveThumb = useMutation({
    mutationFn: (file: File) => api<VideoAsset>(`/api/video-assets/${asset!.id}/thumbnail`, { method: "PUT", raw: file }),
    onSuccess: () => {
      toast.success("Thumbnail updated");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const library = (
    <LibraryDialog
      open={libraryOpen}
      campaignId={campaignId}
      onOpenChange={setLibraryOpen}
      onChoose={(a) => onChange({ ...step, video_asset_id: a.id, video_attach: step.video_attach || !step.video_preview })}
    />
  );
  if (!step.video_asset_id) {
    if (readOnly) return null;
    return (
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => setLibraryOpen(true)}>
          <Video /> Attach Video
        </Button>
        {library}
      </div>
    );
  }
  return (
    <section className="grid gap-2 rounded-lg border p-3 text-sm" aria-label="Video">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <Video className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate font-medium">{asset?.filename ?? "Loading…"}</span>
          {asset && <span className="shrink-0 text-xs text-muted-foreground">{fileSize(asset.size_bytes)}</span>}
        </span>
        {!readOnly && (
          <span className="flex items-center gap-1">
            <Button type="button" variant="ghost" size="sm" onClick={() => setLibraryOpen(true)}>
              Change
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ ...step, video_asset_id: null, video_preview: false, video_attach: false })}>
              Remove
            </Button>
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-[var(--primary)]" disabled={readOnly} checked={!!step.video_preview}
            onChange={(e) => onChange({ ...step, video_preview: e.target.checked })} />
          Include clickable video preview
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-[var(--primary)]" disabled={readOnly} checked={!!step.video_attach}
            onChange={(e) => onChange({ ...step, video_attach: e.target.checked })} />
          Attach actual video file
        </label>
      </div>
      <p className="text-xs text-muted-foreground">Tick both to include both. With neither ticked, saving removes the video from this email.</p>
      {step.video_preview && asset && (
        <div className="grid gap-2">
          <Field label="Video link" htmlFor={`video-link-${index}`} hint="Where the preview leads, such as a YouTube, Vimeo or Loom page. The stored file is private, so it can't be the link. Shared by every email using this video.">
            <Input id={`video-link-${index}`} type="url" disabled={readOnly} value={linkValue} placeholder="https://…"
              onChange={(e) => setLink(e.target.value)} onBlur={() => link !== null && saveLink.mutate()} />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">
              {asset.has_thumbnail ? "Thumbnail set: see the preview." : "No thumbnail yet, so the email shows a text link."}
            </span>
            {!readOnly && (
              <>
                <input ref={thumbInput} type="file" accept="image/jpeg,image/png" className="sr-only" aria-label="Choose a thumbnail image"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) saveThumb.mutate(file);
                  }} />
                <Button type="button" variant="ghost" size="sm" disabled={saveThumb.isPending} onClick={() => thumbInput.current?.click()}>
                  <ImagePlus /> {asset.has_thumbnail ? "Replace thumbnail" : "Add thumbnail"}
                </Button>
              </>
            )}
          </div>
        </div>
      )}
      {step.video_attach && asset && (
        <>
          <p className="text-xs text-muted-foreground">
            Sent as a real MP4 attachment. Recipients open or download it; don’t expect it to play inside the email.
          </p>
          <WarningList warnings={asset.warnings} />
        </>
      )}
      {library}
    </section>
  );
}
