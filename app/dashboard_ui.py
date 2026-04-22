from __future__ import annotations

from typing import Optional


def render_dashboard_html(*, title: str = "StarAgent Dashboard") -> str:
    """
    Lightweight local dashboard.

    Important:
    - This UI does not bypass API key checks. It calls the existing /v1/* APIs
      and requires the operator to provide the API key in-browser.
    - No server-side business logic duplication.
    """
    t = (title or "StarAgent Dashboard").strip()
    # Keep this self-contained (no external assets) for local operability.
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape_html(t)}</title>
    <style>
      :root {{
        --bg: #0b0f16;
        --panel: #121a26;
        --panel2: #0f1622;
        --text: #e6edf3;
        --muted: #9aa7b5;
        --accent: #6ee7ff;
        --danger: #ff5c7c;
        --ok: #56f1b5;
        --border: rgba(255,255,255,0.10);
        --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
      }}
      * {{ box-sizing: border-box; }}
      html, body {{ height: 100%; }}
      body {{
        margin: 0;
        font-family: var(--sans);
        background: radial-gradient(1200px 700px at 10% -10%, rgba(110,231,255,0.12), transparent 60%),
                    radial-gradient(900px 600px at 90% 10%, rgba(86,241,181,0.10), transparent 60%),
                    var(--bg);
        color: var(--text);
      }}
      a {{ color: var(--accent); text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      header {{
        padding: 14px 18px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        background: rgba(18,26,38,0.85);
        position: sticky;
        top: 0;
        backdrop-filter: blur(8px);
        z-index: 5;
      }}
      header h1 {{
        margin: 0;
        font-size: 15px;
        letter-spacing: 0.6px;
        font-weight: 650;
      }}
      header .right {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        justify-content: flex-end;
      }}
      .chip {{
        font-family: var(--mono);
        font-size: 12px;
        padding: 6px 8px;
        border: 1px solid var(--border);
        border-radius: 999px;
        color: var(--muted);
        background: rgba(0,0,0,0.15);
      }}
      main {{
        display: grid;
        grid-template-columns: 360px 1fr;
        gap: 14px;
        padding: 14px;
      }}
      @media (max-width: 980px) {{
        main {{ grid-template-columns: 1fr; }}
      }}
      .panel {{
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(18,26,38,0.75), rgba(15,22,34,0.7));
        border-radius: 12px;
        overflow: hidden;
      }}
      .panel .hd {{
        padding: 10px 12px;
        border-bottom: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .panel .hd h2 {{
        margin: 0;
        font-size: 13px;
        font-weight: 650;
        letter-spacing: 0.4px;
      }}
      .panel .bd {{
        padding: 12px;
      }}
      .row {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
      }}
      label {{
        font-size: 12px;
        color: var(--muted);
      }}
      input, select, textarea {{
        background: var(--panel2);
        border: 1px solid var(--border);
        color: var(--text);
        border-radius: 10px;
        padding: 8px 10px;
        font-size: 13px;
        outline: none;
        width: 100%;
      }}
      textarea {{ min-height: 74px; resize: vertical; font-family: var(--sans); }}
      input.mono {{ font-family: var(--mono); font-size: 12px; }}
      .btn {{
        border: 1px solid var(--border);
        background: rgba(0,0,0,0.18);
        color: var(--text);
        border-radius: 10px;
        padding: 8px 10px;
        font-size: 13px;
        cursor: pointer;
      }}
      .btn:hover {{ border-color: rgba(110,231,255,0.45); }}
      .btn.primary {{ border-color: rgba(110,231,255,0.35); }}
      .btn.danger {{ border-color: rgba(255,92,124,0.35); color: #ffd1da; }}
      .btn.ok {{ border-color: rgba(86,241,181,0.30); color: #d3ffef; }}
      .btn.small {{ padding: 6px 8px; font-size: 12px; border-radius: 9px; }}
      .muted {{ color: var(--muted); }}
      .tasks {{
        display: flex;
        flex-direction: column;
        gap: 8px;
      }}
      .task {{
        padding: 10px 10px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(0,0,0,0.14);
        cursor: pointer;
      }}
      .task:hover {{ border-color: rgba(110,231,255,0.30); }}
      .task.active {{ border-color: rgba(110,231,255,0.60); background: rgba(110,231,255,0.06); }}
      .task .top {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .task .id {{
        font-family: var(--mono);
        font-size: 11px;
        color: var(--muted);
      }}
      .task .status {{
        font-family: var(--mono);
        font-size: 11px;
        padding: 3px 7px;
        border-radius: 999px;
        border: 1px solid var(--border);
        color: var(--muted);
      }}
      .status.completed {{ color: var(--ok); border-color: rgba(86,241,181,0.35); }}
      .status.failed {{ color: var(--danger); border-color: rgba(255,92,124,0.40); }}
      .status.paused {{ color: #ffd47a; border-color: rgba(255,212,122,0.45); }}
      .status.partial {{ color: #c2d8ff; border-color: rgba(194,216,255,0.30); }}
      .status.running {{ color: var(--accent); border-color: rgba(110,231,255,0.40); }}
      .status.pending {{ color: #cbd5e1; border-color: rgba(203,213,225,0.25); }}
      .task .meta {{
        margin-top: 6px;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        font-size: 12px;
        color: var(--muted);
      }}
      .kv {{
        display: grid;
        grid-template-columns: 150px 1fr;
        gap: 10px;
        font-size: 13px;
        align-items: baseline;
      }}
      .kv .k {{ color: var(--muted); font-family: var(--mono); font-size: 12px; }}
      .pre {{
        white-space: pre-wrap;
        background: rgba(0,0,0,0.20);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        font-family: var(--mono);
        font-size: 12px;
        line-height: 1.45;
        max-height: 360px;
        overflow: auto;
      }}
      .grid2 {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }}
      @media (max-width: 980px) {{
        .grid2 {{ grid-template-columns: 1fr; }}
      }}
      .files {{
        display: flex;
        flex-direction: column;
        gap: 6px;
      }}
      .file {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 8px 10px;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: rgba(0,0,0,0.12);
        cursor: pointer;
      }}
      .file:hover {{ border-color: rgba(110,231,255,0.30); }}
      .file .name {{ font-family: var(--mono); font-size: 12px; }}
      .file .tag {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}
      .warn {{
        color: #ffd47a;
      }}
      .err {{
        color: #ffd1da;
      }}

      .banner {{
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 10px;
        background: rgba(0,0,0,0.16);
      }}
      .banner.warn {{
        border-color: rgba(255,212,122,0.45);
        background: rgba(255,212,122,0.08);
      }}
      .banner .title {{
        font-family: var(--mono);
        font-size: 12px;
        color: #ffd47a;
        margin-bottom: 6px;
      }}
      .banner .body {{
        font-family: var(--mono);
        font-size: 12px;
        color: var(--text);
        white-space: pre-wrap;
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>{_escape_html(t)}</h1>
      <div class="right">
        <span class="chip" id="chipBase">base: (loading)</span>
        <span class="chip" id="chipModel">model: (unknown)</span>
        <span class="chip" id="chipHealth">health: (unknown)</span>
      </div>
    </header>

    <main>
      <section class="panel" id="panelTasks">
        <div class="hd">
          <h2>Tasks</h2>
          <div class="row">
            <button class="btn small" id="btnRefresh">Refresh</button>
          </div>
        </div>
        <div class="bd">
          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 200px;">
              <label>API key (stored locally in your browser)</label>
              <input id="apiKey" type="password" class="mono" placeholder="Bearer token (e.g. local-dev-key)" />
            </div>
            <div style="min-width:120px; align-self:flex-end;">
              <button class="btn primary" id="btnSaveKey">Save</button>
            </div>
          </div>

          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 160px;">
              <label>Filter status</label>
              <select id="statusFilter">
                <option value="">(all)</option>
                <option value="pending">pending</option>
                <option value="running">running</option>
                <option value="paused">paused</option>
                <option value="partial">partial</option>
                <option value="completed">completed</option>
                <option value="failed">failed</option>
              </select>
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Filter project</label>
              <input id="projectFilter" class="mono" placeholder="(all)" />
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Limit</label>
              <input id="limit" type="number" value="30" />
            </div>
          </div>

          <div class="row" style="margin-bottom:10px;">
            <div style="flex:1; min-width: 160px;">
              <label>Filter task type</label>
              <select id="typeFilter">
                <option value="">(all)</option>
                <option value="agent">agent</option>
                <option value="research">research</option>
                <option value="repo_audit">repo_audit</option>
                <option value="issue_triage">issue_triage</option>
                <option value="writing">writing</option>
              </select>
            </div>
            <div style="flex:1; min-width: 160px;">
              <label>Filter pack/preset</label>
              <input id="packFilter" class="mono" placeholder="e.g. repo_onboarding / release_review" />
            </div>
          </div>

          <div id="tasks" class="tasks"></div>
          <div class="muted" id="tasksEmpty" style="display:none; margin-top:10px;">(no tasks found)</div>
          <div class="err" id="tasksErr" style="display:none; margin-top:10px;"></div>
        </div>
      </section>

      <section class="panel" id="panelDetail">
        <div class="hd">
          <h2>Task Detail</h2>
          <div class="row">
            <button class="btn small" id="btnContinue">Continue</button>
            <button class="btn small ok" id="btnApprove">Approve</button>
            <button class="btn small danger" id="btnReject">Reject</button>
            <button class="btn small primary" id="btnOpenPrimary">Open primary</button>
          </div>
        </div>
        <div class="bd">
          <div class="muted" id="noSelection">Select a task to view details.</div>

          <div id="detail" style="display:none;">
            <div id="approvalBox" class="banner warn" style="display:none; margin-bottom:12px;">
              <div class="title">Approval required</div>
              <div class="body" id="approvalText"></div>
            </div>
            <div class="kv">
              <div class="k">task_id</div><div class="v" id="dTaskId" style="font-family:var(--mono);"></div>
              <div class="k">status</div><div class="v" id="dStatus"></div>
              <div class="k">type</div><div class="v" id="dType" style="font-family:var(--mono);"></div>
              <div class="k">step</div><div class="v" id="dStep"></div>
              <div class="k">retry</div><div class="v" id="dRetry"></div>
              <div class="k">primary</div><div class="v" id="dPrimary"></div>
              <div class="k">verdict</div><div class="v" id="dVerdict"></div>
              <div class="k">pack/preset</div><div class="v" id="dPack"></div>
            </div>

            <div class="grid2" style="margin-top:12px;">
              <div>
                <div class="muted" style="margin-bottom:6px;">Final summary (or last completed)</div>
                <div class="pre" id="dFinal"></div>
              </div>
              <div>
                <div class="muted" style="margin-bottom:6px;">Current step</div>
                <div class="pre" id="dCurrent"></div>
              </div>
            </div>

            <div class="grid2" style="margin-top:12px;">
              <div>
                <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                  <div class="muted">Artifacts</div>
                  <div class="muted" id="dArtifactDir" style="font-family:var(--mono); font-size:11px;"></div>
                </div>
                <div class="files" id="files"></div>
              </div>
              <div>
                <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                  <div class="muted">Logs (tail)</div>
                  <div>
                    <button class="btn small" id="btnRefreshDetail">Refresh detail</button>
                  </div>
                </div>
                <div class="pre" id="logs"></div>
              </div>
            </div>

            <div style="margin-top:12px;">
              <div class="row" style="justify-content: space-between; margin-bottom:6px;">
                <div class="muted">Artifact preview</div>
                <div class="row">
                  <label class="muted" style="margin-right:6px;">tail</label>
                  <input id="tailLines" type="number" value="200" style="width:90px;" />
                </div>
              </div>
              <div class="pre" id="preview">(select an artifact)</div>
            </div>
          </div>

          <div style="height: 14px;"></div>
          <div class="panel" style="border-radius: 12px;">
            <div class="hd"><h2>Launch Pack</h2></div>
            <div class="bd">
              <div class="row" style="margin-bottom:10px;">
                <div style="flex:1; min-width: 240px;">
                  <label>Pack</label>
                  <select id="packSelect"></select>
                </div>
                <div style="flex:1; min-width: 160px;">
                  <label>project_id</label>
                  <input id="packProject" class="mono" placeholder="default" />
                </div>
                <div style="flex:1; min-width: 160px;">
                  <label>conversation_id</label>
                  <input id="packConv" class="mono" placeholder="default" />
                </div>
              </div>
              <div class="grid2" style="margin-bottom:10px;">
                <div>
                  <label>path</label>
                  <input id="packPath" class="mono" placeholder="." />
                </div>
                <div>
                  <label>output_path (stateful packs)</label>
                  <input id="packOutput" class="mono" placeholder="sandbox_test/release_prep.md" />
                </div>
              </div>
              <div class="grid2" style="margin-bottom:10px;">
                <div>
                  <label>question</label>
                  <textarea id="packQuestion" placeholder="Optional"></textarea>
                </div>
                <div>
                  <label>issue</label>
                  <textarea id="packIssue" placeholder="Optional"></textarea>
                </div>
              </div>
              <div class="row" style="justify-content: space-between;">
                <div class="muted" id="packInfo"></div>
                <button class="btn primary" id="btnRunPack">Run pack</button>
              </div>
              <div class="pre" id="packResult" style="margin-top:10px; max-height: 220px; display:none;"></div>
            </div>
          </div>
        </div>
      </section>
    </main>

    <script>
      const base = window.location.origin;
      document.getElementById("chipBase").textContent = "base: " + base;

      const LS_KEY = "staragent_api_key";
      const apiKeyEl = document.getElementById("apiKey");
      apiKeyEl.value = localStorage.getItem(LS_KEY) || "";
      document.getElementById("btnSaveKey").addEventListener("click", () => {{
        localStorage.setItem(LS_KEY, apiKeyEl.value.trim());
        refreshAll();
      }});

      // Optional URL params for demoability/onboarding links.
      // These are best-effort and never bypass API auth.
      // Supported:
      // - api_key: store in localStorage for this origin
      // - project/status/type/pack: set filters
      // - task_id: auto-open task detail
      // - artifact: auto-preview artifact in the selected task
      let autoTaskId = null;
      let autoArtifact = null;
      (function applyUrlParams() {{
        try {{
          const u = new URL(window.location.href);
          // Prefer hash fragment for API key so it is not sent to the server (and won't show up in access logs).
          // Example: /dashboard#api_key=local-dev-key
          const hashParams = new URLSearchParams(String(u.hash || "").replace(/^#/, ""));
          const apiKey = (hashParams.get("api_key") || hashParams.get("key") || "").trim();
          const project = (u.searchParams.get("project") || "").trim();
          const status = (u.searchParams.get("status") || "").trim();
          const type = (u.searchParams.get("type") || "").trim();
          const pack = (u.searchParams.get("pack") || "").trim();
          autoTaskId = (u.searchParams.get("task_id") || u.searchParams.get("task") || "").trim() || null;
          autoArtifact = (u.searchParams.get("artifact") || "").trim() || null;

          let touched = false;
          if (apiKey) {{
            localStorage.setItem(LS_KEY, apiKey);
            apiKeyEl.value = apiKey;
            touched = true;
          }}
          if (project) {{
            document.getElementById("projectFilter").value = project;
            touched = true;
          }}
          if (status) {{
            document.getElementById("statusFilter").value = status;
            touched = true;
          }}
          if (type) {{
            document.getElementById("typeFilter").value = type;
            touched = true;
          }}
          if (pack) {{
            document.getElementById("packFilter").value = pack;
            touched = true;
          }}
          if (autoTaskId || autoArtifact) touched = true;

          // Remove fragment/query helpers to avoid leaking ids via copy/paste history.
          if (touched && window.history && window.history.replaceState) {{
            const clean = u.pathname; // keep it simple; no need to preserve params for operator UI
            window.history.replaceState({{}}, "", clean);
          }}
        }} catch (e) {{
          // Ignore.
        }}
      }})();

      function authHeader() {{
        const v = (localStorage.getItem(LS_KEY) || "").trim();
        if (!v) return {{}};
        // Accept either raw token or full "Bearer ..." string.
        const token = v.toLowerCase().startsWith("bearer ") ? v.slice(7).trim() : v;
        return {{ "Authorization": "Bearer " + token }};
      }}

      async function apiFetch(path, opts={{}}) {{
        const headers = Object.assign({{"Content-Type":"application/json"}}, authHeader(), (opts.headers||{{}}));
        const res = await fetch(base + path, Object.assign({{}}, opts, {{ headers }}));
        if (!res.ok) {{
          const text = await res.text();
          throw new Error(res.status + " " + res.statusText + ": " + text);
        }}
        const ct = res.headers.get("content-type") || "";
        if (ct.includes("application/json")) return await res.json();
        return await res.text();
      }}

      function fmtStatus(s) {{
        s = (s||"").toLowerCase();
        return s || "unknown";
      }}

      function setText(id, v) {{
        const el = document.getElementById(id);
        if (el) el.textContent = (v === null || v === undefined) ? "" : String(v);
      }}

      function el(tag, cls, txt) {{
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (txt !== undefined) e.textContent = txt;
        return e;
      }}

      let selectedTaskId = null;
      let selectedPrimaryName = null;

      async function refreshHeader() {{
        try {{
          const h = await apiFetch("/health");
          document.getElementById("chipHealth").textContent = "health: ok";
          document.getElementById("chipModel").textContent = "model: " + (h.default_model || "(unknown)");
        }} catch (e) {{
          document.getElementById("chipHealth").textContent = "health: error";
        }}
      }}

      async function refreshTasks() {{
        const tasksEl = document.getElementById("tasks");
        const emptyEl = document.getElementById("tasksEmpty");
        const errEl = document.getElementById("tasksErr");
        errEl.style.display = "none";
        emptyEl.style.display = "none";
        tasksEl.innerHTML = "";

        const status = document.getElementById("statusFilter").value;
        const project = (document.getElementById("projectFilter").value || "").trim();
        const limit = parseInt(document.getElementById("limit").value || "30", 10);
        let qs = `?limit=${{Math.max(1, Math.min(limit, 200))}}&offset=0`;
        if (status) qs += `&status=${{encodeURIComponent(status)}}`;
        if (project) qs += `&project_id=${{encodeURIComponent(project)}}`;
        try {{
          const out = await apiFetch("/v1/tasks" + qs);
          let tasks = out.tasks || [];

          // Client-side filters (API is kept stable; no new query params needed).
          const typeFilter = (document.getElementById("typeFilter").value || "").trim().toLowerCase();
          const packFilter = (document.getElementById("packFilter").value || "").trim().toLowerCase();
          if (typeFilter) {{
            tasks = tasks.filter(t => String(t.task_type || "").toLowerCase() === typeFilter);
          }}
          if (packFilter) {{
            tasks = tasks.filter(t => {{
              const aj = t.artifacts_json || {{}};
              const pack = String(aj.pack_name || "").toLowerCase();
              const preset = String(aj.preset || aj.pack_preset || "").toLowerCase();
              const conv = String(t.conversation_id || "").toLowerCase();
              return pack.includes(packFilter) || preset.includes(packFilter) || conv.includes(packFilter);
            }});
          }}
          if (!tasks.length) {{
            emptyEl.style.display = "block";
            return;
          }}
          for (const t of tasks) {{
            const item = el("div", "task" + (t.task_id === selectedTaskId ? " active" : ""), "");
            const top = el("div", "top", "");
            const id = el("div", "id", (t.task_id || "").slice(0, 8) + "…" );
            const st = el("div", "status " + fmtStatus(t.status), fmtStatus(t.status));
            top.appendChild(id);
            top.appendChild(st);
            item.appendChild(top);

            const meta = el("div", "meta", "");
            meta.appendChild(el("div", "", "type: " + (t.task_type || "-")));
            meta.appendChild(el("div", "", "step: " + (t.current_step_index ?? "-") + "/" + (t.max_steps ?? "-")));
            meta.appendChild(el("div", "", "retry: " + (t.retry_count ?? 0)));
            const aj = t.artifacts_json || {{}};
            const pack = aj.pack_name ? ("pack: " + aj.pack_name) : (aj.preset ? ("preset: " + aj.preset) : ("proj: " + (t.project_id || "-")));
            meta.appendChild(el("div", "", pack));
            item.appendChild(meta);

            item.addEventListener("click", () => {{
              selectedTaskId = t.task_id;
              refreshTasks();
              loadTaskDetail(t.task_id);
            }});
            tasksEl.appendChild(item);
          }}
        }} catch (e) {{
          errEl.textContent = "Error loading tasks: " + e.message;
          errEl.style.display = "block";
        }}
      }}

      async function loadTaskDetail(taskId) {{
        document.getElementById("noSelection").style.display = "none";
        document.getElementById("detail").style.display = "block";
        setText("preview", "(select an artifact)");
        setText("logs", "(loading)");
        document.getElementById("files").innerHTML = "";
        setText("dTaskId", taskId);

        try {{
          const [inspect, summary, artifacts, logs] = await Promise.all([
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/inspect`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/summary`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/artifacts`),
            apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/logs?tail_steps=30`),
          ]);

          const tr = (inspect.task || {{}});
          const prog = (inspect.progress || {{}});
          const cur = (prog.current_step || {{}});
          const primary = (inspect.primary_artifact || artifacts.primary_artifact || {{}});
          selectedPrimaryName = primary.name || null;

          setText("dStatus", tr.status || "");
          setText("dType", tr.task_type || "");
          setText("dStep", (tr.current_step_index ?? "-") + "/" + (tr.max_steps ?? "-"));
          setText("dRetry", tr.retry_count ?? 0);
          setText("dVerdict", tr.final_verdict || "");
          setText("dPrimary", primary.name ? `${{primary.name}} (${{primary.exists ? "ok" : "missing"}})` : "-");
          setText("dArtifactDir", artifacts.artifact_dir || "");

          const aj = tr.artifacts_json || {{}};
          const packName = aj.pack_name || "";
          const presetName = aj.preset || aj.pack_preset || "";
          const packDisp = packName ? ("pack: " + packName) : (presetName ? ("preset: " + presetName) : "-");
          setText("dPack", packDisp);

          const final = (summary.task || {{}}).final_summary || "";
          setText("dFinal", final || "(none)");
          const curTxt = [
            `#${{cur.step_index ?? "-"}} ${{cur.step_type || ""}} [${{cur.status || ""}}]`,
            (cur.instruction || ""),
          ].filter(Boolean).join("\\n\\n");
          setText("dCurrent", curTxt || "(none)");

          // logs
          const logsArr = logs.logs || [];
          const logLines = logsArr.map(x => {{
            const head = `#${{x.step_index}} ${{x.step_type}} [${{x.status}}] (attempt=${{x.attempt_count}})`;
            const instr = x.instruction ? ("instr: " + x.instruction) : "";
            const out = x.output_summary ? ("out: " + String(x.output_summary).slice(0, 700)) : "";
            return [head, instr, out].filter(Boolean).join("\\n");
          }}).join("\\n\\n");
          setText("logs", logLines || "(no logs)");

          // artifacts list
          const filesEl = document.getElementById("files");
          filesEl.innerHTML = "";
          const files = artifacts.files || [];
          if (!files.length) {{
            filesEl.appendChild(el("div", "muted", "(no artifacts)"));
          }} else {{
            for (const f of files) {{
              const row = el("div", "file", "");
              const left = el("div", "", "");
              const nm = el("div", "name", (f.is_primary ? "★ " : "") + f.name);
              left.appendChild(nm);
              row.appendChild(left);
              row.appendChild(el("div", "tag", (f.type || "") + "  " + (f.size_bytes ? (f.size_bytes + "B") : "")));
              row.addEventListener("click", () => previewArtifact(taskId, f.name, f.type));
              filesEl.appendChild(row);
            }}
          }}

          // approval clarity
          const apprBox = document.getElementById("approvalBox");
          const apprText = document.getElementById("approvalText");
          apprBox.style.display = "none";
          apprText.textContent = "";
          const pending = (aj && aj.pending_approval) ? aj.pending_approval : null;
          if (pending && pending.tool_call) {{
            const tc = pending.tool_call;
            const fn = (tc.function || {{}}).name || "unknown";
            let args = {{}};
            try {{
              args = JSON.parse((tc.function || {{}}).arguments || "{{}}");
            }} catch (e) {{
              args = {{ raw_arguments: (tc.function || {{}}).arguments || "" }};
            }}
            const lines = [];
            lines.push("action: " + fn);
            if (args.path) lines.push("target: " + args.path);
            if (pending.note) lines.push("note: " + pending.note);
            if (fn === "write_file") {{
              const contentLen = (args.content || "").length;
              lines.push("will: write/overwrite file");
              lines.push("content_bytes: " + contentLen);
              if (String(args.path || "").startsWith("sandbox_test/")) {{
                lines.push("destination: sandbox_test (safe by default)");
              }}
            }}
            apprText.textContent = lines.join("\\n");
            apprBox.style.display = "block";
          }}

          // Action buttons state
          const st = String(tr.status || "").toLowerCase();
          const btnC = document.getElementById("btnContinue");
          const btnA = document.getElementById("btnApprove");
          const btnR = document.getElementById("btnReject");
          // Allow starting newly-created tasks as well.
          btnC.disabled = !(st === "pending" || st === "partial" || st === "running");
          btnA.disabled = !(st === "paused" && pending);
          btnR.disabled = !(st === "paused" && pending);
        }} catch (e) {{
          setText("dFinal", "Error loading task detail: " + e.message);
        }}
      }}

      async function previewArtifact(taskId, name, type) {{
        const tail = parseInt(document.getElementById("tailLines").value || "200", 10);
        let fmt = "text";
        if ((type || "").toLowerCase() === "json" || (name || "").toLowerCase().endsWith(".json")) fmt = "json";
        try {{
          const out = await apiFetch(`/v1/tasks/${{encodeURIComponent(taskId)}}/artifacts/${{encodeURIComponent(name)}}?format=${{encodeURIComponent(fmt)}}&tail_lines=${{encodeURIComponent(String(Math.max(0, Math.min(tail, 2000))))}}`);
          const extra = out.truncated ? "\\n\\n(warn) truncated" : "";
          setText("preview", out.content + extra);
        }} catch (e) {{
          setText("preview", "Error previewing artifact: " + e.message);
        }}
      }}

      async function taskAction(action) {{
        if (!selectedTaskId) return;
        const body = {{
          action,
          max_step_advances: 6,
          max_duration_s: 50.0,
        }};
        if (action === "reject") {{
          const reason = prompt("Reject reason?", "rejected");
          body.reason = reason || "rejected";
        }}
        try {{
          await apiFetch(`/v1/tasks/${{encodeURIComponent(selectedTaskId)}}/continue`, {{
            method: "POST",
            body: JSON.stringify(body),
          }});
          await refreshTasks();
          await loadTaskDetail(selectedTaskId);
        }} catch (e) {{
          alert("Action failed: " + e.message);
        }}
      }}

      document.getElementById("btnContinue").addEventListener("click", () => taskAction("continue"));
      document.getElementById("btnApprove").addEventListener("click", () => taskAction("approve"));
      document.getElementById("btnReject").addEventListener("click", () => taskAction("reject"));
      document.getElementById("btnOpenPrimary").addEventListener("click", () => {{
        if (!selectedTaskId || !selectedPrimaryName) return;
        previewArtifact(selectedTaskId, selectedPrimaryName, (selectedPrimaryName || "").toLowerCase().endsWith(".json") ? "json" : "markdown");
      }});
      document.getElementById("btnRefresh").addEventListener("click", refreshAll);
      document.getElementById("btnRefreshDetail").addEventListener("click", () => selectedTaskId && loadTaskDetail(selectedTaskId));
      document.getElementById("statusFilter").addEventListener("change", refreshTasks);
      document.getElementById("projectFilter").addEventListener("change", refreshTasks);
      document.getElementById("typeFilter").addEventListener("change", refreshTasks);
      document.getElementById("packFilter").addEventListener("change", refreshTasks);

      // Packs
      async function loadPacks() {{
        const sel = document.getElementById("packSelect");
        const info = document.getElementById("packInfo");
        sel.innerHTML = "";
        info.textContent = "";
        try {{
          const out = await apiFetch("/v1/presets/packs");
          const packs = out.packs || [];
          for (const p of packs) {{
            const opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = p.name;
            opt.dataset.desc = p.description || "";
            opt.dataset.ro = p.read_only ? "true" : "false";
            opt.dataset.appr = p.may_require_approval ? "true" : "false";
            opt.dataset.primary = p.primary_artifact || "";
            sel.appendChild(opt);
          }}
          if (packs.length) {{
            sel.value = packs[0].name;
            updatePackInfo();
          }}
        }} catch (e) {{
          info.textContent = "Error loading packs: " + e.message;
        }}
      }}

      function updatePackInfo() {{
        const sel = document.getElementById("packSelect");
        const opt = sel.options[sel.selectedIndex];
        const info = document.getElementById("packInfo");
        if (!opt) return;
        const ro = opt.dataset.ro === "true" ? "read-only" : "stateful";
        const roLabel = ro === "read-only" ? "RO" : "STATEFUL";
        const appr = opt.dataset.appr === "true" ? "may require approval" : "no approval expected";
        const primary = opt.dataset.primary || "-";
        info.textContent = `${{opt.dataset.desc}}  (${{roLabel}}, primary=${{primary}}, ${{appr}})`;
      }}

      document.getElementById("packSelect").addEventListener("change", updatePackInfo);

      document.getElementById("btnRunPack").addEventListener("click", async () => {{
        const packName = document.getElementById("packSelect").value;
        const project = document.getElementById("packProject").value.trim() || "default";
        const conv = document.getElementById("packConv").value.trim() || "default";
        const path = document.getElementById("packPath").value.trim() || ".";
        const question = document.getElementById("packQuestion").value.trim() || null;
        const issue = document.getElementById("packIssue").value.trim() || null;
        const output_path = document.getElementById("packOutput").value.trim() || null;

        const body = {{
          project_id: project,
          conversation_id: conv,
          path,
          question,
          issue,
          output_path,
          run_now: true,
        }};
        const pre = document.getElementById("packResult");
        pre.style.display = "block";
        pre.textContent = "(running...)";
        try {{
          const out = await apiFetch(`/v1/presets/packs/${{encodeURIComponent(packName)}}/run`, {{
            method: "POST",
            body: JSON.stringify(body),
          }});
          pre.textContent = JSON.stringify(out, null, 2);
          // Best effort: auto-select last task.
          const runs = out.runs || [];
          for (let i=runs.length-1; i>=0; i--) {{
            const tid = (runs[i].task || {{}}).task_id;
            if (tid) {{
              selectedTaskId = tid;
              break;
            }}
          }}
          await refreshTasks();
          if (selectedTaskId) await loadTaskDetail(selectedTaskId);
        }} catch (e) {{
          pre.textContent = "Error: " + e.message;
        }}
      }});

      async function refreshAll() {{
        await refreshHeader();
        await loadPacks();
        await refreshTasks();
        if (selectedTaskId) {{
          await loadTaskDetail(selectedTaskId);
        }}
        if (autoTaskId) {{
          selectedTaskId = autoTaskId;
          await loadTaskDetail(autoTaskId);
          if (autoArtifact) {{
            // Let detail populate and then preview.
            setTimeout(() => {{
              previewArtifact(autoTaskId, autoArtifact, (autoArtifact || "").toLowerCase().endsWith(".json") ? "json" : "markdown");
              const p = document.getElementById("preview");
              if (p && p.scrollIntoView) {{
                p.scrollIntoView({{ block: "start" }});
              }}
            }}, 650);
          }}
        }}
      }}

      refreshAll();
    </script>
  </body>
</html>
"""


def _escape_html(s: Optional[str]) -> str:
    s = s or ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
