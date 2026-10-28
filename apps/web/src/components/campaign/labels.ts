import type { Step } from "@/lib/types";

/** "Initial email", "Follow-up #1"…, or the name given to the step. */
export function stepLabel(position: number, name?: string | null) {
  return name?.trim() || (position <= 1 ? "Initial email" : `Follow-up #${position - 1}`);
}

export function stepTitle(step: Pick<Step, "name">, position: number) {
  return stepLabel(position, step.name);
}
