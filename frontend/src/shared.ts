/** Small helpers shared by the document workspace and the chat. */

export type Located = {
  start_offset: number;
  end_offset: number;
  page_start: number | null;
  page_end: number | null;
};

export function location(item: Located): string {
  if (item.page_start == null) return `${item.start_offset}–${item.end_offset}`;
  return item.page_end != null && item.page_end !== item.page_start
    ? `pages ${item.page_start}–${item.page_end}`
    : `page ${item.page_start}`;
}

export const ASK_ERRORS: Record<string, string> = {
  MODEL_UNAVAILABLE:
    "No AI model is available yet, so this question could not be answered. Search still works.",
  INVALID_OUTPUT: "The model returned an unusable answer. Please try again.",
  TASK_EXPIRED: "The question timed out before it was answered. Ask again.",
  AUTHORIZATION_CHANGED:
    "Your access or the documents changed while answering. Ask again.",
};
