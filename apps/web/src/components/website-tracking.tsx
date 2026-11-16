"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, CircleAlert, Copy, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common";
import { etTime } from "@/components/engagement";
import { api } from "@/lib/api";
import { num } from "@/lib/format";
import type { SiteTrackingStatus } from "@/lib/types";

export const EMAIL_LINK = "https://trace-studio.tech/demo?rid={{recipient_id}}";

function copy(text: string, what: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${what} copied`), () => toast.error("Couldn't copy"));
}

function CopyRow({ label, value, shown = value, hint }: { label: string; value: string; shown?: string; hint?: React.ReactNode }) {
  return (
    <div className="grid gap-1">
      <span className="text-sm font-medium">{label}</span>
      <div className="flex items-center gap-1.5">
        <code className="min-w-0 flex-1 break-all rounded-md border bg-muted/40 px-2.5 py-1.5 font-mono text-[13px]">{shown}</code>
        <Button type="button" variant="outline" size="icon-sm" aria-label={`Copy ${label.toLowerCase()}`} onClick={() => copy(value, label)}>
          <Copy />
        </Button>
      </div>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

/** Settings → Tracking: what to paste into Vercel for trace-studio.tech, and whether batches are arriving. */
export function WebsiteTrackingCard() {
  const [reveal, setReveal] = React.useState(false);
  const q = useQuery({ queryKey: ["site-tracking"], queryFn: () => api<SiteTrackingStatus>("/api/tracking/site"), refetchInterval: 30_000 });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Website tracking (trace-studio.tech)</CardTitle>
        <CardDescription>
          The website forwards every step a prospect takes on the demo video page and in the demos. Visits appear on the
          prospect&apos;s Engagement tab and in Engaged Prospects.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.isPending ? <Skeleton className="h-40" /> : (
          <>
            <CopyRow label="Email link" value={EMAIL_LINK}
              hint={<>Use this for the video/demo button. <code>{"{{recipient_id}}"}</code> becomes the prospect&apos;s id; demo links without it get <code>rid</code> added automatically.</>} />
            <CopyRow label="TRACKING_WEBHOOK_URL" value={q.data.endpoint} hint="Paste into the website's Vercel project." />
            {!q.data.configured ? (
              <p className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
                <span>Not receiving yet: set <code>TRACKING_WEBHOOK_SECRET</code> (at least 32 characters) on the API&apos;s Vercel project and on the website&apos;s, then redeploy both. Until then the address answers 503.</span>
              </p>
            ) : q.data.secret ? (
              <div className="grid gap-1">
                <CopyRow label="TRACKING_WEBHOOK_SECRET" value={q.data.secret}
                  shown={reveal ? q.data.secret : "•".repeat(Math.min(q.data.secret.length, 48))}
                  hint="The same value must be set on the API (it is) and on the website." />
                <Button type="button" variant="ghost" size="sm" className="justify-self-start" onClick={() => setReveal((r) => !r)}>
                  {reveal ? <EyeOff /> : <Eye />}{reveal ? "Hide" : "Show"} secret
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">The secret is set. Only administrators can see it.</p>
            )}
            <div className="grid gap-1 text-sm">
              <span className="flex items-center gap-1.5 font-medium">
                {q.data.last_received_at ? <CheckCircle2 className="size-4 text-emerald-600" aria-hidden /> : <CircleAlert className="size-4 text-muted-foreground" aria-hidden />}
                {q.data.last_received_at ? `Last batch received ${etTime(q.data.last_received_at)}` : "No batch received yet"}
              </span>
              <span className="text-muted-foreground">
                {num(q.data.events)} events · {num(q.data.visitors)} visitors, {num(q.data.matched_visitors)} matched to a prospect
              </span>
            </div>
            {q.data.unmatched.length > 0 && (
              <div className="grid gap-1.5">
                <span className="text-sm font-medium">Not matched to a prospect yet</span>
                <span className="text-xs text-muted-foreground">Linked automatically when they arrive with a known rid, or give an email or phone that matches a prospect.</span>
                <ul className="grid gap-1 text-xs">
                  {q.data.unmatched.map((u) => (
                    <li key={u.vid} className="rounded-md border px-2.5 py-1.5">
                      <span className="font-medium">{u.name || u.email || u.phone || `Visitor ${u.vid}`}</span>
                      <span className="text-muted-foreground">
                        {" · "}{[u.email && u.name ? u.email : null, u.rid && `rid ${u.rid}`, [u.city, u.region].filter(Boolean).join(", "),
                          u.device, `${num(u.events)} events`, etTime(u.last_seen_at)].filter(Boolean).join(" · ")}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
