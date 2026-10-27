"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, ShieldCheck, ShieldOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Spinner } from "@/components/common";
import { useIsAdmin, useMe } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { dateTime } from "@/lib/format";

type Setup = { secret: string; otpauth_uri: string; qr_svg: string };
type AuditEntry = {
  id: string;
  created_at: string;
  action: string;
  user_email: string | null;
  ip_address: string | null;
  data: Record<string, unknown>;
};

const ACTIONS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "auth.login_failed": "Failed sign-in",
  "auth.mfa_failed": "Wrong two-step code",
  "auth.locked_out": "Sign-in locked",
  "auth.password_changed": "Password changed",
  "auth.sessions_revoked": "Signed out other sessions",
  "auth.mfa_setup_started": "Started two-step setup",
  "auth.mfa_enabled": "Two-step verification on",
  "auth.mfa_disabled": "Two-step verification off",
  "auth.recovery_code_used": "Used a recovery code",
  "auth.recovery_codes_replaced": "New recovery codes",
  "authz.denied": "Permission refused",
  "user.created": "User created",
  "user.updated": "User changed",
  "user.mfa_reset": "Two-step reset by admin",
  "contacts.exported": "Prospects exported",
  "contacts.export_refused": "Export refused (limit)",
  "settings.updated": "Settings changed",
};

const RISKY = new Set(["auth.login_failed", "auth.mfa_failed", "auth.locked_out", "auth.mfa_disabled", "authz.denied",
  "auth.recovery_code_used", "contacts.exported", "user.mfa_reset"]);

function describe(e: AuditEntry) {
  const label = ACTIONS[e.action] ?? e.action.replace(/^[a-z]+\./, "").replace(/_/g, " ");
  const who = e.user_email ?? (typeof e.data.email === "string" ? e.data.email : null);
  const extra = e.action === "contacts.exported" ? `${e.data.count ?? 0} prospects`
    : e.action === "authz.denied" ? `${e.data.method} ${e.data.path}` : null;
  return { label, who, extra };
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  return (
    <div className="grid gap-2">
      <p className="text-sm">
        Save these recovery codes somewhere safe (a password manager). Each one signs you in once if you lose your
        phone. <strong>They won’t be shown again.</strong>
      </p>
      <ul className="grid grid-cols-2 gap-1 rounded-md border bg-muted/40 p-3 font-mono text-sm">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <Button
        type="button"
        variant="outline"
        onClick={() => navigator.clipboard.writeText(codes.join("\n")).then(() => toast.success("Copied"))}
      >
        <Copy /> Copy codes
      </Button>
    </div>
  );
}

function EnableDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [setup, setSetup] = React.useState<Setup | null>(null);
  const [codes, setCodes] = React.useState<string[] | null>(null);
  const start = useMutation({
    mutationFn: () => api<Setup>("/api/auth/mfa/setup", { body: { password } }),
    onSuccess: (d) => { setSetup(d); setPassword(""); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const confirm = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/api/auth/mfa/enable", { body: { code } }),
    onSuccess: (d) => {
      setCodes(d.recovery_codes);
      qc.invalidateQueries({ queryKey: ["me"] });
      toast.success("Two-step verification is on");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) { setSetup(null); setCodes(null); setCode(""); setPassword(""); }
  };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Turn on two-step verification</DialogTitle>
          <DialogDescription>
            After your password, sign-in will also ask for a 6-digit code from an authenticator app (Google
            Authenticator, Microsoft Authenticator, 1Password…).
          </DialogDescription>
        </DialogHeader>
        {codes ? (
          <>
            <RecoveryCodes codes={codes} />
            <Button type="button" onClick={() => close(false)}>I’ve saved them</Button>
          </>
        ) : !setup ? (
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); start.mutate(); }}>
            <Field label="Confirm your password" htmlFor="mfa-pw">
              <Input id="mfa-pw" type="password" autoComplete="current-password" autoFocus value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <Button type="submit" disabled={!password || start.isPending}>
              {start.isPending && <Spinner />} Continue
            </Button>
          </form>
        ) : (
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); confirm.mutate(); }}>
            <p className="text-sm">1. Scan this with your authenticator app.</p>
            {/* eslint-disable-next-line @next/next/no-img-element -- generated SVG, not a static asset */}
            <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(setup.qr_svg)}`} alt="QR code for your authenticator app"
                 className="mx-auto size-48 rounded-md border bg-white p-1" />
            <p className="text-xs text-muted-foreground">
              Can’t scan? Enter this key instead: <code className="break-all font-mono">{setup.secret}</code>
            </p>
            <Field label="2. Enter the 6-digit code it shows" htmlFor="mfa-code">
              <Input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus value={code}
                     onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
            </Field>
            <Button type="submit" disabled={code.length !== 6 || confirm.isPending}>
              {confirm.isPending && <Spinner />} Turn on
            </Button>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Password + current code, for turning it off or replacing recovery codes. */
function ConfirmDialog({ mode, onOpenChange }: { mode: "disable" | "codes" | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [codes, setCodes] = React.useState<string[] | null>(null);
  const run = useMutation({
    mutationFn: () => api<{ recovery_codes?: string[] }>(
      mode === "disable" ? "/api/auth/mfa/disable" : "/api/auth/mfa/recovery-codes", { body: { password, code } }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["me"] });
      if (d.recovery_codes) setCodes(d.recovery_codes);
      else { toast.success("Two-step verification is off"); close(false); }
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  function close(o: boolean) {
    onOpenChange(o);
    if (!o) { setPassword(""); setCode(""); setCodes(null); }
  }
  return (
    <Dialog open={mode !== null} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{mode === "disable" ? "Turn off two-step verification" : "New recovery codes"}</DialogTitle>
          <DialogDescription>
            {mode === "disable"
              ? "Your account will be protected by your password only. Admins are notified."
              : "Your old recovery codes stop working."}
          </DialogDescription>
        </DialogHeader>
        {codes ? (
          <>
            <RecoveryCodes codes={codes} />
            <Button type="button" onClick={() => close(false)}>I’ve saved them</Button>
          </>
        ) : (
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
            <Field label="Password" htmlFor="mfa-c-pw">
              <Input id="mfa-c-pw" type="password" autoComplete="current-password" autoFocus value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <Field label={mode === "disable" ? "Code from your app (or a recovery code)" : "Code from your app"} htmlFor="mfa-c-code">
              <Input id="mfa-c-code" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.trim())} />
            </Field>
            <Button type="submit" variant={mode === "disable" ? "destructive" : "default"}
                    disabled={!password || code.length < 6 || run.isPending}>
              {run.isPending && <Spinner />} {mode === "disable" ? "Turn off" : "Replace codes"}
            </Button>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function SecurityLog() {
  const q = useQuery({
    queryKey: ["security-log"],
    queryFn: () => api<AuditEntry[]>("/api/auth/audit-log", { query: { limit: 50 } }),
  });
  if (q.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (q.isError) return <p className="text-sm text-destructive">{errorMessage(q.error)}</p>;
  return (
    <ul className="max-h-80 divide-y overflow-y-auto text-sm">
      {q.data.map((e) => {
        const { label, who, extra } = describe(e);
        return (
          <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1.5">
            <span className={RISKY.has(e.action) ? "font-medium text-signal" : undefined}>
              {label}
              {who && <span className="font-normal text-muted-foreground"> · {who}</span>}
              {extra && <span className="font-normal text-muted-foreground"> · {extra}</span>}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {dateTime(e.created_at)}{e.ip_address ? ` · ${e.ip_address}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function SecuritySettings() {
  const me = useMe();
  const isAdmin = useIsAdmin();
  const [enabling, setEnabling] = React.useState(false);
  const [confirming, setConfirming] = React.useState<"disable" | "codes" | null>(null);
  const signOutOthers = useMutation({
    mutationFn: () => api<{ message: string }>("/api/auth/sessions/revoke-others", { method: "POST" }),
    onSuccess: (d) => toast.success(d.message),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const on = me.data?.user.mfa_enabled ?? false;
  return (
    <Card id="security" className="scroll-mt-20 lg:col-span-2">
      <CardHeader>
        <CardTitle>Security</CardTitle>
        <CardDescription>Your sign-in protection{isAdmin ? ", and recent security events for the whole workspace" : ""}.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <div className="grid content-start gap-3">
          <p className="flex items-center gap-2 font-medium">
            {on ? <ShieldCheck className="size-5 text-emerald-600 dark:text-emerald-400" aria-hidden />
              : <ShieldOff className="size-5 text-signal" aria-hidden />}
            Two-step verification is {on ? "on" : "off"}
          </p>
          <p className="text-sm text-muted-foreground">
            {on ? "Signing in needs your password and a code from your authenticator app."
              : "With it on, a stolen or guessed password isn’t enough to get into your account."}
            {isAdmin && !on && " Required for admins on the live site."}
          </p>
          <div className="flex flex-wrap gap-2">
            {on ? (
              <>
                <Button variant="outline" onClick={() => setConfirming("codes")}>New recovery codes</Button>
                <Button variant="outline" onClick={() => setConfirming("disable")}>Turn off</Button>
              </>
            ) : (
              <Button onClick={() => setEnabling(true)}>Turn on two-step verification</Button>
            )}
            <Button variant="outline" disabled={signOutOthers.isPending} onClick={() => signOutOthers.mutate()}>
              Sign out other devices
            </Button>
          </div>
        </div>
        {isAdmin && (
          <div className="grid content-start gap-2">
            <p className="font-medium">Security log</p>
            <SecurityLog />
          </div>
        )}
      </CardContent>
      <EnableDialog open={enabling} onOpenChange={setEnabling} />
      <ConfirmDialog mode={confirming} onOpenChange={(o) => !o && setConfirming(null)} />
    </Card>
  );
}
