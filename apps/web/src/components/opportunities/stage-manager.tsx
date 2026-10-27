"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Plus, Trash2 } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Field, NativeSelect } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { humanize, num } from "@/lib/format";
import { LEAD_STATUSES, type Stage } from "@/lib/types";

export const STAGE_COLORS = ["#8a8579", "#5b8def", "#a78bfa", "#f5a524", "#22b8cf", "#ff5a33", "#2fb37a", "#e5484d", "#d6409f", "#6b6b6b"];

function ColorPicker({ value, onChange, label }: { value: string; onChange: (c: string) => void; label: string }) {
  return (
    <Popover>
      <PopoverTrigger
        render={<button type="button" aria-label={label} className="size-6 shrink-0 rounded-full border-2 border-background ring-1 ring-border outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-ring" style={{ background: value }} />}
      />
      <PopoverContent className="w-auto" align="start">
        <div className="grid grid-cols-5 gap-2" role="listbox" aria-label="Stage color">
          {STAGE_COLORS.map((c) => (
            <button key={c} type="button" role="option" aria-selected={c === value} aria-label={c} onClick={() => onChange(c)}
              className={cn("size-7 rounded-full outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-ring", c === value && "ring-2 ring-foreground ring-offset-2 ring-offset-popover")}
              style={{ background: c }} />
          ))}
        </div>
        <label className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
          Custom
          <input type="color" value={value} onChange={(e) => onChange(e.target.value)} className="h-7 w-12 cursor-pointer rounded border bg-transparent" />
        </label>
      </PopoverContent>
    </Popover>
  );
}

function StageRow({ s, stages, onSave, onDelete }: {
  s: Stage;
  stages: Stage[];
  onSave: (id: string, body: Partial<Pick<Stage, "name" | "color" | "lead_status">>) => void;
  onDelete: (s: Stage) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: s.id });
  const [name, setName] = React.useState(s.name);
  const [prev, setPrev] = React.useState(s.name);
  if (prev !== s.name) {
    setPrev(s.name);
    setName(s.name);
  }
  const usedStatuses = new Set(stages.filter((x) => x.id !== s.id && x.lead_status).map((x) => x.lead_status));
  const commit = () => {
    const v = name.trim();
    if (v && v !== s.name) onSave(s.id, { name: v });
    else setName(s.name);
  };
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("flex items-center gap-2 rounded-lg border bg-popover p-2", isDragging && "relative z-10 shadow-lg ring-2 ring-primary")}
    >
      <button type="button" {...attributes} {...listeners} aria-label={`Reorder ${s.name}`}
        className="cursor-grab touch-none rounded p-0.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing">
        <GripVertical className="size-4" />
      </button>
      <ColorPicker value={s.color} label={`Color for ${s.name}`} onChange={(c) => onSave(s.id, { color: c })} />
      <Input aria-label={`Name of stage ${s.name}`} value={name} maxLength={80} className="h-8 min-w-0 flex-1"
        onChange={(e) => setName(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { e.stopPropagation(); setName(s.name); }
        }} />
      <NativeSelect aria-label={`Prospect status linked to ${s.name}`} title="Linked prospect status: moving a card here sets it; automation moves cards in linked stages"
        className="hidden w-40 sm:block" value={s.lead_status ?? ""}
        onChange={(e) => onSave(s.id, { lead_status: (e.target.value || null) as Stage["lead_status"] })}>
        <option value="">No status link</option>
        {LEAD_STATUSES.map((st) => <option key={st} value={st} disabled={usedStatuses.has(st)}>{humanize(st)}</option>)}
      </NativeSelect>
      <span className="w-10 text-right text-xs text-muted-foreground tabular-nums" title="Opportunities in this stage">{num(s.count)}</span>
      <Button variant="ghost" size="icon-sm" aria-label={`Delete ${s.name}`} disabled={stages.length <= 1} onClick={() => onDelete(s)}><Trash2 /></Button>
    </li>
  );
}

