"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Archive, ArrowRightLeft, Bell, CheckCircle2, CircleDot, History, ListPlus, Mail, MessageSquareReply, NotebookPen, Pencil,
  RotateCcw, Sparkles, Trash2, UserRoundPen,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/common";
import { api } from "@/lib/api";
import { dateTime, relTime } from "@/lib/format";
import type { ActivityItem } from "@/lib/types";

const ICONS: Record<string, LucideIcon> = {
  created: Sparkles, stage_change: ArrowRightLeft, contact_updated: UserRoundPen, opportunity_status: CircleDot,
  task_created: ListPlus, task_updated: Pencil, task_completed: CheckCircle2, task_reopened: RotateCcw, task_deleted: Trash2,
  task: ListPlus, note: NotebookPen, note_edited: Pencil, note_archived: Archive, note_restored: RotateCcw,
  email_sent: Mail, reply: MessageSquareReply, reminder: Bell,
};

export function ActivityTab({ contactId }: { contactId: string }) {
  const q = useQuery({
    queryKey: ["opportunity-activity", contactId],
    queryFn: () => api<ActivityItem[]>(`/api/opportunities/${contactId}/activity`),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending) return <div className="grid gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>;
  if (q.data.length === 0) return <EmptyState icon={History} title="No activity yet" />;
  return (
    <ol className="relative grid gap-0" aria-label="Activity history">
      {q.data.map((a, i) => {
        const Icon = ICONS[a.type] ?? CircleDot;
        return (
          <li key={a.id} className="relative flex gap-3 pb-4">
            {i < q.data.length - 1 && <span className="absolute top-7 bottom-0 left-[13px] w-px bg-border" aria-hidden />}
            <span className="relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border bg-popover">
              <Icon className="size-3.5 text-muted-foreground" aria-hidden />
            </span>
            <div className="grid min-w-0 gap-0.5 pt-0.5">
              <p className="text-sm break-words">{a.summary}</p>
              <p className="text-xs text-muted-foreground">
                {a.actor} · <time dateTime={a.at} title={relTime(a.at)}>{dateTime(a.at)}</time>
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
