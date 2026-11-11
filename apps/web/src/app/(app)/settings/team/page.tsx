"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, KeyRound, MoreHorizontal, Pencil, Power, RefreshCw, ShieldCheck, Trash2, UserPlus, UsersRound } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, PageHeader, Spinner } from "@/components/common";
import { ROLE_LABEL, useIsAdmin, useMe } from "@/components/app-shell";
import { SettingsNav } from "@/components/settings-nav";
import { memberName } from "@/components/team";
import { api, errorMessage } from "@/lib/api";
import { dateTime, relTime } from "@/lib/format";
import type { PermissionCatalog, Role, User } from "@/lib/types";

const USERNAME = /^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$/;

/** A strong random password that meets the server's rules (12+ chars, upper, lower, digit). */
function generatePassword() {
  const sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnopqrstuvwxyz", "23456789", "!@#$%*-_"];
  const all = sets.join("");
  const pick = (chars: string) => chars[crypto.getRandomValues(new Uint32Array(1))[0] % chars.length];
  const chars = [...sets.map(pick), ...Array.from({ length: 14 }, () => pick(all))];
  for (let i = chars.length - 1; i > 0; i--) {
    const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

function passwordProblem(p: string) {
  if (p.length < 12) return "At least 12 characters";
  if (p === p.toLowerCase() || p === p.toUpperCase() || !/\d/.test(p)) return "Needs upper and lower case letters and a digit";
  return null;
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied");
  } catch {
    toast.error("Couldn’t copy. Select the password and copy it manually.");
  }
}

function PasswordInput({ id, value, onChange }: { id: string; value: string; onChange: (v: string) => void }) {
  const problem = value ? passwordProblem(value) : null;
  return (
    <Field label="Password" htmlFor={id} error={problem ?? undefined} hint="Share it privately. They can change it after signing in.">
      <div className="flex gap-2">
        <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="new-password" spellCheck={false} className="font-mono" />
        <Button type="button" variant="outline" size="icon" aria-label="Generate a password" title="Generate" onClick={() => onChange(generatePassword())}>
          <RefreshCw />
        </Button>
        <Button type="button" variant="outline" size="icon" aria-label="Copy password" title="Copy" disabled={!value} onClick={() => copy(value)}>
          <Copy />
        </Button>
      </div>
    </Field>
  );
}

// --- create / edit ---------------------------------------------------------------------------------

type Draft = { full_name: string; email: string; username: string; role: Role; password: string; overrides: Record<string, boolean> };

function PermissionSwitches({ catalog, role, overrides, onChange }: {
  catalog: PermissionCatalog;
  role: Role;
  overrides: Record<string, boolean>;
  onChange: (o: Record<string, boolean>) => void;
}) {
  const defaults = new Set(catalog.roles.find((r) => r.key === role)?.permissions ?? []);
  const adjustable = catalog.permissions.filter((p) => p.adjustable);
  if (role !== "member")
    return (
      <p className="rounded-lg border bg-muted/30 px-3 py-2.5 text-sm text-muted-foreground">
        {role === "admin" ? "Administrators always have every permission." : "Viewers have read-only access."}
      </p>
    );
  return (
    <ul className="divide-y rounded-lg border">
      {adjustable.map((p) => {
        const on = overrides[p.key] ?? defaults.has(p.key);
        const changed = p.key in overrides && overrides[p.key] !== defaults.has(p.key);
        return (
          <li key={p.key} className="flex items-start justify-between gap-4 px-3 py-2.5">
            <label htmlFor={`perm-${p.key}`} className="grid gap-0.5 text-sm">
              <span className="font-medium">
                {p.label}
                {changed && <span className="ml-2 rounded bg-primary/10 px-1.5 py-px text-[10px] font-medium text-signal uppercase">Custom</span>}
              </span>
              <span className="text-xs text-muted-foreground">{p.description}</span>
            </label>
            <Switch
              id={`perm-${p.key}`}
              checked={on}
              onCheckedChange={(v) => {
                const next = { ...overrides };
                if (v === defaults.has(p.key)) delete next[p.key];
                else next[p.key] = v;
                onChange(next);
              }}
            />
          </li>
        );
      })}
    </ul>
  );
}

function UserDialog({ open, onOpenChange, user, catalog, isSelf }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  user: User | null; // null = create
  catalog: PermissionCatalog;
  isSelf: boolean;
}) {
  const qc = useQueryClient();
  const fresh = (): Draft => user
    ? { full_name: user.full_name, email: user.email, username: user.username ?? "", role: user.role, password: "", overrides: user.permission_overrides ?? {} }
    : { full_name: "", email: "", username: "", role: "member", password: generatePassword(), overrides: {} };
  const [d, setD] = React.useState<Draft>(fresh);
  const [key, setKey] = React.useState<string | null>(null);
  const openKey = open ? (user?.id ?? "new") : null;
  if (openKey !== key) {
    setKey(openKey);
    if (openKey) setD(fresh());
  }
  const usernameBad = !!d.username && !USERNAME.test(d.username);
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        full_name: d.full_name.trim(), email: d.email.trim(), role: d.role, permission_overrides: d.overrides,
      };
      if (d.username.trim()) body.username = d.username.trim();
      if (!user) return api<User>("/api/auth/users", { body: { ...body, password: d.password } });
      return api<User>(`/api/auth/users/${user.id}`, { method: "PATCH", body });
    },
    onSuccess: (u) => {
      toast.success(user ? "Changes saved" : `${memberName(u)} can now sign in`, {
        description: user ? undefined : `Sign-in: ${u.username ?? u.email}. Share the password privately.`,
      });
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["team"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(user ? "Couldn’t save" : "Couldn’t create the account", { description: errorMessage(e) }),
  });
  const valid = d.full_name.trim() && d.email.trim() && !usernameBad && (user || !passwordProblem(d.password));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{user ? `Edit ${memberName(user)}` : "Add team member"}</DialogTitle>
          <DialogDescription>
            {user ? "Changes apply on their next request; no need to sign them out." : "They get their own sign-in and work in the same shared CRM."}
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate(); }}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Full name" htmlFor="u-name" className="sm:col-span-2">
              <Input id="u-name" required autoFocus value={d.full_name} onChange={(e) => setD({ ...d, full_name: e.target.value })} autoComplete="off" />
            </Field>
            <Field label="Email" htmlFor="u-email">
              <Input id="u-email" type="email" required value={d.email} onChange={(e) => setD({ ...d, email: e.target.value })} autoComplete="off" />
            </Field>
            <Field label="Username (optional)" htmlFor="u-username" error={usernameBad ? "3–60 letters, digits, dots, dashes or underscores" : undefined}
              hint="They can sign in with this or their email.">
              <Input id="u-username" value={d.username} onChange={(e) => setD({ ...d, username: e.target.value })} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Role" htmlFor="u-role" className="sm:col-span-2"
              hint={catalog.roles.find((r) => r.key === d.role)?.description}>
              <NativeSelect id="u-role" className="w-full" value={d.role} disabled={isSelf}
                onChange={(e) => setD({ ...d, role: e.target.value as Role, overrides: {} })}>
                {catalog.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              </NativeSelect>
            </Field>
            {!user && <div className="sm:col-span-2"><PasswordInput id="u-password" value={d.password} onChange={(password) => setD({ ...d, password })} /></div>}
          </div>
          <section className="grid gap-2" aria-labelledby="u-perms">
            <h3 id="u-perms" className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Permissions</h3>
            <PermissionSwitches catalog={catalog} role={d.role} overrides={d.overrides} onChange={(overrides) => setD({ ...d, overrides })} />
          </section>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending && <Spinner className="text-primary-foreground" />} {user ? "Save changes" : "Create account"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- reset password / delete -----------------------------------------------------------------------

function ResetPasswordDialog({ user, onOpenChange }: { user: User | null; onOpenChange: (o: boolean) => void }) {
  const [password, setPassword] = React.useState("");
  const [forId, setForId] = React.useState<string | null>(null);
  if ((user?.id ?? null) !== forId) {
    setForId(user?.id ?? null);
    if (user) setPassword(generatePassword());
  }
  const reset = useMutation({
    mutationFn: () => api<{ message: string }>(`/api/auth/users/${user!.id}/password`, { body: { new_password: password } }),
    onSuccess: (r) => {
      toast.success(r.message);
      onOpenChange(false);
    },
    onError: (e) => toast.error("Couldn’t reset the password", { description: errorMessage(e) }),
  });
  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Reset password{user ? ` for ${memberName(user)}` : ""}</DialogTitle>
          <DialogDescription>They are signed out on every device and need the new password to get back in.</DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (!passwordProblem(password)) reset.mutate(); }}>
          <PasswordInput id="reset-password" value={password} onChange={setPassword} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!!passwordProblem(password) || reset.isPending}>
              {reset.isPending && <Spinner className="text-primary-foreground" />} Reset password
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({ user, users, onOpenChange }: { user: User | null; users: User[]; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [target, setTarget] = React.useState("");
  const del = useMutation({
    mutationFn: () => api<{ message: string }>(`/api/auth/users/${user!.id}`, { method: "DELETE", query: { reassign_to: target || null } }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["team"] });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      setTarget("");
      onOpenChange(false);
    },
    onError: (e) => toast.error("Couldn’t delete the account", { description: errorMessage(e) }),
  });
  const others = users.filter((u) => u.is_active && u.id !== user?.id);
  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete {user ? memberName(user) : "account"}?</DialogTitle>
          <DialogDescription>
            They are signed out and can never sign in again. Their name stays on notes, tasks and history. To keep the account
            for later, deactivate it instead.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); del.mutate(); }}>
          <Field label="Give their opportunities and open tasks to" htmlFor="del-target">
            <NativeSelect id="del-target" className="w-full" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Nobody (Unassigned)</option>
              {others.map((u) => <option key={u.id} value={u.id}>{memberName(u)}</option>)}
            </NativeSelect>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="destructive" disabled={del.isPending}>
              {del.isPending && <Spinner />} Delete account
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- page ------------------------------------------------------------------------------------------

