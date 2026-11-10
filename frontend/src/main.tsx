import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type User = { id: string; username: string; is_admin: boolean };
type Item = { id: string; name?: string; title?: string };
type Meta = {
  id: string;
  title: string;
  latest_version_id: string | null;
  active_version_id: string | null;
  publication_revision: string;
  owner_id: string;
  restricted: boolean;
};
type Version = { id: string; state: string; sha256: string; size: number };
type Approval = {
  id: string;
  candidate_version_id: string;
  candidate_sha256: string;
  status: string;
};
type Review = {
  approval_id: string;
  candidate_sha256: string;
  original: string | null;
  proposed: string;
};
type SearchHit = {
  document_id: string;
  title: string;
  version_id: string;
  snippet: string;
  start_offset: number;
  end_offset: number;
};
type Account = { id: string; username: string; enabled: boolean };
type Member = { id: string; username: string };
type AskSource = {
  id: string;
  document_id: string;
  title: string;
  version_id: string;
  start_offset: number;
  end_offset: number;
};
type AskTask = {
  id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  answer: string | null;
  error_code: string | null;
  sources: AskSource[];
};
const ASK_ERRORS: Record<string, string> = {
  MODEL_UNAVAILABLE:
    "No AI model is available yet, so this question could not be answered. Search still works.",
  INVALID_OUTPUT: "The model returned an unusable answer. Please try again.",
  TASK_EXPIRED: "The question timed out before it was answered. Ask again.",
  AUTHORIZATION_CHANGED:
    "Your access or the documents changed while answering. Ask again.",
};