function DeleteStageDialog({ stage, stages, onClose }: { stage: Stage | null; stages: Stage[]; onClose: () => void }) {
  const qc = useQueryClient();
  const others = stages.filter((s) => s.id !== stage?.id);
  const [target, setTarget] = React.useState("");
  const chosen = others.some((s) => s.id === target) ? target : others[0]?.id ?? "";
  const del = useMutation({
    mutationFn: () => api<{ moved: number; stages: Stage[] }>(`/api/opportunities/stages/${stage!.id}`, { method: "DELETE", query: { move_to: stage!.count ? chosen : undefined } }),
    onSuccess: (r) => {
      qc.setQueryData(["opportunity-stages"], r.stages);
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      toast.success(`Stage “${stage!.name}” deleted`, { description: r.moved ? `${num(r.moved)} opportunit${r.moved === 1 ? "y" : "ies"} moved.` : undefined });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!stage} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete “{stage?.name}”?</DialogTitle>
          <DialogDescription>
            {stage?.count
              ? `${num(stage.count)} opportunit${stage.count === 1 ? "y is" : "ies are"} in this stage. Choose where to move ${stage.count === 1 ? "it" : "them"}. No opportunity or prospect is deleted, and prospect statuses don’t change.`
              : "This stage is empty. No opportunity or prospect is affected."}
          </DialogDescription>
        </DialogHeader>
        {!!stage?.count && (
          <Field label="Move opportunities to" htmlFor="move-to">
            <NativeSelect id="move-to" className="w-full" value={chosen} onChange={(e) => setTarget(e.target.value)}>
              {others.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </NativeSelect>
          </Field>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="destructive" disabled={del.isPending || (!!stage?.count && !chosen)} onClick={() => del.mutate()}>
            {stage?.count ? "Move and delete" : "Delete stage"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function StageManager({ open, onOpenChange, stages }: { open: boolean; onOpenChange: (o: boolean) => void; stages: Stage[] }) {
  const qc = useQueryClient();
  const [newName, setNewName] = React.useState("");
  const [newColor, setNewColor] = React.useState(STAGE_COLORS[5]);
  const [deleting, setDeleting] = React.useState<Stage | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const done = (r: Stage[]) => {
    qc.setQueryData(["opportunity-stages"], r);
    qc.invalidateQueries({ queryKey: ["opportunities"] });
  };
  const save = useMutation({
    mutationFn: ({ id, body }: { id: string; body: object }) => api<Stage[]>(`/api/opportunities/stages/${id}`, { method: "PATCH", body }),
    onSuccess: (r) => { done(r); toast.success("Stage updated"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const add = useMutation({
    mutationFn: () => api<Stage[]>("/api/opportunities/stages", { body: { name: newName.trim(), color: newColor } }),
    onSuccess: (r) => { done(r); setNewName(""); toast.success("Stage added"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api<Stage[]>("/api/opportunities/stages/order", { method: "PUT", body: { ids } }),
    onMutate: (ids) => {
      const prev = qc.getQueryData<Stage[]>(["opportunity-stages"]);
      if (prev) qc.setQueryData(["opportunity-stages"], ids.map((id) => prev.find((s) => s.id === id)!));
      return { prev };
    },
    onSuccess: done,
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["opportunity-stages"], ctx.prev);
      toast.error(errorMessage(e));
    },
  });
  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return;
    const ids = stages.map((s) => s.id);
    reorder.mutate(arrayMove(ids, ids.indexOf(String(e.active.id)), ids.indexOf(String(e.over.id))));
  };
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Manage stages</DialogTitle>
            <DialogDescription>Rename, recolor and drag to reorder. A stage linked to a prospect status keeps campaign automation in step with the board.</DialogDescription>
          </DialogHeader>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={stages.map((s) => s.id)} strategy={verticalListSortingStrategy}>
              <ul className="grid max-h-[55vh] gap-2 overflow-y-auto pr-1">
                {stages.map((s) => (
                  <StageRow key={s.id} s={s} stages={stages} onSave={(id, body) => save.mutate({ id, body })} onDelete={setDeleting} />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
          <form className="flex items-center gap-2 border-t pt-3" onSubmit={(e) => { e.preventDefault(); if (newName.trim()) add.mutate(); }}>
            <ColorPicker value={newColor} label="Color for the new stage" onChange={setNewColor} />
            <Input aria-label="New stage name" placeholder="New stage, e.g. Negotiation" maxLength={80} className="h-8 flex-1" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Button type="submit" size="sm" disabled={add.isPending || !newName.trim()}><Plus /> Add stage</Button>
          </form>
        </DialogContent>
      </Dialog>
      <DeleteStageDialog stage={deleting} stages={stages} onClose={() => setDeleting(null)} />
    </>
  );
}
