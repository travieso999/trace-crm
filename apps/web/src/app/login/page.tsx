"use client";

import { type FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/common";
import { TraceLogo } from "@/components/brand";
import { ApiError, api, errorMessage } from "@/lib/api";
import { zodResolver } from "@/lib/zod-resolver";

const schema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
});
type Values = z.infer<typeof schema>;

type LoginResult = { mfa_required?: boolean; mfa_token?: string };

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [verifying, setVerifying] = useState(false);
  const { register, handleSubmit, formState } = useForm<Values>({ resolver: zodResolver(schema) });

  function signedIn() {
    qc.clear();
    const next = params.get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  }

  async function onSubmit(values: Values) {
    setError(null);
    try {
      const r = await api<LoginResult>("/api/auth/login", { body: values });
      if (r.mfa_required && r.mfa_token) setMfaToken(r.mfa_token);
      else signedIn();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function onCode(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setVerifying(true);
    try {
      await api("/api/auth/login/mfa", { body: { mfa_token: mfaToken, code } });
      signedIn();
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.message.startsWith("Sign-in expired")) setMfaToken(null);
    } finally {
      setVerifying(false);
    }
  }

  if (mfaToken)
    return (
      <form onSubmit={onCode} className="grid gap-4" noValidate>
        <Field label="Code from your authenticator app" htmlFor="code" hint="Lost your phone? Enter one of your recovery codes instead.">
          <Input id="code" autoComplete="one-time-code" autoFocus value={code} onChange={(e) => setCode(e.target.value.trim())} />
        </Field>
        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" size="lg" disabled={code.length < 6 || verifying}>
          {verifying && <Loader2 className="animate-spin" />}
          Verify
        </Button>
        <Button type="button" variant="ghost" onClick={() => { setMfaToken(null); setCode(""); setError(null); }}>
          Back
        </Button>
      </form>
    );

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="grid gap-4" noValidate>
      <Field label="Email" htmlFor="email" error={formState.errors.email?.message}>
        <Input id="email" type="email" autoComplete="username" autoFocus {...register("email")} aria-invalid={!!formState.errors.email} />
      </Field>
      <Field label="Password" htmlFor="password" error={formState.errors.password?.message}>
        <Input id="password" type="password" autoComplete="current-password" {...register("password")} aria-invalid={!!formState.errors.password} />
      </Field>
      {error && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" size="lg" disabled={formState.isSubmitting}>
        {formState.isSubmitting && <Loader2 className="animate-spin" />}
        Sign in
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 bg-background px-4 py-10">
      <TraceLogo className="h-14 sm:h-16" title="Trace Studio" />
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-lg">Sign in to Trace CRM</CardTitle>
          <CardDescription>Internal access only. Accounts are created by an administrator.</CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense>
            <LoginForm />
          </Suspense>
        </CardContent>
      </Card>
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Trace Studio · Internal</p>
    </div>
  );
}
