import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ASK_ERRORS, location } from "./shared";
import type { Located } from "./shared";

type Source = Located & {
  id: string;
  document_id: string;
  title: string;
  version_id: string;
};
type Turn = {
  id: string;
  seq: number;
  question: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  answer: string | null;
  redacted: boolean;
  error_code: string | null;
  model: { id: string; evaluated: boolean } | null;
  clarify: { document_id: string; title: string }[];
  sources: Source[];
};
type Thread = {
  id: string;
  title: string;
  focus: { id: string; title: string }[];
  turns: Turn[];
};
type ThreadItem = { id: string; title: string; updated_at: string };

type Props = {
  project: string;
  api: <T>(path: string, init?: RequestInit) => Promise<T>;
  onOpenSource: (documentId: string, versionId: string) => void;
  onError: (message: string) => void;
};

const open = (t: Turn) => t.status === "PENDING" || t.status === "RUNNING";

export function Chat({ project, api, onOpenSource, onError }: Props) {
  const [threads, setThreads] = useState<ThreadItem[]>([]);
  const [active, setActive] = useState<Thread | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const pending = !!active?.turns.some(open);
  const activeId = active?.id;

  const fail = (e: unknown) =>
    onError(e instanceof Error ? e.message : "Connection error");

  async function loadThreads() {
    setThreads(await api<ThreadItem[]>(`/projects/${project}/chat/threads`));
  }
  async function loadThread(id: string) {
    setActive(await api<Thread>(`/chat/threads/${id}`));
  }

  // A different project means a different set of chats.
  useEffect(() => {
    setActive(null);
    setDraft("");
    void loadThreads().catch(fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  // Follow an unfinished answer until it settles (the model answers asynchronously).
  useEffect(() => {
    if (!activeId || !pending) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Thread>(`/chat/threads/${activeId}`);
        if (cancelled) return;
        setActive(next);
        if (!next.turns.some(open)) void loadThreads().catch(() => undefined);
      } catch (e) {
        if (!cancelled) fail(e);
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, pending]);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [active?.turns.length, pending]);

  async function send(text?: string, documentIds?: string[]) {
    const content = (text ?? draft).trim();
    if (content.length < 2 || sending || pending) return;
    setSending(true);
    try {
      let id = active?.id;
      if (!id) {
        id = (
          await api<{ id: string }>(`/projects/${project}/chat/threads`, {
            method: "POST",
          })
        ).id;
      }
      await api(`/chat/threads/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          documentIds ? { content, document_ids: documentIds } : { content },
        ),
      });
      if (text === undefined) setDraft("");
      await loadThread(id);
      await loadThreads();
    } catch (e) {
      fail(e);
    } finally {
      setSending(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  async function forgetTopic() {
    if (!active) return;
    try {
      await api(`/chat/threads/${active.id}/focus`, { method: "DELETE" });
      await loadThread(active.id);
    } catch (e) {
      fail(e);
    }
  }

  async function remove(item: ThreadItem) {
    if (!window.confirm(`Delete “${item.title}”? This cannot be undone.`)) return;
    try {
      await api(`/chat/threads/${item.id}`, { method: "DELETE" });
      if (active?.id === item.id) setActive(null);
      await loadThreads();
    } catch (e) {
      fail(e);
    }
  }
  async function rename(item: ThreadItem) {
    const title = window.prompt("Chat title", item.title)?.trim();
    if (!title) return;
    try {
      await api(`/chat/threads/${item.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (active?.id === item.id) setActive({ ...active, title });
      await loadThreads();
    } catch (e) {
      fail(e);
    }
  }

  return (
    <div className="chat">
      <nav className="chat-threads" aria-label="Your chats">
        <button
          className="chat-new"
          onClick={() => {
            setActive(null);
            setDraft("");
          }}
        >
          + New chat
        </button>
        {threads.length === 0 && (
          <p className="muted">No chats yet. Ask your first question.</p>
        )}
        {threads.map((t) => (
          <div
            key={t.id}
            className={"chat-thread" + (active?.id === t.id ? " current" : "")}
          >
            <button
              className="chat-thread-open"
              onClick={() => void loadThread(t.id).catch(fail)}
              title={t.title}
            >
              {t.title}
            </button>
            <button
              className="chat-icon"
              aria-label={`Rename ${t.title}`}
              title="Rename"
              onClick={() => void rename(t)}
            >
              ✎
            </button>
            <button
              className="chat-icon"
              aria-label={`Delete ${t.title}`}
              title="Delete"
              onClick={() => void remove(t)}
            >
              ×
            </button>
          </div>
        ))}
      </nav>

      <section className="chat-main">
        <div className="chat-log" ref={log} role="log" aria-live="polite">
          {!active || active.turns.length === 0 ? (
            <div className="chat-empty">
              <h2>Ask about your documents</h2>
              <p className="muted">
                Answers use only published documents you can access, and cite
                where they came from. You can ask follow-up questions; the chat
                remembers what you discussed.
              </p>
            </div>
          ) : (
            active.turns.map((t) => (
              <div key={t.id} className="chat-turn">
                <div className="bubble user">{t.question}</div>
                <div className="bubble assistant">
                  {open(t) && (
                    <p className="chat-thinking">
                      Thinking<span className="dots" aria-hidden="true" />
                      <small>
                        This can take up to a minute on this computer.
                      </small>
                    </p>
                  )}
                  {t.status === "COMPLETED" && t.redacted && (
                    <p className="ask-failed">
                      Hidden: you no longer have access to a document this
                      answer used.
                    </p>
                  )}
                  {t.status === "COMPLETED" && !t.redacted && (
                    <p className="ask-answer">{t.answer}</p>
                  )}
                  {t.clarify.length > 0 && t === active?.turns[active.turns.length - 1] && (
                    <div className="chat-choices" role="group" aria-label="Choose a document">
                      {t.clarify.map((c) => (
                        <button
                          key={c.document_id}
                          className="chat-choice"
                          disabled={sending || pending}
                          onClick={() => void send(t.question, [c.document_id])}
                        >
                          {c.title}
                        </button>
                      ))}
                    </div>
                  )}
                  {t.status === "FAILED" && (
                    <p className="ask-failed">
                      {ASK_ERRORS[t.error_code ?? ""] ??
                        "The question could not be answered."}
                    </p>
                  )}
                  {t.status === "COMPLETED" && !t.redacted && t.model && (
                    <p className={t.model.evaluated ? "muted" : "ask-warning"}>
                      Answered by {t.model.id}
                      {t.model.evaluated
                        ? "."
                        : " — an unevaluated test model. It may be wrong; check the cited sources."}
                    </p>
                  )}
                  {t.sources.length > 0 && (
                    <div className="chat-sources">
                      {t.sources.map((s) => (
                        <button
                          key={s.id}
                          className="chat-source"
                          title={`${s.title} · ${location(s)}`}
                          onClick={() => onOpenSource(s.document_id, s.version_id)}
                        >
                          {s.title} · {location(s)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {active && active.focus.length > 0 && (
          <div className="chat-focus">
            <span>Talking about:</span>
            {active.focus.map((f) => (
              <span className="chat-focus-chip" key={f.id}>
                {f.title}
              </span>
            ))}
            <button
              className="chat-focus-clear"
              title="Stop assuming this document"
              aria-label="Forget the current document"
              onClick={() => void forgetTopic()}
            >
              ×
            </button>
          </div>
        )}
        <div className="chat-composer">
          <textarea
            aria-label="Message"
            placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
            value={draft}
            maxLength={1000}
            rows={3}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
          />
          <button
            onClick={() => void send()}
            disabled={sending || pending || draft.trim().length < 2}
          >
            {pending ? "Answering…" : "Send"}
          </button>
        </div>
        <small className="chat-note">
          AI can be wrong. Check the cited sources. Chats are private to you and
          can be deleted at any time.
        </small>
      </section>
    </div>
  );
}