function App() {
  const [token, setToken] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Item[]>([]);
  const [project, setProject] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searched, setSearched] = useState(false);
  const [ask, setAsk] = useState<AskTask | null>(null);
  const grantUser = useRef<HTMLSelectElement>(null);
  const [docs, setDocs] = useState<Item[]>([]);
  const [doc, setDoc] = useState<Meta | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [review, setReview] = useState<Review | null>(null);
  const [text, setText] = useState("");
  const [loadedVersion, setLoadedVersion] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const retryUpload = useRef<{ signature: string; key: string } | null>(null);

  async function api<T>(
    path: string,
    init: RequestInit = {},
    bearer = token,
  ): Promise<T> {
    const response = await fetch("/api/v1" + path, {
      ...init,
      headers: {
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        ...init.headers,
      },
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status === 401 && bearer) {
        clearSession();
      }
      const error = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      throw new Error(error.detail || `Request failed (${response.status})`);
    }
    return (response.status === 204 ? undefined : await response.json()) as T;
  }
  const json = (body: unknown): RequestInit => ({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  function clearDocument() {
    generation.current++;
    setDoc(null);
    setVersions([]);
    setApprovals([]);
    setReview(null);
    setText("");
    setLoadedVersion(null);
    retryUpload.current = null;
  }
  function clearSession() {
    clearDocument();
    setToken("");
    setUser(null);
    setProjects([]);
    setProject("");
    setDocs([]);
    setAccounts([]);
    setMembers([]);
    setHits([]);
    setSearched(false);
    setAsk(null);
  }
  function success(text: string) {
    setMessage(text);
    setOk(true);
  }
  async function task(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    setOk(false);
    try {
      await action();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Connection error");
    } finally {
      setBusy(false);
    }
  }
  async function refreshProjects(bearer = token) {
    setProjects(await api<Item[]>("/projects", {}, bearer));
  }
  async function refreshDocs(p = project) {
    if (!p) return;
    setDocs(await api<Item[]>("/documents?project_id=" + p));
    setMembers(
      await api<Member[]>(`/projects/${p}/members`).catch(() => [] as Member[]),
    );
  }
  async function openDocument(id: string, preferredVersion?: string) {
    clearDocument();
    const current = generation.current;
    const meta = await api<Meta>("/documents/" + id);
    const history = await api<Version[]>(
      "/documents/" + id + "/versions",
    ).catch(() => []);
    const requests = await api<Approval[]>(
      "/documents/" + id + "/approvals",
    ).catch(() => []);
    if (generation.current !== current) return;
    setDoc(meta);
    setVersions(history);
    setApprovals(requests);
    const version =
      preferredVersion || meta.latest_version_id || meta.active_version_id;
    if (version) await loadText(id, version, current);
  }
  async function loadText(
    id: string,
    version: string,
    current = generation.current,
  ) {
    const response = await fetch(
      `/api/v1/documents/${id}/content?version_id=${version}`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
    );
    if (!response.ok) {
      const body = (await response.json()) as { detail: string };
      throw new Error(body.detail);
    }
    const content = await response.text();
    if (generation.current === current) {
      setText(content);
      setLoadedVersion(version);
    }
  }
  useEffect(() => {
    if (!token) return;
    const expiry = window.setTimeout(() => {
      clearSession();
      setOk(false);
      setMessage("Your session has expired. Please sign in again.");
    }, 3600_000);
    return () => window.clearTimeout(expiry);
  }, [token]);

  // Poll an unfinished question until it settles; the orchestrator answers asynchronously.
  const askId = ask?.id;
  const askOpen = ask?.status === "PENDING" || ask?.status === "RUNNING";
  useEffect(() => {
    if (!askId || !askOpen) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await api<AskTask>("/ai/tasks/" + askId);
        if (cancelled) return;
        setAsk(next);
        if (next.status === "PENDING" || next.status === "RUNNING")
          timer = window.setTimeout(() => void poll(), 1500);
      } catch (e) {
        if (cancelled) return;
        setAsk(null);
        setOk(false);
        setMessage(e instanceof Error ? e.message : "Connection error");
      }
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // api() reads the current token; restart only when the task or session changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askId, askOpen, token]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await task(async () => {
      const auth = await api<{ access_token: string }>(
        "/auth/login",
        json({
          username: form.get("username"),
          password: form.get("password"),
        }),
        "",
      );
      const me = await api<User>("/auth/me", {}, auth.access_token);
      setToken(auth.access_token);
      setUser(me);
      await refreshProjects(auth.access_token);
    });
  }
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("file") as File;
    if (!file?.size || file.size > 2097152) {
      setOk(false);
      setMessage("Choose a non-empty file no larger than 2 MiB.");
      return;
    }
    await task(async () => {
      const signature = [
        project,
        file.name,
        file.size,
        file.lastModified,
        data.get("title"),
        data.get("restricted"),
      ].join(":");
      if (retryUpload.current?.signature !== signature)
        retryUpload.current = { signature, key: crypto.randomUUID() };
      const mime = file.name.toLowerCase().endsWith(".pdf")
        ? "application/pdf"
        : "text/plain";
      const result = await api<{ document_id: string }>(
        "/documents?" +
          new URLSearchParams({
            project_id: project,
            title: String(data.get("title") || file.name),
            restricted: String(data.get("restricted") === "on"),
          }),
        {
          method: "POST",
          headers: {
            "Content-Type": mime,
            "Idempotency-Key": retryUpload.current!.key,
          },
          body: file,
        },
      );
      retryUpload.current = null;
      form.reset();
      await refreshDocs();
      await openDocument(result.document_id);
    });
  }

  if (!user)
    return (
      <main className="login">
        <section className="intro">
          <span className="eyebrow">PRIVATE WORKSPACE</span>
          <h1>
            Your organization’s knowledge.
            <br />
            In your own workspace.
          </h1>
          <p>
            Manage documents and versions, review changes, and publish with
            authorized approval.
          </p>
          <span className="status">● Self-hosted · Access controlled</span>
        </section>
        <section className="login-card">
          <div className="mark">W</div>
          <h2>Sign in to your workspace</h2>
          <p className="muted">Use your organization account to continue.</p>
          <form onSubmit={login}>
            <label>
              Username
              <input
                name="username"
                autoComplete="username"
                dir="ltr"
                required
                minLength={3}
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                dir="ltr"
                required
                minLength={16}
                maxLength={256}
              />
            </label>
            <button disabled={busy}>
              {busy ? "Signing in…" : "Sign in to your workspace →"}
            </button>
          </form>
          <p role="alert" className="notice">
            {message}
          </p>
          <small>Contact your administrator to request an account.</small>
        </section>
      </main>
    );

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="mark">W</span>
          <div>
            Private Workspace<small>Organization documents</small>
          </div>
        </div>
        <div className="nav-label">PROJECT SPACE</div>
        <select
          aria-label="Select a project"
          value={project}
          disabled={busy}
          onChange={(e) => {
            const p = e.target.value;
            clearDocument();
            setProject(p);
            setHits([]);
            setSearched(false);
            setAsk(null);
            setDocs([]);
            void task(() => refreshDocs(p));
          }}
        >
          <option value="">Select a project</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          className="nav-item"
          disabled={busy}
          onClick={() =>
            void task(async () => {
              await refreshProjects();
              await refreshDocs();
            })
          }
        >
          ↻ Refresh documents
        </button>
        <div className="side-note">
          Previous versions are preserved.
          <br />
          Publishing requires independent approval.
        </div>
        <div className="profile">
          <strong>{user.username}</strong>
          <small>
            {user.is_admin ? "Administrator" : "Organization member"}
          </small>
          <details className="password">
            <summary>Change password</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = e.currentTarget;
                const data = new FormData(f);
                if (data.get("new") !== data.get("again")) {
                  setOk(false);
                  setMessage("The new passwords do not match.");
                  return;
                }
                void task(async () => {
                  await api("/auth/password", {
                    ...json({
                      current_password: data.get("current"),
                      new_password: data.get("new"),
                    }),
                  });
                  f.reset();
                  success(
                    "Password changed. Your other sessions were signed out.",
                  );
                });
              }}
            >
              <input
                name="current"
                type="password"
                aria-label="Current password"
                placeholder="Current password"
                autoComplete="current-password"
                maxLength={256}
                required
              />
              <input
                name="new"
                type="password"
                aria-label="New password"
                placeholder="New password (16+ characters)"
                autoComplete="new-password"
                minLength={16}
                maxLength={256}
                required
              />
              <input
                name="again"
                type="password"
                aria-label="Repeat new password"
                placeholder="Repeat new password"
                autoComplete="new-password"
                minLength={16}
                maxLength={256}
                required
              />
              <button disabled={busy}>Save password</button>
            </form>
          </details>
          <button
            className="secondary"
            onClick={() =>
              void task(async () => {
                try {
                  await api("/auth/logout", { method: "POST" });
                } finally {
                  clearSession();
                }
              })
            }
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header>
          <div>
            <span className="eyebrow">DOCUMENT WORKSPACE</span>
            <h1>Documents & approvals</h1>
            <p className="muted">
              From draft to publication, with a clear history.
            </p>
          </div>
          <span className="badge">Local & private</span>
        </header>
        {message && (
          <div
            className={ok ? "notice ok" : "notice"}
            role={ok ? "status" : "alert"}
          >
            {message}
          </div>
        )}
        {user.is_admin && (
          <details
            className="admin"
            onToggle={(e) => {
              if (e.currentTarget.open && !accounts.length)
                void task(async () =>
                  setAccounts(await api<Account[]>("/users")),
                );
            }}
          >
            <summary>Manage users and projects</summary>
            <div className="admin-grid">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const data = new FormData(f);
                  void task(async () => {
                    const p = await api<Item>(
                      "/projects",
                      json({ name: data.get("name") }),
                    );
                    if (data.get("join"))
                      await api(`/projects/${p.id}/members/${user.id}`, {
                        ...json({
                          can_edit: true,
                          can_history: true,
                          can_approve: true,
                          can_publish: true,
                        }),
                        method: "PUT",
                      });
                    await refreshProjects();
                    f.reset();
                    success("Project created.");
                  });
                }}
              >
                <h3>New project</h3>
                <input
                  aria-label="Project name"
                  name="name"
                  placeholder="Project name"
                  required
                />
                <label className="check">
                  <input type="checkbox" name="join" defaultChecked /> Join this
                  project with document management permissions
                </label>
                <button disabled={busy}>Create project</button>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const data = new FormData(f);
                  void task(async () => {
                    await api(
                      "/users",
                      json({
                        username: data.get("username"),
                        password: data.get("password"),
                      }),
                    );
                    f.reset();
                    setAccounts(await api<Account[]>("/users"));
                    success("User created.");
                  });
                }}
              >
                <h3>New user</h3>
                <input
                  name="username"
                  aria-label="New username"
                  placeholder="Lowercase username"
                  dir="ltr"
                  required
                />
                <input
                  name="password"
                  aria-label="New user password"
                  placeholder="Password (at least 16 characters)"
                  type="password"
                  autoComplete="new-password"
                  minLength={16}
                  maxLength={256}
                  required
                />
                <button disabled={busy}>Create user</button>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const data = new FormData(e.currentTarget);
                  void task(async () => {
                    await api(
                      `/projects/${project}/members/${data.get("user")}`,
                      {
                        ...json({
                          can_edit: data.get("edit") === "on",
                          can_history: data.get("history") === "on",
                          can_approve: data.get("approve") === "on",
                          can_publish: data.get("publish") === "on",
                        }),
                        method: "PUT",
                      },
                    );
                    success(
                      "Membership saved. Existing project approvals require a new review.",
                    );
                  });
                }}
              >
                <h3>Selected project membership</h3>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    void task(async () =>
                      setAccounts(await api<Account[]>("/users")),
                    )
                  }
                >
                  Load users
                </button>
                <select name="user" aria-label="Project member" required>
                  <option value="">Select a user</option>
                  {accounts
                    .filter((a) => a.enabled)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.username}
                      </option>
                    ))}
                </select>
                <div className="checks">
                  {[
                    ["edit", "Edit"],
                    ["history", "History"],
                    ["approve", "Approve"],
                    ["publish", "Publish"],
                  ].map(([name, label]) => (
                    <label className="check" key={name}>
                      <input type="checkbox" name={name} />
                      {label}
                    </label>
                  ))}
                </div>
                <button disabled={busy || !project}>Save membership</button>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const data = new FormData(f);
                  void task(async () => {
                    await api(`/users/${data.get("user")}/password`, {
                      ...json({ new_password: data.get("password") }),
                      method: "PUT",
                    });
                    f.reset();
                    success(
                      "Password reset. That user was signed out and their pending approvals need a new review.",
                    );
                  });
                }}
              >
                <h3>Reset a user’s password</h3>
                <select name="user" aria-label="User to reset" required>
                  <option value="">Select a user</option>
                  {accounts
                    .filter((a) => a.id !== user.id)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.username}
                      </option>
                    ))}
                </select>
                <input
                  name="password"
                  type="password"
                  aria-label="Temporary password"
                  placeholder="Temporary password (16+ characters)"
                  autoComplete="new-password"
                  minLength={16}
                  maxLength={256}
                  required
                />
                <button disabled={busy}>Reset password</button>
                <small>
                  Share the temporary password through a trusted channel and
                  ask the user to change it.
                </small>
              </form>
            </div>
          </details>
        )}
        {!project ? (
          <section className="empty">
            <span className="empty-icon">▤</span>
            <h2>Select a project</h2>
            <p>Documents are visible only in projects you belong to.</p>
            {!projects.length && <p>No projects are available to you yet.</p>}
          </section>
        ) : (
          <div className="columns">
            <section className="documents">
              <div className="section-title">
                <h2>Document library</h2>
                <span>
                  {docs.length} {docs.length === 1 ? "document" : "documents"}
                </span>
              </div>
              <form
                className="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  const data = new FormData(e.currentTarget);
                  setHits([]);
                  setSearched(false);
                  void task(async () => {
                    setHits(
                      await api<SearchHit[]>(
                        "/search?" +
                          new URLSearchParams({
                            project_id: project,
                            q: String(data.get("q")),
                          }),
                      ),
                    );
                    setSearched(true);
                  });
                }}
              >
                <label>
                  Search published documents
                  <input
                    name="q"
                    placeholder="Search terms…"
                    minLength={2}
                    maxLength={200}
                    required
                  />
                </label>
                <button className="secondary" disabled={busy}>
                  Search
                </button>
                <small>
                  Documents are indexed after publication. This is text search.
                </small>
              </form>
              {searched && (
                <section className="search-results">
                  <h3>Search results</h3>
                  {hits.length === 0 && (
                    <p className="muted">No results found.</p>
                  )}
                  {hits.map((hit, index) => (
                    <button
                      key={
                        hit.version_id + ":" + hit.start_offset + ":" + index
                      }
                      className="search-hit"
                      disabled={busy}
                      onClick={() =>
                        void task(async () => {
                          await openDocument(hit.document_id, hit.version_id);
                        })
                      }
                    >
                      <strong>{hit.title}</strong>
                      <small>{hit.snippet}</small>
                      <span dir="ltr">
                        {hit.version_id.slice(0, 8)} · {hit.start_offset}–
                        {hit.end_offset}
                      </span>
                    </button>
                  ))}
                </section>
              )}
              <form
                className="ask"
                onSubmit={(e) => {
                  e.preventDefault();
                  const f = e.currentTarget;
                  const data = new FormData(f);
                  setAsk(null);
                  void task(async () => {
                    const created = await api<{ id: string }>(
                      "/ai/tasks",
                      json({
                        project_id: project,
                        question: String(data.get("question")),
                      }),
                    );
                    setAsk(await api<AskTask>("/ai/tasks/" + created.id));
                    f.reset();
                  });
                }}
              >
                <label>
                  Ask a question about published documents
                  <textarea
                    name="question"
                    placeholder="For example: What is the payment term in the services agreement?"
                    minLength={2}
                    maxLength={1000}
                    rows={2}
                    required
                  />
                </label>
                <button disabled={busy || askOpen}>
                  {askOpen ? "Answering…" : "Ask"}
                </button>
                <small>
                  Answers use only published documents you can access, with
                  citations. AI can be wrong; check the sources.
                </small>
              </form>
              {ask && (
                <section className="ask-result" aria-live="polite">
                  <h3>Answer</h3>
                  {askOpen && (
                    <p className="muted">Finding evidence and answering…</p>
                  )}
                  {ask.status === "COMPLETED" && (
                    <p className="ask-answer">{ask.answer}</p>
                  )}
                  {ask.status === "FAILED" && (
                    <p className="ask-failed">
                      {ASK_ERRORS[ask.error_code ?? ""] ??
                        "The question could not be answered."}
                    </p>
                  )}
                  {ask.status === "COMPLETED" && ask.sources.length === 0 && (
                    <p className="muted">No supporting sources.</p>
                  )}
                  {ask.sources.map((src) => (
                    <button
                      key={src.id}
                      className="search-hit"
                      disabled={busy}
                      onClick={() =>
                        void task(async () => {
                          await openDocument(src.document_id, src.version_id);
                        })
                      }
                    >
                      <strong>{src.title}</strong>
                      <span dir="ltr">
                        {src.version_id.slice(0, 8)} · {src.start_offset}–
                        {src.end_offset}
                      </span>
                    </button>
                  ))}
                </section>
              )}
              <form className="upload" onSubmit={upload}>
                <h3>Add a document</h3>
                <input
                  name="title"
                  placeholder="Document title"
                  aria-label="Document title"
                  maxLength={200}
                />
                <input
                  name="file"
                  type="file"
                  aria-label="Document file"
                  accept=".txt,.md,.pdf"
                  required
                />
                <label className="check">
                  <input name="restricted" type="checkbox" /> Restrict this
                  document to specific people
                </label>
                <button disabled={busy}>Upload draft</button>
                <small>
                  Up to 2 MiB. PDFs remain quarantined pending validation.
                </small>
              </form>
              <div className="doc-list">
                {docs.map((d) => (
                  <button
                    key={d.id}
                    className={
                      "doc-row " + (doc?.id === d.id ? "selected" : "")
                    }
                    disabled={busy}
                    onClick={() => void task(() => openDocument(d.id))}
                  >
                    <span>▤</span>
                    <div>
                      <strong>{d.title}</strong>
                      <small>View document and versions</small>
                    </div>
                    <span>→</span>
                  </button>
                ))}
                {!docs.length && (
                  <p className="muted">This project has no documents yet.</p>
                )}
              </div>
            </section>
            <section className="editor">
              {!doc ? (
                <div className="empty">
                  <h2>Open a document to review</h2>
                  <p>
                    Document text, version history, and approval requests appear
                    here.
                  </p>
                </div>
              ) : (
                <>
                  <div className="section-title">
                    <h2>{doc.title}</h2>
                    <span className="badge">
                      {doc.active_version_id
                        ? "Published version available"
                        : "Draft"}
                    </span>
                  </div>
                  <div className="toolbar">
                    {versions.map((v) => (
                      <button
                        className={
                          "version " + (loadedVersion === v.id ? "active" : "")
                        }
                        key={v.id}
                        disabled={busy}
                        title={v.sha256}
                        onClick={() => void task(() => loadText(doc.id, v.id))}
                      >
                        {v.state === "PUBLISHED" ? "Published" : "Draft"} ·{" "}
                        {v.id.slice(0, 6)}
                      </button>
                    ))}
                  </div>
                  <label>
                    Document text
                    <textarea
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      readOnly={
                        !doc.latest_version_id ||
                        loadedVersion !== doc.latest_version_id
                      }
                      dir="auto"
                      rows={16}
                    />
                  </label>
                  <div className="actions">
                    <button
                      disabled={
                        busy ||
                        !doc.latest_version_id ||
                        loadedVersion !== doc.latest_version_id ||
                        !text
                      }
                      onClick={() =>
                        void task(async () => {
                          const body = new Blob([text], { type: "text/plain" });
                          const signature = `${doc.id}:${doc.latest_version_id}:${text}`;
                          if (retryUpload.current?.signature !== signature)
                            retryUpload.current = {
                              signature,
                              key: crypto.randomUUID(),
                            };
                          await api(
                            `/documents/${doc.id}/versions?parent_version_id=${doc.latest_version_id}`,
                            {
                              method: "POST",
                              headers: {
                                "Content-Type": "text/plain",
                                "Idempotency-Key": retryUpload.current!.key,
                              },
                              body,
                            },
                          );
                          retryUpload.current = null;
                          await openDocument(doc.id);
                          success(
                            "New version saved. The original is preserved.",
                          );
                        })
                      }
                    >
                      Save new version
                    </button>
                    <button
                      className="secondary"
                      disabled={
                        busy ||
                        !loadedVersion ||
                        !doc.latest_version_id ||
                        loadedVersion === doc.active_version_id
                      }
                      onClick={() =>
                        void task(async () => {
                          await api(
                            `/documents/${doc.id}/approvals`,
                            json({
                              candidate_version_id: loadedVersion,
                              expected_active_version_id: doc.active_version_id,
                              expected_publication_revision: Number(
                                doc.publication_revision,
                              ),
                            }),
                          );
                          await openDocument(doc.id);
                          success(
                            "Review requested. Another authorized person must approve it.",
                          );
                        })
                      }
                    >
                      Request approval for selected version
                    </button>
                  </div>
                  {doc.owner_id === user.id && doc.latest_version_id && (
                    <details className="access-panel">
                      <summary>Document access</summary>
                      <p className="muted">
                        Document permissions are always limited by project
                        membership and permissions.
                      </p>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={doc.restricted}
                          disabled={busy}
                          onChange={(e) => {
                            const restricted = e.target.checked;
                            void task(async () => {
                              await api(`/documents/${doc.id}/access`, {
                                ...json({ restricted }),
                                method: "PATCH",
                              });
                              await openDocument(doc.id);
                            });
                          }}
                        />{" "}
                        Restrict access to specific people
                      </label>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          const data = new FormData(e.currentTarget);
                          void task(async () => {
                            await api(
                              `/documents/${doc.id}/access/${data.get("user")}`,
                              {
                                ...json({
                                  can_edit: data.get("edit") === "on",
                                  can_history: data.get("history") === "on",
                                }),
                                method: "PUT",
                              },
                            );
                            success("Document access saved.");
                          });
                        }}
                      >
                        <select
                          ref={grantUser}
                          name="user"
                          aria-label="Person to give document access"
                          required
                        >
                          <option value="">Select a person</option>
                          {members
                            .filter((m) => m.id !== user.id)
                            .map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.username}
                              </option>
                            ))}
                        </select>
                        <div className="checks">
                          <label className="check">
                            <input name="edit" type="checkbox" />
                            Edit
                          </label>
                          <label className="check">
                            <input name="history" type="checkbox" />
                            History
                          </label>
                        </div>
                        <div className="actions">
                          <button className="secondary" disabled={busy}>
                            Save access
                          </button>
                          <button
                            type="button"
                            className="danger"
                            disabled={busy}
                            onClick={() =>
                              void task(async () => {
                                const id = grantUser.current?.value;
                                if (!id) throw new Error("Select a person first.");
                                await api(`/documents/${doc.id}/access/${id}`, {
                                  method: "DELETE",
                                });
                                success("Document-specific access revoked.");
                              })
                            }
                          >
                            Revoke this user’s access
                          </button>
                        </div>
                      </form>
                    </details>
                  )}
                  <div className="section-title lower">
                    <h3>Approval workflow</h3>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => void task(() => openDocument(doc.id))}
                    >
                      Refresh
                    </button>
                  </div>
                  {approvals.map((a) => (
                    <div className="approval" key={a.id}>
                      <div>
                        <strong>
                          {(
                            {
                              PENDING: "Pending review",
                              APPROVED: "Approved",
                              REJECTED: "Rejected",
                              CONSUMED: "Published",
                            } as Record<string, string>
                          )[a.status] || a.status}
                        </strong>
                        <small dir="ltr">
                          {a.candidate_sha256.slice(0, 16)}
                        </small>
                      </div>
                      {a.status === "PENDING" && (
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            void task(async () =>
                              setReview(
                                await api<Review>(`/approvals/${a.id}/review`),
                              ),
                            )
                          }
                        >
                          Review change
                        </button>
                      )}
                      {a.status === "APPROVED" && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            void task(async () => {
                              await api(
                                `/documents/${doc.id}/publish`,
                                json({ approval_id: a.id }),
                              );
                              await openDocument(doc.id);
                              success("Version published successfully.");
                            })
                          }
                        >
                          Publish version
                        </button>
                      )}
                    </div>
                  ))}
                  {!approvals.length && (
                    <p className="muted">No approval requests are available.</p>
                  )}
                </>
              )}
            </section>
          </div>
        )}
      </main>
      {review && (
        <div className="modal-backdrop">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-title"
            className="review"
          >
            <div className="section-title">
              <h2 id="review-title">Review version before approval</h2>
              <button className="secondary" onClick={() => setReview(null)}>
                Close
              </button>
            </div>
            <p className="muted">
              Your approval applies only to this version and the current
              permissions.
            </p>
            <small dir="ltr">SHA-256: {review.candidate_sha256}</small>
            <div className="comparison">
              <div>
                <h3>Active version</h3>
                <pre dir="auto">
                  {review.original ?? "No version has been published yet."}
                </pre>
              </div>
              <div>
                <h3>Proposed version</h3>
                <pre dir="auto">{review.proposed}</pre>
              </div>
            </div>
            <div className="actions">
              <button
                disabled={busy}
                onClick={() =>
                  void task(async () => {
                    await api(`/approvals/${review.approval_id}/approve`, {
                      method: "POST",
                    });
                    setReview(null);
                    if (doc) await openDocument(doc.id);
                    success(
                      "Approval recorded. Publishing is a separate action.",
                    );
                  })
                }
              >
                Approve this version
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  void task(async () => {
                    await api(`/approvals/${review.approval_id}/reject`, {
                      method: "POST",
                    });
                    setReview(null);
                    if (doc) await openDocument(doc.id);
                  })
                }
              >
                Reject request
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