function TeamManagement() {
  const qc = useQueryClient();
  const me = useMe().data?.user;
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/api/auth/users") });
  const catalog = useQuery({ queryKey: ["permission-catalog"], queryFn: () => api<PermissionCatalog>("/api/auth/permissions"), staleTime: Infinity });
  const [show, setShow] = React.useState<"all" | "active" | "inactive">("all");
  const [editing, setEditing] = React.useState<User | "new" | null>(null);
  const [resetting, setResetting] = React.useState<User | null>(null);
  const [deleting, setDeleting] = React.useState<User | null>(null);
  const [toggling, setToggling] = React.useState<User | null>(null);
  const setActive = useMutation({
    mutationFn: (u: User) => api<User>(`/api/auth/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } }),
    onSuccess: (u) => {
      toast.success(u.is_active ? `${memberName(u)} can sign in again` : `${memberName(u)} is deactivated and signed out`);
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["team"] });
      setToggling(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const all = users.data ?? [];
  const active = all.filter((u) => u.is_active);
  const rows = show === "all" ? all : show === "active" ? active : all.filter((u) => !u.is_active);
  return (
    <>
      <PageHeader
        title="Settings"
        description={users.data ? `${active.length} active · ${all.length - active.length} inactive` : "Accounts, roles and permissions."}
        actions={<Button onClick={() => setEditing("new")} disabled={!catalog.data}><UserPlus /> Add team member</Button>}
      />
      <SettingsNav />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Show accounts" className="inline-flex rounded-lg border p-0.5">
          {(["all", "active", "inactive"] as const).map((k) => (
            <button key={k} role="tab" type="button" aria-selected={show === k} onClick={() => setShow(k)}
              className={cn("rounded-md px-3 py-1 text-sm capitalize text-muted-foreground transition-colors hover:text-foreground",
                show === k && "bg-muted font-medium text-foreground")}>
              {k}
            </button>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">Everyone works in the same CRM with their own sign-in. There is no limit on accounts.</p>
      </div>
      {users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : users.isPending ? (
        <div className="grid gap-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : rows.length === 0 ? (
        <EmptyState icon={UsersRound} title={show === "inactive" ? "No inactive accounts" : "No accounts"} />
      ) : (
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead className="hidden md:table-cell">Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden lg:table-cell">Last sign-in</TableHead>
                <TableHead className="w-10"><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((u) => {
                const self = u.id === me?.id;
                const custom = Object.keys(u.permission_overrides ?? {}).length;
                return (
                  <TableRow key={u.id} className={cn(!u.is_active && "text-muted-foreground")}>
                    <TableCell className="max-w-64">
                      <div className="truncate font-medium">{u.full_name || "—"}{self && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(you)</span>}</div>
                      <div className="truncate text-xs text-muted-foreground">{u.username ? `@${u.username}` : <span className="md:hidden">{u.email}</span>}</div>
                    </TableCell>
                    <TableCell className="hidden max-w-64 truncate md:table-cell">{u.email}</TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">
                        {u.role === "admin" && <ShieldCheck className="size-3.5 text-signal" aria-hidden />}
                        {ROLE_LABEL[u.role] ?? u.role}
                      </span>
                      {custom > 0 && <div className="text-xs text-muted-foreground">{custom} custom permission{custom === 1 ? "" : "s"}</div>}
                    </TableCell>
                    <TableCell>
                      <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
                        u.is_active ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>
                        <span className={cn("size-1.5 rounded-full", u.is_active ? "bg-emerald-500" : "bg-muted-foreground")} aria-hidden />
                        {u.is_active ? "Active" : "Inactive"}
                      </span>
                    </TableCell>
                    <TableCell className="hidden lg:table-cell" title={dateTime(u.last_login_at)}>{u.last_login_at ? relTime(u.last_login_at) : "Never"}</TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${memberName(u)}`} />}>
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setEditing(u)}><Pencil /> Edit</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setResetting(u)}><KeyRound /> Reset password</DropdownMenuItem>
                          {!self && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => (u.is_active ? setToggling(u) : setActive.mutate(u))}>
                                <Power /> {u.is_active ? "Deactivate" : "Activate"}
                              </DropdownMenuItem>
                              <DropdownMenuItem variant="destructive" onClick={() => setDeleting(u)}><Trash2 /> Delete</DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      {catalog.data && (
        <UserDialog
          open={editing !== null}
          onOpenChange={(o) => !o && setEditing(null)}
          user={editing === "new" ? null : editing}
          catalog={catalog.data}
          isSelf={editing !== "new" && editing?.id === me?.id}
        />
      )}
      <ResetPasswordDialog user={resetting} onOpenChange={(o) => !o && setResetting(null)} />
      <DeleteDialog user={deleting} users={all} onOpenChange={(o) => !o && setDeleting(null)} />
      <ConfirmDialog
        open={!!toggling}
        onOpenChange={(o) => !o && setToggling(null)}
        title={`Deactivate ${toggling ? memberName(toggling) : ""}?`}
        description="They are signed out immediately and can’t sign in until you activate them again. Their opportunities stay assigned to them until you reassign them."
        confirmLabel="Deactivate"
        destructive
        pending={setActive.isPending}
        onConfirm={() => toggling && setActive.mutate(toggling)}
      />
    </>
  );
}

export default function TeamPage() {
  const isAdmin = useIsAdmin();
  if (!isAdmin)
    return (
      <>
        <PageHeader title="Settings" />
        <SettingsNav />
        <EmptyState icon={ShieldCheck} title="Administrators only" description="Ask an administrator to add or change team accounts." />
      </>
    );
  return <TeamManagement />;
}
