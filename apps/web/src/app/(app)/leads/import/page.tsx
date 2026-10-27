"use client";

import * as React from "react";
import { cn } from "cn";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, CheckCircle2, FileUp, Loader2 } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Field, NativeSelect, PageHeader } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { humanize, num } from "@/lib/format";

type Preview = {
  headers: string[];
  sample_rows: string[][];
  total_rows: number;
  suggested_mapping: Record<string, string>;
  fields: string[];
};

type Result = {
  created: number;
  updated: number;
  skipped_duplicates: number;
  skipped_suppressed: number;
  invalid: number;
  errors: { row: number; email: string | null; error: string }[];
  dry_run: boolean;
};

const CUSTOM = "__custom__";

/** Header → snake_case custom field name accepted by the API ([a-z][a-z0-9_]{0,49}). */
function customName(header: string) {
  const s = header.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[^a-z]+|_+$/g, "");
  return (s || "field").slice(0, 50);
}

function ResultSummary({ r }: { r: Result }) {
  const stats: [string, number][] = [
    [r.dry_run ? "Would create" : "Created", r.created],
    [r.dry_run ? "Would update" : "Updated", r.updated],
    ["Duplicates skipped", r.skipped_duplicates],
    ["Suppressed skipped", r.skipped_suppressed],
    ["Invalid rows", r.invalid],
  ];
  return (
    <div className="grid gap-3">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stats.map(([label, n]) => (
          <div key={label} className="rounded-lg border p-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{num(n)}</dd>
          </div>
        ))}
      </dl>
      {r.errors.length > 0 && (
        <div className="max-h-64 overflow-y-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Row</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Problem</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.errors.map((e, i) => (
                <TableRow key={i}>
                  <TableCell className="tabular-nums">{e.row}</TableCell>
                  <TableCell>{e.email ?? "—"}</TableCell>
                  <TableCell className="text-destructive">{e.error}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

export default function ImportPage() {
  const qc = useQueryClient();
  const [file, setFile] = React.useState<File | null>(null);
  const [preview, setPreview] = React.useState<Preview | null>(null);
  const [mapping, setMapping] = React.useState<Record<string, string>>({});
  const [duplicates, setDuplicates] = React.useState<"skip" | "update">("skip");
  const [tags, setTags] = React.useState("");
  const [source, setSource] = React.useState("");
  const [busy, setBusy] = React.useState<"preview" | "dry" | "import" | null>(null);
  const [result, setResult] = React.useState<Result | null>(null);

  async function loadPreview(f: File) {
    setFile(f);
    setResult(null);
    setBusy("preview");
    try {
      const form = new FormData();
      form.append("file", f);
      const p = await api<Preview>("/api/contacts/import/preview", { form });
      setPreview(p);
      setMapping(Object.fromEntries(p.headers.map((h) => [h, p.suggested_mapping[h] ?? ""])));
      setSource(f.name.replace(/\.[^.]+$/, ""));
    } catch (e) {
      setPreview(null);
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const mappedTargets = Object.values(mapping).filter(Boolean);
  const dupTargets = mappedTargets.filter((t, i) => mappedTargets.indexOf(t) !== i);
  const hasEmail = mappedTargets.includes("email");

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(dryRun ? "dry" : "import");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append(
        "options",
        JSON.stringify({
          mapping,
          duplicate_strategy: duplicates,
          tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
          source: source.trim() || null,
          dry_run: dryRun,
        }),
      );
      const r = await api<Result>("/api/contacts/import", { form });
      setResult(r);
      if (!dryRun) {
        toast.success(`Imported: ${num(r.created)} created, ${num(r.updated)} updated`);
        qc.invalidateQueries({ queryKey: ["contacts"] });
        qc.invalidateQueries({ queryKey: ["contact-tags"] });
        qc.invalidateQueries({ queryKey: ["dashboard"] });
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const done = result && !result.dry_run;

  return (
    <>
      <Link href="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Leads
      </Link>
      <PageHeader title="Import leads" description="Upload a CSV, map its columns, check with a dry run, then import." />

      <Card className="mb-4">
        <CardHeader>
          <CardTitle>1. Choose a file</CardTitle>
          <CardDescription>CSV with a header row. Suppressed addresses are always skipped.</CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed px-4 py-6 text-sm hover:bg-muted/50 has-focus-visible:ring-3 has-focus-visible:ring-ring/50">
            {busy === "preview" ? <Loader2 className="size-5 animate-spin text-muted-foreground" /> : <FileUp className="size-5 text-muted-foreground" />}
            <span>
              {file ? (
                <>
                  <span className="font-medium">{file.name}</span>
                  {preview && <span className="text-muted-foreground"> · {num(preview.total_rows)} rows</span>}
                </>
              ) : (
                "Click to choose a .csv file"
              )}
            </span>
            <input
              type="file"
              accept=".csv,.tsv,.txt,text/csv"
              className="sr-only"
              disabled={!!busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) loadPreview(f);
                e.target.value = "";
              }}
            />
          </label>
        </CardContent>
      </Card>

      {preview && !done && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle>2. Map columns</CardTitle>
            <CardDescription>Columns set to “Skip” are ignored. An email column is required.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>CSV column</TableHead>
                    <TableHead>Import as</TableHead>
                    <TableHead className="hidden md:table-cell">Sample</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.headers.map((h, col) => {
                    const v = mapping[h] ?? "";
                    const isCustom = v.startsWith("custom:");
                    return (
                      <TableRow key={h}>
                        <TableCell className="font-medium">{h}</TableCell>
                        <TableCell>
                          <NativeSelect
                            aria-label={`Import ${h} as`}
                            value={isCustom ? CUSTOM : v}
                            aria-invalid={dupTargets.includes(v)}
                            onChange={(e) =>
                              setMapping({ ...mapping, [h]: e.target.value === CUSTOM ? `custom:${customName(h)}` : e.target.value })
                            }
                          >
                            <option value="">Skip</option>
                            {preview.fields.map((f) => (
                              <option key={f} value={f}>
                                {humanize(f)}
                              </option>
                            ))}
                            <option value={CUSTOM}>Custom field…</option>
                          </NativeSelect>
                          {isCustom && <span className="ml-2 font-mono text-xs text-muted-foreground">{v.slice(7)}</span>}
                        </TableCell>
                        <TableCell className="hidden max-w-72 truncate text-muted-foreground md:table-cell">
                          {preview.sample_rows
                            .slice(0, 3)
                            .map((r) => r[col])
                            .filter(Boolean)
                            .join(" · ") || "—"}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            {dupTargets.length > 0 && (
              <p className="text-sm text-destructive" role="alert">
                Each field can be mapped once: {[...new Set(dupTargets)].map(humanize).join(", ")}
              </p>
            )}
            {!hasEmail && (
              <p className="text-sm text-destructive" role="alert">
                Map one column to Email.
              </p>
            )}

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Existing leads" htmlFor="dups">
                <NativeSelect id="dups" value={duplicates} onChange={(e) => setDuplicates(e.target.value as "skip" | "update")}>
                  <option value="skip">Skip duplicates</option>
                  <option value="update">Update with CSV values</option>
                </NativeSelect>
              </Field>
              <Field label="Tags" htmlFor="tags" hint="Applied to every imported lead">
                <Input id="tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="e.g. dentists, q4" />
              </Field>
              <Field label="Source" htmlFor="source">
                <Input id="source" value={source} onChange={(e) => setSource(e.target.value)} />
              </Field>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" disabled={!!busy || !hasEmail || dupTargets.length > 0} onClick={() => run(true)}>
                {busy === "dry" && <Loader2 className="animate-spin" />}
                Dry run
              </Button>
              <Button disabled={!!busy || !hasEmail || dupTargets.length > 0} onClick={() => run(false)}>
                {busy === "import" && <Loader2 className="animate-spin" />}
                Import {num(preview.total_rows)} rows
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {result && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {done && <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden />}
              {result.dry_run ? "Dry run — nothing was saved" : "Import complete"}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <ResultSummary r={result} />
            {done && (
              <div className="flex gap-2">
                <Link href="/leads" className={cn(buttonVariants())}>
                  View leads
                </Link>
                <Button
                  variant="outline"
                  onClick={() => {
                    setFile(null);
                    setPreview(null);
                    setResult(null);
                  }}
                >
                  Import another file
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
