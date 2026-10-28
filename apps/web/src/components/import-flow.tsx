"use client";

/** Spreadsheet import: file → column mapping → dry run → import. Every import is saved as a named, reusable
 * list; with `campaignId` the whole list (including prospects already in the CRM) also joins that campaign. */

import * as React from "react";
import { cn } from "cn";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, CircleAlert, FileUp, ListChecks, Loader2, Send } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Field, NativeSelect } from "@/components/common";
import { EnrollDialog } from "@/components/enroll-dialog";
import { api, errorMessage } from "@/lib/api";
import { dateTime, humanize, num } from "@/lib/format";

type Preview = {
  headers: string[];
  sample_rows: string[][];
  total_rows: number;
  suggested_mapping: Record<string, string>;
  fields: string[];
  format: "csv" | "xlsx";
  sheet: string | null;
  previous_import: { at: string; filename: string | null; created: number | null } | null;
};

type RowStatus = "new" | "update" | "existing_skipped" | "duplicate_in_file" | "suppressed" | "invalid_email" | "missing_email" | "error";

type ReportRow = {
  row: number;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  source_name: string | null;
  status: RowStatus;
  warnings: string[];
};

type Result = {
  created: number;
  updated: number;
  skipped_duplicates: number;
  skipped_suppressed: number;
  invalid: number;
  missing_fields: number;
  errors: { row: number; email: string | null; error: string }[];
  rows: ReportRow[];
  rows_truncated: boolean;
  contact_ids: string[];
  previous_import: Preview["previous_import"];
  dry_run: boolean;
  batch_id: string | null;
  list_name: string | null;
  list_contacts: number;
  enrollment: { enrolled: number; skipped_existing: number; skipped_suppressed: number; skipped_invalid: number } | null;
};

export type ImportResult = Result;

