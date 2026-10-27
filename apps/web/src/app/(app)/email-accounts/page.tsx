"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FlaskConical, Mail, MoreHorizontal, Pause, Play, Plus, RefreshCw, Send, Settings2, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, PageHeader, StatusBadge } from "@/components/common";
import { ScheduleEditor, scheduleSummary } from "@/components/schedule-editor";
import { useIsAdmin } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { EmailAccount, MailProviders, Schedule } from "@/lib/types";

const PROVIDER_LABEL: Record<EmailAccount["provider"], string> = { gmail: "Gmail", microsoft: "Microsoft 365", sandbox: "Sandbox" };

function UsageBar({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, limit ? (used / limit) * 100 : 0);
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-muted-foreground">
        <span>Sent today</span>
        <span className="tabular-nums">
          {used} / {limit}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted" role="progressbar" aria-valuenow={used} aria-valuemax={limit} aria-label="Daily usage">
        <div className="h-1.5 rounded-full" style={{ width: `${pct}%`, background: "var(--series-1)" }} />
      </div>
    </div>
  );
}

const SENDING_MODE: Record<MailProviders["real_sending"], string> = {
  disabled: "Real sending is off on this server (REAL_SENDING=disabled): nothing will be sent from Gmail.",
  test_only: "Test-only mode (REAL_SENDING=test_only): test emails, campaigns and replies are sent only to addresses in ALLOWED_TEST_RECIPIENTS. Anyone else is held back.",
  enabled: "Real sending is ON (REAL_SENDING=enabled): active campaigns will send from this mailbox.",
};

const OAUTH_ERRORS: Record<string, string> = {
  access_denied: "Google access was not granted (the consent screen was cancelled).",
  missing_scopes: "Both permissions are required. Connect again and keep “Send email” and “Read email” ticked.",
  state_invalid: "That sign-in link was already used or is invalid. Start the connection again.",
  state_expired: "The sign-in took longer than 10 minutes. Start the connection again.",
  session_mismatch: "You were signed in as a different user when Google sent you back. Start again from this account.",
  not_signed_in: "Your Trace CRM session ended during sign-in. Log in and connect again.",
  forbidden: "Only admins can connect mailboxes.",
  wrong_account: "You signed in to a different Google account than the mailbox you were reconnecting.",
  no_refresh_token: "Google didn’t return offline access. Remove this app’s access at myaccount.google.com/permissions and connect again.",
  token_exchange_failed: "Google rejected the authorization (check the client ID/secret and redirect URI on the server).",
  profile_failed: "Connected, but the Gmail profile couldn’t be read. Check that the Gmail API is enabled for the Google Cloud project.",
  not_configured: "Google OAuth is not configured on the server.",
  google_error: "Google returned an error during sign-in.",
  missing_code: "Google didn’t return an authorization code. Try again.",
};

/** Starts OAuth only on click: the API returns Google's consent URL, then the browser navigates there. */
function ConnectGmailButton({ disabled, reconnectId, label = "Connect Gmail" }: { disabled?: boolean; reconnectId?: string; label?: string }) {
  const start = useMutation({
    mutationFn: () => api<{ authorization_url: string }>("/api/email-accounts/oauth/google/start", { body: { reconnect_account_id: reconnectId ?? null } }),
    onSuccess: (r) => window.location.assign(r.authorization_url),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Button size="sm" disabled={disabled || start.isPending || start.isSuccess} onClick={() => start.mutate()}>
      {start.isPending || start.isSuccess ? "Opening Google…" : label}
    </Button>
  );
}

/** Reads ?connected= / ?oauth_error= set by the OAuth callback, shows it once, then cleans the URL. */
function OAuthResult() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const connected = params.get("connected");
  const error = params.get("oauth_error");
  React.useEffect(() => {
    if (!connected && !error) return;
    if (connected) {
      toast.success(`Gmail connected: ${params.get("email") ?? ""}`, { description: "Replies will sync from now on." });
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
    } else if (error) {
      const detail = params.get("detail");
      toast.error("Gmail was not connected", { description: (OAUTH_ERRORS[error] ?? error) + (detail && error === "wrong_account" ? ` (${detail})` : ""), duration: 15000 });
    }
    router.replace("/email-accounts");
  }, [connected, error, params, router, qc]);
  return null;
}

function ConnectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const providers = useQuery({ queryKey: ["mail-providers"], queryFn: () => api<MailProviders>("/api/email-accounts/providers"), enabled: open });
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const create = useMutation({
    mutationFn: () => api<EmailAccount>("/api/email-accounts/sandbox", { body: { email, display_name: name || null } }),
    onSuccess: () => {
      toast.success("Sandbox mailbox added");
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      setEmail("");
      setName("");
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const p = providers.data;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect a mailbox</DialogTitle>
          <DialogDescription>Trace CRM sends from your own mailboxes through their official APIs.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-2 rounded-lg border p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Gmail / Google Workspace</div>
                <div className="text-xs text-muted-foreground">
                  {!p ? "Checking…" : p.gmail.available ? "You’ll sign in with Google and approve access." : p.gmail.reason}
                </div>
              </div>
              <ConnectGmailButton disabled={!p?.gmail.available} />
            </div>
            {p?.gmail.available && (
              <ul className="grid gap-0.5 text-xs text-muted-foreground">
                <li>• Send email as you (gmail.send)</li>
                <li>• Read your mail to detect replies and bounces (gmail.readonly). Only mail arriving after you connect is processed.</li>
                <li>• {SENDING_MODE[p.real_sending]}</li>
              </ul>
            )}
          </div>
          <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
            <div>
              <div className="text-sm font-medium">{PROVIDER_LABEL.microsoft}</div>
              <div className="text-xs text-muted-foreground">{p?.microsoft.reason ?? "Checking…"}</div>
            </div>
            <Button variant="outline" size="sm" disabled>
              Connect
            </Button>
          </div>
          {p?.sandbox.available && (
            <form
              className="grid gap-3 rounded-lg border border-dashed p-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (email.trim()) create.mutate();
              }}
            >
              <div>
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  <FlaskConical className="size-4 text-signal" aria-hidden /> Sandbox mailbox (development)
                </div>
                <p className="text-xs text-muted-foreground">
                  Nothing is sent. Emails are written as .eml files to <code className="break-all">{p.sandbox.outbox_dir}</code>, and you can
                  simulate replies and bounces from the inbox.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Address" htmlFor="sbx-email">
                  <Input id="sbx-email" type="email" placeholder="sam@tracestudio.dev" value={email} onChange={(e) => setEmail(e.target.value)} />
                </Field>
                <Field label="Sender name" htmlFor="sbx-name">
                  <Input id="sbx-name" placeholder="Sam at Trace" value={name} onChange={(e) => setName(e.target.value)} />
                </Field>
              </div>
              <Button type="submit" className="justify-self-end" disabled={create.isPending || !email.trim()}>
                Add sandbox mailbox
              </Button>
            </form>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SettingsDialog({ account, onOpenChange }: { account: EmailAccount | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState<{ display_name: string; daily_limit: number; min_interval_seconds: number; schedule: Schedule; signature_html: string } | null>(null);
  const [forId, setForId] = React.useState<string | null>(null);
  if (account && forId !== account.id) {
    setForId(account.id);
    setDraft({
      display_name: account.display_name ?? "",
      daily_limit: account.daily_limit,
      min_interval_seconds: account.min_interval_seconds,
      schedule: account.schedule,
      signature_html: account.signature_html ?? "",
    });
  }
  const save = useMutation({
    mutationFn: () => api<EmailAccount>(`/api/email-accounts/${account!.id}`, { method: "PATCH", body: { ...draft, signature_html: draft!.signature_html || null } }),
    onSuccess: () => {
      toast.success("Mailbox settings saved");
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!account} onOpenChange={(o) => { if (!o) setForId(null); onOpenChange(o); }}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Mailbox settings</DialogTitle>
          <DialogDescription>{account?.email}</DialogDescription>
        </DialogHeader>
        {draft && (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            <Field label="Sender name" htmlFor="mb-name">
              <Input id="mb-name" value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Daily limit" htmlFor="mb-limit" hint="Max 500. Start low for new mailboxes.">
                <Input id="mb-limit" type="number" min={1} max={500} value={draft.daily_limit}
                  onChange={(e) => setDraft({ ...draft, daily_limit: Number(e.target.value) })} />
              </Field>
              <Field label="Seconds between sends" htmlFor="mb-gap" hint="30–3600">
                <Input id="mb-gap" type="number" min={30} max={3600} value={draft.min_interval_seconds}
                  onChange={(e) => setDraft({ ...draft, min_interval_seconds: Number(e.target.value) })} />
              </Field>
            </div>
            <ScheduleEditor value={draft.schedule} onChange={(schedule) => setDraft({ ...draft, schedule })} />
            <Field label="Signature (HTML, used for manual replies)" htmlFor="mb-sig">
              <Textarea id="mb-sig" rows={3} value={draft.signature_html} onChange={(e) => setDraft({ ...draft, signature_html: e.target.value })} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={save.isPending}>
                Save
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** One fixed test email to an allow-listed address. The server enforces the allowlist and REAL_SENDING again. */
function TestEmailDialog({ account, onOpenChange }: { account: EmailAccount | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [to, setTo] = React.useState("");
  const allowed = useQuery({
    queryKey: ["test-recipients", account?.id],
    queryFn: () => api<{ recipients: string[]; real_sending: MailProviders["real_sending"] }>(`/api/email-accounts/${account!.id}/test-recipients`),
    enabled: !!account,
  });
  const recipients = allowed.data?.recipients ?? [];
  const chosen = recipients.includes(to) ? to : (recipients[0] ?? "");
  const blocked = account?.provider !== "sandbox" && allowed.data?.real_sending === "disabled";
  const send = useMutation({
    mutationFn: () => api<{ message: string }>(`/api/email-accounts/${account!.id}/test-email`, { body: { to_email: chosen } }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!account} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send test email</DialogTitle>
          <DialogDescription>
            One short message from {account?.email} to check the mailbox can send. No campaign or prospect is involved.
          </DialogDescription>
        </DialogHeader>
        {allowed.isError ? (
          <ErrorState error={allowed.error} onRetry={() => allowed.refetch()} />
        ) : allowed.isPending ? (
          <Skeleton className="h-16" />
        ) : (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              send.mutate();
            }}
          >
            {recipients.length === 0 ? (
              <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
                No allowed test recipients. Add your own address to ALLOWED_TEST_RECIPIENTS in the server .env and restart
                the API.
              </p>
            ) : (
              <Field label="Send to" htmlFor="test-to" hint="Only addresses in ALLOWED_TEST_RECIPIENTS can receive test emails.">
                <NativeSelect id="test-to" className="w-full" value={chosen} onChange={(e) => setTo(e.target.value)}>
                  {recipients.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            )}
            {account?.provider !== "sandbox" && allowed.data && (
              <p className="text-xs text-muted-foreground">{SENDING_MODE[allowed.data.real_sending]}</p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!chosen || blocked || send.isPending}>
                <Send /> Send test
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function AccountCard({ a, isAdmin, onEdit, onTest, onDisconnect }: { a: EmailAccount; isAdmin: boolean; onEdit: () => void; onTest: () => void; onDisconnect: () => void }) {
  const qc = useQueryClient();
  const act = useMutation({
    mutationFn: (action: "pause" | "resume" | "sync") => api<unknown>(`/api/email-accounts/${a.id}/${action}`, { method: "POST" }),
    onSuccess: (r, action) => {
      if (action === "sync") toast.success(`Synced: ${(r as { processed?: number }).processed ?? 0} new message(s)`);
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      qc.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const circuitOpen = a.circuit_open_until && new Date(a.circuit_open_until) > new Date();
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="truncate text-base">{a.email}</CardTitle>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <StatusBadge status={a.status} />
            <span>{PROVIDER_LABEL[a.provider]}</span>
            {a.display_name && <span>· {a.display_name}</span>}
          </div>
        </div>
        {isAdmin && (
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${a.email}`} />}>
              <MoreHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={onEdit}>
                <Settings2 /> Settings
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => act.mutate("sync")}>
                <RefreshCw /> Sync now
              </DropdownMenuItem>
              {a.status === "connected" && (
                <DropdownMenuItem onClick={onTest}>
                  <Send /> Send test email
                </DropdownMenuItem>
              )}
              {a.status === "paused" || a.status === "error" ? (
                <DropdownMenuItem onClick={() => act.mutate("resume")}>
                  <Play /> Resume sending
                </DropdownMenuItem>
              ) : a.status === "connected" ? (
                <DropdownMenuItem onClick={() => act.mutate("pause")}>
                  <Pause /> Pause sending
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={onDisconnect}>
                <Unplug /> Disconnect
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <UsageBar used={a.sent_today} limit={a.daily_limit} />
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Window</dt>
          <dd className="text-right">{scheduleSummary(a.schedule)}</dd>
          <dt className="text-muted-foreground">Spacing</dt>
          <dd className="text-right">{a.min_interval_seconds}s between sends</dd>
          <dt className="text-muted-foreground">Total sent</dt>
          <dd className="text-right tabular-nums">{a.sent_total.toLocaleString()}</dd>
          <dt className="text-muted-foreground">Last sync</dt>
          <dd className="text-right">{relTime(a.last_synced_at)}</dd>
        </dl>
        {a.campaigns.length > 0 && (
          <div className="flex flex-wrap gap-1 text-xs">
            {a.campaigns.map((c) => (
              <Link key={c.id} href={`/campaigns/${c.id}`} className="rounded-md bg-muted px-1.5 py-0.5 hover:underline">
                {c.name}
              </Link>
            ))}
          </div>
        )}
        {a.provider === "gmail" && a.status === "auth_error" && isAdmin && (
          <div className="flex items-center justify-between gap-2 rounded-md border border-destructive/30 px-2 py-1.5 text-xs">
            <span>Google access expired or was revoked.</span>
            <ConnectGmailButton reconnectId={a.id} label="Reconnect" />
          </div>
        )}
        {(a.last_error || circuitOpen) && (
          <p className="rounded-md bg-destructive/10 px-2 py-1.5 text-xs text-destructive" role="status">
            {circuitOpen ? `Paused after repeated failures until ${new Date(a.circuit_open_until!).toLocaleTimeString()}. ` : ""}
            {a.last_error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default function EmailAccountsPage() {
  const qc = useQueryClient();
  const isAdmin = useIsAdmin();
  const list = useQuery({ queryKey: ["email-accounts"], queryFn: () => api<EmailAccount[]>("/api/email-accounts") });
  const providers = useQuery({ queryKey: ["mail-providers"], queryFn: () => api<MailProviders>("/api/email-accounts/providers") });
  const hasReal = (list.data ?? []).some((a) => a.provider !== "sandbox");
  const [connecting, setConnecting] = React.useState(false);
  const [editing, setEditing] = React.useState<EmailAccount | null>(null);
  const [testing, setTesting] = React.useState<EmailAccount | null>(null);
  const [disconnecting, setDisconnecting] = React.useState<EmailAccount | null>(null);
  const disconnect = useMutation({
    mutationFn: (id: string) => api<{ message: string }>(`/api/email-accounts/${id}`, { method: "DELETE" }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["email-accounts"] });
      setDisconnecting(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="Email Accounts"
        description="Mailboxes that send your campaigns. Limits and windows apply per mailbox."
        actions={
          isAdmin && (
            <Button onClick={() => setConnecting(true)}>
              <Plus /> Connect mailbox
            </Button>
          )
        }
      />
      <React.Suspense>
        <OAuthResult />
      </React.Suspense>
      {hasReal && providers.data && (
        <p
          role="status"
          className={
            providers.data.real_sending === "enabled"
              ? "mb-4 rounded-lg border border-signal/40 bg-primary/10 px-3 py-2 text-sm"
              : "mb-4 rounded-lg border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
          }
        >
          {SENDING_MODE[providers.data.real_sending]} Reply sync works in every mode.
        </p>
      )}
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-56" />
          ))}
        </div>
      ) : list.data.length === 0 ? (
        <EmptyState
          icon={Mail}
          title="No mailboxes connected"
          description={isAdmin ? "Connect a mailbox to start sending campaigns." : "Ask an admin to connect a mailbox."}
          action={
            isAdmin && (
              <Button onClick={() => setConnecting(true)}>
                <Plus /> Connect mailbox
              </Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list.data.map((a) => (
            <AccountCard key={a.id} a={a} isAdmin={isAdmin} onEdit={() => setEditing(a)} onTest={() => setTesting(a)} onDisconnect={() => setDisconnecting(a)} />
          ))}
        </div>
      )}
      <ConnectDialog open={connecting} onOpenChange={setConnecting} />
      <SettingsDialog account={editing} onOpenChange={(o) => !o && setEditing(null)} />
      <TestEmailDialog account={testing} onOpenChange={(o) => !o && setTesting(null)} />
      <ConfirmDialog
        open={!!disconnecting}
        onOpenChange={(o) => !o && setDisconnecting(null)}
        title={`Disconnect ${disconnecting?.email}?`}
        description={`It stops sending and syncing and is removed from its campaigns.${
          disconnecting?.provider === "gmail" ? " Access is also revoked at Google and the stored tokens are deleted." : ""
        } Sent history and conversations are kept.`}
        confirmLabel="Disconnect"
        destructive
        pending={disconnect.isPending}
        onConfirm={() => disconnecting && disconnect.mutate(disconnecting.id)}
      />
    </>
  );
}
