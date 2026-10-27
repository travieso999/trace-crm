"use client";

import { cn } from "cn";
import { Input } from "@/components/ui/input";
import { Field, NativeSelect } from "@/components/common";
import { COMMON_TIMEZONES, WEEKDAYS } from "@/lib/format";
import type { Schedule } from "@/lib/types";

/** Sending window editor: weekdays (Mon = 0), start/end time and time zone. */
export function ScheduleEditor({
  value,
  onChange,
  disabled,
  showRecipientTz,
}: {
  value: Schedule;
  onChange: (s: Schedule) => void;
  disabled?: boolean;
  showRecipientTz?: boolean;
}) {
  const days = new Set(value.days);
  function toggle(d: number) {
    const next = new Set(days);
    if (next.has(d)) next.delete(d);
    else next.add(d);
    if (next.size) onChange({ ...value, days: [...next].sort() });
  }
  const zones = COMMON_TIMEZONES.includes(value.timezone) ? COMMON_TIMEZONES : [value.timezone, ...COMMON_TIMEZONES];
  return (
    <div className="grid gap-3">
      <fieldset className="grid gap-1.5" disabled={disabled}>
        <legend className="mb-1.5 text-sm font-medium">Sending days</legend>
        <div className="flex flex-wrap gap-1">
          {WEEKDAYS.map((label, d) => (
            <button
              key={label}
              type="button"
              aria-pressed={days.has(d)}
              onClick={() => toggle(d)}
              className={cn(
                "h-7 min-w-11 rounded-md border px-2 text-xs font-medium outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                days.has(d) ? "border-primary bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="From" htmlFor="sched-start">
          <Input id="sched-start" type="time" value={value.start} disabled={disabled} onChange={(e) => onChange({ ...value, start: e.target.value })} />
        </Field>
        <Field label="Until" htmlFor="sched-end">
          <Input id="sched-end" type="time" value={value.end} disabled={disabled} onChange={(e) => onChange({ ...value, end: e.target.value })} />
        </Field>
        <Field label="Time zone" htmlFor="sched-tz" className="col-span-2 sm:col-span-1">
          <NativeSelect id="sched-tz" value={value.timezone} disabled={disabled} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
      {showRecipientTz && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--primary)]"
            checked={!!value.use_recipient_timezone}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, use_recipient_timezone: e.target.checked })}
          />
          <span>
            Use each prospect’s time zone
            <span className="block text-xs text-muted-foreground">Sends land inside these hours in the recipient’s local time when it’s known.</span>
          </span>
        </label>
      )}
    </div>
  );
}

export function scheduleSummary(s: Schedule | undefined) {
  if (!s) return "—";
  const days = s.days.length === 7 ? "Every day" : s.days.join(",") === "0,1,2,3,4" ? "Mon–Fri" : s.days.map((d) => WEEKDAYS[d]).join(", ");
  return `${days} · ${s.start}–${s.end} ${s.timezone.split("/").pop()?.replace(/_/g, " ")}`;
}
