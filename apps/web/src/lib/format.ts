import { formatDistanceToNowStrict, format } from "date-fns";

export function fullName(c: { first_name?: string | null; last_name?: string | null; email?: string }) {
  return [c.first_name, c.last_name].filter(Boolean).join(" ") || c.email || "—";
}

export function relTime(iso: string | null | undefined) {
  if (!iso) return "—";
  return formatDistanceToNowStrict(new Date(iso), { addSuffix: true });
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return "—";
  return format(new Date(iso), "MMM d, yyyy h:mm a");
}

export function shortDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return format(new Date(iso), "MMM d");
}

export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();
export const pct = (n: number | null | undefined) => `${(n ?? 0).toFixed(1)}%`;

// Display names that differ from the stored value (prospect statuses are stored as lead statuses)
const LABELS: Record<string, string> = { new_lead: "New" };

export function humanize(s: string) {
  return LABELS[s] ?? s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const COMMON_TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Puerto_Rico",
  "UTC",
];
