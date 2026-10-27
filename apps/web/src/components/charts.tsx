"use client";

import * as React from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { shortDate } from "@/lib/format";

// Series colors come from --series-N (validated light + dark steps in globals.css). Text always uses ink tokens.
export type Series = { key: string; label: string; color: string };

const AXIS = { stroke: "var(--chart-axis)", fontSize: 11, tickLine: false, axisLine: false } as const;

type TipProps = {
  active?: boolean;
  label?: string;
  payload?: { dataKey?: string | number; value?: number; payload?: Record<string, unknown> }[];
  series: Series[];
  extra?: (row: Record<string, unknown>) => React.ReactNode;
};

function ChartTooltip({ active, label, payload, series, extra }: TipProps) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload ?? {};
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <div className="mb-1 font-medium">{shortDate(String(label))}</div>
      {series.map((s) => (
        <div key={s.key} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: s.color }} aria-hidden />
          <span className="text-muted-foreground">{s.label}</span>
          <span className="ml-auto pl-4 font-medium tabular-nums">{Number(row[s.key] ?? 0).toLocaleString()}</span>
        </div>
      ))}
      {extra?.(row)}
    </div>
  );
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function NoData({ height }: { height: number }) {
  return (
    <div style={{ height }} className="flex items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
      No data in this period yet
    </div>
  );
}

const isEmpty = (data: Record<string, unknown>[], keys: string[]) => data.every((row) => keys.every((k) => !Number(row[k])));

/** Daily bars for a single measure; `extra` adds rows (e.g. failures) to the tooltip. */
export function DailyBars({
  data,
  series,
  extra,
  height = 220,
}: {
  data: Record<string, unknown>[];
  series: Series;
  extra?: TipProps["extra"];
  height?: number;
}) {
  if (isEmpty(data, [series.key])) return <NoData height={height} />;
  return (
    <div style={{ height }} role="img" aria-label={`${series.label} per day`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -16 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} minTickGap={24} />
          <YAxis {...AXIS} allowDecimals={false} width={44} />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.6 }}
            content={(p) => <ChartTooltip {...(p as unknown as TipProps)} series={[series]} extra={extra} />}
          />
          <Bar dataKey={series.key} fill={series.color} radius={[4, 4, 0, 0]} maxBarSize={18} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Up to four daily trend lines with a legend, an end-of-line direct label, and a crosshair tooltip. */
export function TrendLines({ data, series, height = 220 }: { data: Record<string, unknown>[]; series: Series[]; height?: number }) {
  if (isEmpty(data, series.map((s) => s.key))) return <NoData height={height} />;
  const last = data.length - 1;
  // End labels collide when line ends sit within ~8% of the y-range; the legend carries identity then.
  const ends = series.map((s) => Number(data[last]?.[s.key] ?? 0)).sort((a, b) => a - b);
  const max = Math.max(1, ...data.flatMap((row) => series.map((s) => Number(row[s.key] ?? 0))));
  const labelEnds = ends.every((v, i) => i === 0 || v - ends[i - 1] > max * 0.08);
  return (
    <div>
      <Legend series={series} />
      <div style={{ height }} role="img" aria-label={series.map((s) => s.label).join(" and ") + " per day"}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: labelEnds ? 56 : 8, bottom: 0, left: -16 }}>
            <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
            <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} minTickGap={24} />
            <YAxis {...AXIS} allowDecimals={false} width={44} />
            <Tooltip
              cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3" }}
              content={(p) => <ChartTooltip {...(p as unknown as TipProps)} series={series} />}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
                isAnimationActive={false}
              >
                <LabelList
                  dataKey={s.key}
                  content={({ index, x, y }) =>
                    labelEnds && index === last ? (
                      <text x={Number(x) + 6} y={Number(y)} dy={4} fontSize={11} fill="var(--muted-foreground)">
                        {s.label}
                      </text>
                    ) : null
                  }
                />
              </Line>
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
