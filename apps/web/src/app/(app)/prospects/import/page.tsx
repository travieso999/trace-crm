"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/common";
import { ImportFlow } from "@/components/import-flow";

export default function ImportPage() {
  return (
    <>
      <Link href="/prospects" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Prospects
      </Link>
      <PageHeader title="Import prospects" description="Upload a spreadsheet, check the mapping, review a dry run, then import. Each upload is saved as a reusable list." />
      <ImportFlow />
    </>
  );
}