/** "texas_bail-bonds.xlsx" → "Texas Bail Bonds" (the server applies the same default). */
export function listNameFromFile(name: string) {
  return name
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w === w.toLowerCase() ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

const CUSTOM = "__custom__";

const FIELD_LABEL: Record<string, string> = {
  full_name: "Full name → split into first / last",
  company_name: "Company / agency name",
};

const STATUS: Record<RowStatus, { label: string; tone: string }> = {
  new: { label: "Ready (new prospect)", tone: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400" },
  update: { label: "Updates existing prospect", tone: "bg-sky-500/12 text-sky-700 dark:text-sky-400" },
  existing_skipped: { label: "Already in Prospects (skipped)", tone: "bg-muted text-muted-foreground" },
  duplicate_in_file: { label: "Duplicate in file (skipped)", tone: "bg-muted text-muted-foreground" },
  suppressed: { label: "Suppressed (skipped)", tone: "bg-red-500/12 text-red-700 dark:text-red-400" },
  invalid_email: { label: "Invalid email", tone: "bg-red-500/12 text-red-700 dark:text-red-400" },
  missing_email: { label: "Missing email", tone: "bg-red-500/12 text-red-700 dark:text-red-400" },
  error: { label: "Error", tone: "bg-red-500/12 text-red-700 dark:text-red-400" },
};

const WARNING: Record<string, string> = {
  missing_first_name: "No first name: {{first_name}} can’t be used",
  missing_company_name: "No company name: {{company_name}} can’t be used",
  name_split: "Name split from the name column",
  single_name: "Only one name given: no last name added",
  name_looks_like_business: "Name column looks like a business: not used as a person’s name",
  name_not_parsed: "Name couldn’t be split safely: left blank",
};

/** Header → snake_case custom field name accepted by the API ([a-z][a-z0-9_]{0,49}). */
function customName(header: string) {
  const s = header.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^[^a-z]+|_+$/g, "");
  return (s || "field").slice(0, 50);
}

const SKIPPED: RowStatus[] = ["existing_skipped", "duplicate_in_file", "suppressed"];
const PROBLEM: RowStatus[] = ["invalid_email", "missing_email", "error"];

function Report({ r }: { r: Result }) {
  const [view, setView] = React.useState<"all" | "ready" | "attention" | "skipped" | "invalid">("all");
  const counts = {
    ready: r.rows.filter((x) => (x.status === "new" || x.status === "update") && !x.warnings.some((w) => w.startsWith("missing_"))).length,
    attention: r.rows.filter((x) => (x.status === "new" || x.status === "update") && x.warnings.some((w) => w.startsWith("missing_"))).length,
    skipped: r.rows.filter((x) => SKIPPED.includes(x.status)).length,
    invalid: r.rows.filter((x) => PROBLEM.includes(x.status)).length,
  };
  const rows = r.rows.filter((x) => {
    const ok = x.status === "new" || x.status === "update";
    const missing = x.warnings.some((w) => w.startsWith("missing_"));
    if (view === "ready") return ok && !missing;
    if (view === "attention") return ok && missing;
    if (view === "skipped") return SKIPPED.includes(x.status);
    if (view === "invalid") return PROBLEM.includes(x.status);
    return true;
  });
  const stats: [string, number, string][] = [
    [r.dry_run ? "Ready to import" : "Imported", counts.ready, "Have an email, first name and company"],
    ["Missing fields", counts.attention, "Imported, but {{first_name}} or {{company_name}} would be blank — campaigns skip them"],
    ["Skipped", counts.skipped, "Already in Prospects, duplicated in the file, or suppressed"],
    ["Invalid / missing email", counts.invalid, "Not imported"],
  ];
  return (
    <div className="grid gap-3">
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map(([label, n, hint]) => (
          <div key={label} className="rounded-lg border p-3" title={hint}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{num(n)}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        {r.dry_run ? "Would create" : "Created"} {num(r.created)} · {r.dry_run ? "would update" : "updated"} {num(r.updated)} existing.
        Names and companies are only taken from your file — nothing is guessed.
      </p>
      <NativeSelect aria-label="Show rows" className="w-fit" value={view} onChange={(e) => setView(e.target.value as typeof view)}>
        <option value="all">All rows ({num(r.rows.length)})</option>
        <option value="ready">Ready ({num(counts.ready)})</option>
        <option value="attention">Missing fields ({num(counts.attention)})</option>
        <option value="skipped">Skipped ({num(counts.skipped)})</option>
        <option value="invalid">Invalid / missing email ({num(counts.invalid)})</option>
      </NativeSelect>
      <div className="max-h-[28rem] overflow-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8 sm:w-14">Row</TableHead>
              <TableHead>Email</TableHead>
              <TableHead className="hidden sm:table-cell">First</TableHead>
              <TableHead className="hidden sm:table-cell">Last</TableHead>
              <TableHead className="hidden md:table-cell">Company</TableHead>
              <TableHead>Result</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((x) => (
              <TableRow key={x.row}>
                <TableCell className="tabular-nums text-muted-foreground">{x.row}</TableCell>
                <TableCell>
                  <div className="max-w-28 truncate sm:max-w-56" title={x.email ?? undefined}>
                    {x.email ?? <span className="text-muted-foreground">—</span>}
                  </div>
                </TableCell>
                <TableCell className="hidden max-w-32 truncate sm:table-cell" title={x.source_name ? `From “${x.source_name}”` : undefined}>
                  {x.first_name ?? <span className="text-muted-foreground">—</span>}
                </TableCell>
                <TableCell className="hidden max-w-32 truncate sm:table-cell">{x.last_name ?? <span className="text-muted-foreground">—</span>}</TableCell>
                <TableCell className="hidden max-w-48 truncate md:table-cell">{x.company_name ?? <span className="text-muted-foreground">—</span>}</TableCell>
                <TableCell className="min-w-36 whitespace-normal">
                  {(() => {
                    const incomplete = (x.status === "new" || x.status === "update") && x.warnings.some((w) => w.startsWith("missing_"));
                    return (
                      <span
                        className={cn(
                          "inline-flex min-h-5 items-center rounded-md px-1.5 py-0.5 text-xs font-medium",
                          incomplete ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" : STATUS[x.status].tone,
                        )}
                      >
                        {incomplete ? (x.status === "new" ? "New prospect — missing fields" : "Update — missing fields") : STATUS[x.status].label}
                      </span>
                    );
                  })()}
                  {x.warnings.length > 0 && (
                    <ul className="mt-0.5 text-[11px] text-muted-foreground">
                      {x.warnings.map((w) => (
                        <li key={w} className={w.startsWith("missing_") ? "text-amber-700 dark:text-amber-400" : undefined}>
                          {WARNING[w] ?? humanize(w)}
                          {w === "name_split" && x.source_name ? ` (“${x.source_name}”)` : ""}
                        </li>
                      ))}
                    </ul>
                  )}
                  {(x.status === "invalid_email" || x.status === "error") && (
                    <div className="mt-0.5 text-[11px] text-destructive">
                      {r.errors.find((e) => e.row === x.row)?.error}
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {r.rows_truncated && <p className="text-xs text-muted-foreground">Showing the first 2,000 rows of the report.</p>}
    </div>
  );
}

export function ImportFlow({ campaignId, campaignName, onImported }: { campaignId?: string; campaignName?: string; onImported?: (r: Result) => void }) {
  const qc = useQueryClient();
  const [listName, setListName] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [preview, setPreview] = React.useState<Preview | null>(null);
  const [mapping, setMapping] = React.useState<Record<string, string>>({});
  const [duplicates, setDuplicates] = React.useState<"skip" | "update">("skip");
  const [tags, setTags] = React.useState("");
  const [source, setSource] = React.useState("");
  const [busy, setBusy] = React.useState<"preview" | "dry" | "import" | null>(null);
  const [result, setResult] = React.useState<Result | null>(null);
  const [enrolling, setEnrolling] = React.useState(false);

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
      setListName(listNameFromFile(f.name));
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
  const changedMapping = !!preview && JSON.stringify(mapping) !== JSON.stringify(Object.fromEntries(preview.headers.map((h) => [h, preview.suggested_mapping[h] ?? ""])));

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
          list_name: listName.trim() || null,
          campaign_id: campaignId ?? null,
          dry_run: dryRun,
        }),
      );
      const r = await api<Result>("/api/contacts/import", { form });
      setResult(r);
      if (!dryRun) {
        toast.success(`Saved as list “${r.list_name}” · ${num(r.list_contacts)} prospects`, {
          description: r.enrollment ? `${num(r.enrollment.enrolled)} added to ${campaignName ?? "the campaign"}` : `${num(r.created)} new, ${num(r.updated)} updated`,
        });
        qc.invalidateQueries({ queryKey: ["import-lists"] });
        if (campaignId) {
          for (const k of ["campaign", "campaign-leads", "campaign-preflight", "campaign-summary"]) qc.invalidateQueries({ queryKey: [k] });
        }
        onImported?.(r);
        qc.invalidateQueries({ queryKey: ["contacts"] });
        qc.invalidateQueries({ queryKey: ["contact-tags"] });
        qc.invalidateQueries({ queryKey: ["companies"] });
        qc.invalidateQueries({ queryKey: ["dashboard"] });
      }
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const done = result && !result.dry_run;
  const canRun = !busy && hasEmail && dupTargets.length === 0;

  return (
    <>
      <Card className="mb-4">
        <CardHeader>
          <CardTitle>1. Choose a file</CardTitle>
          <CardDescription>CSV or Excel (.xlsx) with a header row. The first sheet with data is used. Suppressed addresses are always skipped.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed px-4 py-6 text-sm hover:bg-muted/50 has-focus-visible:ring-3 has-focus-visible:ring-ring/50">
            {busy === "preview" ? <Loader2 className="size-5 animate-spin text-muted-foreground" /> : <FileUp className="size-5 text-muted-foreground" />}
            <span>
              {file ? (
                <>
                  <span className="font-medium">{file.name}</span>
                  {preview && (
                    <span className="text-muted-foreground">
                      {" "}
                      · {num(preview.total_rows)} rows{preview.sheet ? ` · sheet “${preview.sheet}”` : ""}
                    </span>
                  )}
                </>
              ) : (
                "Click to choose a .csv or .xlsx file"
              )}
            </span>
            <input
              type="file"
              accept=".csv,.tsv,.txt,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="sr-only"
              disabled={!!busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) loadPreview(f);
                e.target.value = "";
              }}
            />
          </label>
          {preview?.previous_import && (
            <p className="flex items-start gap-2 rounded-md bg-amber-500/12 px-3 py-2 text-sm text-amber-800 dark:text-amber-300" role="status">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                This exact file was already imported{preview.previous_import.filename ? ` as “${preview.previous_import.filename}”` : ""} on{" "}
                {dateTime(preview.previous_import.at)}. Prospects already in your list are skipped, so importing again only adds rows that weren’t
                saved before.
              </span>
            </p>
          )}
        </CardContent>
      </Card>

      {preview && !done && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle>2. Check the column mapping</CardTitle>
            <CardDescription>
              Columns were matched automatically — correct any that are wrong. Full names are split into first and last name only when it’s clearly a
              person; the original value is kept on the prospect as <code>source_name</code>.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Spreadsheet column</TableHead>
                    <TableHead>Import as</TableHead>
                    <TableHead className="hidden md:table-cell">Sample values</TableHead>
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
                                {FIELD_LABEL[f] ?? humanize(f)}
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

            <Field
              label="List name"
              htmlFor="list-name"
              hint={campaignId ? `Saved under Imported Lists and added to ${campaignName ?? "this campaign"} as a whole.` : "Saved under Imported Lists so you can reuse the whole group in campaigns."}
            >
              <Input id="list-name" maxLength={200} value={listName} onChange={(e) => setListName(e.target.value)} placeholder="Texas Bail Bonds" />
            </Field>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Prospects already in your list" htmlFor="dups">
                <NativeSelect id="dups" value={duplicates} onChange={(e) => setDuplicates(e.target.value as "skip" | "update")}>
                  <option value="skip">Skip them (recommended)</option>
                  <option value="update">Fill in their blank fields</option>
                </NativeSelect>
              </Field>
              <Field label="Tags" htmlFor="tags" hint="Added to every imported prospect — handy for finding this batch later">
                <Input id="tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="e.g. tx-bail-bonds, oct-list" />
              </Field>
              <Field label="Source" htmlFor="source">
                <Input id="source" value={source} onChange={(e) => setSource(e.target.value)} />
              </Field>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              {changedMapping && result?.dry_run && <span className="mr-auto text-xs text-muted-foreground">Mapping changed — run the dry run again.</span>}
              <Button variant={result?.dry_run ? "outline" : "default"} disabled={!canRun} onClick={() => run(true)}>
                {busy === "dry" && <Loader2 className="animate-spin" />}
                Dry run (nothing saved)
              </Button>
              <Button variant={result?.dry_run ? "default" : "outline"} disabled={!canRun} onClick={() => run(false)}>
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
              {result.dry_run ? "3. Dry run report — nothing was saved" : "Import complete"}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Report r={result} />
            {done && result.batch_id && (
              <p className="flex items-start gap-2 rounded-md bg-emerald-500/10 px-3 py-2 text-sm" role="status">
                <ListChecks className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                <span>
                  Saved as the list <span className="font-medium">“{result.list_name}”</span> with {num(result.list_contacts)} prospect
                  {result.list_contacts === 1 ? "" : "s"} (new and already known).
                  {result.enrollment &&
                    ` ${num(result.enrollment.enrolled)} added to ${campaignName ?? "the campaign"}${
                      result.enrollment.skipped_suppressed + result.enrollment.skipped_invalid
                        ? `; ${num(result.enrollment.skipped_suppressed + result.enrollment.skipped_invalid)} skipped (suppressed, invalid or closed)`
                        : ""
                    }.`}
                </span>
              </p>
            )}
            {done && !campaignId && (
              <div className="flex flex-wrap gap-2">
                {result.contact_ids.length > 0 && (
                  <Button onClick={() => setEnrolling(true)}>
                    <Send /> Add {num(result.contact_ids.length)} imported prospect{result.contact_ids.length === 1 ? "" : "s"} to a campaign…
                  </Button>
                )}
                {result.batch_id && (
                  <Link href={`/prospects?batch_id=${result.batch_id}`} className={cn(buttonVariants({ variant: "outline" }))}>
                    View this list
                  </Link>
                )}
                <Link href="/lists" className={cn(buttonVariants({ variant: "outline" }))}>
                  Imported lists
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
      {result && !result.dry_run && !campaignId && <EnrollDialog contactIds={result.contact_ids} open={enrolling} onOpenChange={setEnrolling} />}
    </>
  );
}
