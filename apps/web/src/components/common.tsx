"use client";

import * as React from "react";
import { cn } from "cn";
import type { LucideIcon } from "lucide-react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { humanize } from "@/lib/format";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-14 text-center",
        className,
      )}
    >
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" aria-hidden />
      </div>
      <h3 className="text-sm font-medium">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export const NativeSelect = React.forwardRef<HTMLSelectElement, React.ComponentProps<"select">>(
  function NativeSelect({ className, children, ...props }, ref) {
    return (
      <select
        ref={ref}
        className={cn(
          "h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground",
          className,
        )}
        {...props}
      >
        {children}
      </select>
    );
  },
);

export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor?: string;
  error?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  // campaign / mailbox / message states
  active: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  connected: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  completed: "bg-sky-500/12 text-sky-700 dark:text-sky-400",
  scheduled: "bg-violet-500/12 text-violet-700 dark:text-violet-400",
  paused: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  draft: "bg-muted text-muted-foreground",
  stopped: "bg-muted text-muted-foreground",
  disconnected: "bg-muted text-muted-foreground",
  error: "bg-red-500/12 text-red-700 dark:text-red-400",
  auth_error: "bg-red-500/12 text-red-700 dark:text-red-400",
  failed: "bg-red-500/12 text-red-700 dark:text-red-400",
  // lead stages
  new_lead: "bg-muted text-muted-foreground",
  contacted: "bg-sky-500/12 text-sky-700 dark:text-sky-400",
  replied: "bg-indigo-500/12 text-indigo-700 dark:text-indigo-400",
  interested: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  meeting_scheduled: "bg-violet-500/12 text-violet-700 dark:text-violet-400",
  proposal_sent: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  won: "bg-emerald-600/15 text-emerald-800 dark:text-emerald-300",
  lost: "bg-red-500/12 text-red-700 dark:text-red-400",
  not_interested: "bg-muted text-muted-foreground",
  // enrollment / queue states
  bounced: "bg-red-500/12 text-red-700 dark:text-red-400",
  unsubscribed: "bg-muted text-muted-foreground",
  removed: "bg-muted text-muted-foreground",
  queued: "bg-violet-500/12 text-violet-700 dark:text-violet-400",
  sending: "bg-sky-500/12 text-sky-700 dark:text-sky-400",
  sent: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  cancelled: "bg-muted text-muted-foreground",
  // reply intent
  positive: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  neutral: "bg-muted text-muted-foreground",
  negative: "bg-red-500/12 text-red-700 dark:text-red-400",
  meeting: "bg-violet-500/12 text-violet-700 dark:text-violet-400",
  not_now: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  wrong_person: "bg-muted text-muted-foreground",
  // verification
  valid: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  risky: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  invalid: "bg-red-500/12 text-red-700 dark:text-red-400",
  unknown: "bg-muted text-muted-foreground",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge variant="secondary" className={cn("border-0 font-medium", STATUS_TONE[status], className)}>
      {humanize(status)}
    </Badge>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between gap-2 py-3 text-sm text-muted-foreground">
      <span>
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        <span className="px-2 tabular-nums">
          {page} / {pages}
        </span>
        <Button variant="outline" size="icon-sm" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  destructive,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  pending?: boolean;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={pending}
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
          >
            {pending && <Loader2 className="animate-spin" />}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted-foreground", className)} aria-label="Loading" />;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm" role="alert">
      <p className="font-medium text-destructive">Couldn’t load this data</p>
      <p className="mt-1 text-muted-foreground">{error instanceof Error ? error.message : "Unknown error"}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
