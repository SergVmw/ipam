import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken } from "../api";
import Modal from "../components/Modal";
import { fmt } from "../util";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { DocFileOut, DocPageFull, DocSectionOut } from "../types";

marked.setOptions({ gfm: true, breaks: true });

// markdown → чистый HTML (sanitized) + токен в прямые ссылки на файлы
function renderMd(body: string): string {
  let html = DOMPurify.sanitize(marked.parse(body || "") as string, { ADD_ATTR: ["target"] });
  const tok = getToken();
  if (tok) {
    html = html.replace(
      /href="(\/api\/docs\/files\/\d+)"(?![^>]*token=)/g,
      (_m, href) => `href="${href}?token=${encodeURIComponent(tok)}"`,
    );
    // <img> тоже без Bearer-заголовка — токен в query
    html = html.replace(
      /src="(\/api\/docs\/files\/\d+)"(?![^>]*token=)/g,
      (_m, src) => `src="${src}?token=${encodeURIComponent(tok)}"`,
    );
  }
  return html;
}

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " МБ";
  if (n >= 1024) return (n / 1024).toFixed(0) + " КБ";
  return n + " Б";
}

// прямая ссылка на файл для <a>: браузер не шлёт Bearer-заголовок, берём ?token=
function tokHref(url: string): string {
  const t = getToken();
  return t ? url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t) : url;
}

const TABLE_TPL = `
| Колонка 1 | Колонка 2 | Колонка 3 |
| --- | --- | --- |
|  |  |  |
`;

type UploadTarget = { kind: "section" | "page"; id: number } | null;

export default function Docs() {
  const [tree, setTree] = useState<DocSectionOut[]>([]);
  const [page, setPage] = useState<DocPageFull | null>(null);
  const [selPage, setSelPage] = useState<number | null>(null);
  const [me, setMe] = useState<any>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<{ title: string; body: string } | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [openSec, setOpenSec] = useState<Set<number>>(new Set());
  const [uploadTarget, setUploadTarget] = useState<UploadTarget>(null);
  const [fileEdit, setFileEdit] = useState<{ file: DocFileOut; content: string } | null>(null);
  const [fileEditErr, setFileEditErr] = useState("");
  const [savingFile, setSavingFile] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const imgRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [imgUploading, setImgUploading] = useState(false);

  const canWrite = me && (me.role === "admin" || me.role === "operator");

  const load = useCallback(async () => {
    try {
      const t = await api<DocSectionOut[]>("/docs");
      setTree(t);
      setOpenSec((prev) => (prev.size ? prev : new Set(t.map((s) => s.id))));
      return t;
    } catch (e: any) {
      setErr(e.message);
      return null;
    }
  }, []);

  const loadPage = useCallback(async (id: number) => {
    try {
      setPage(await api<DocPageFull>(`/docs/pages/${id}`));
    } catch (e: any) {
      setErr(e.message);
    }
  }, []);

  useEffect(() => {
    api<any>("/auth/me").then(setMe).catch(() => {});
    load().then((t) => {
      const first = t?.flatMap((s) => s.pages)[0];
      if (first) select(first.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = (id: number) => {
    setSelPage(id);
    setEditing(null);
    setShowPreview(false);
    setErr("");
    loadPage(id);
  };

  const flash = (m: string) => {
    setMsg(m);
    setTimeout(() => setMsg(""), 4000);
  };

  // --- markdown-тулбар: быстрые вставки в textarea ---
  const applyMd = (before: string, after = "", placeholder = "") => {
    const ta = taRef.current;
    if (!ta || !editing) return;
    const s = ta.selectionStart, e = ta.selectionEnd;
    const sel = ta.value.slice(s, e) || placeholder;
    const next = ta.value.slice(0, s) + before + sel + after + ta.value.slice(e);
    setEditing({ ...editing, body: next });
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(s + before.length, s + before.length + sel.length);
    });
  };

  const linePrefix = (prefix: string) => {
    const ta = taRef.current;
    if (!ta || !editing) return;
    const s = ta.selectionStart, e = ta.selectionEnd;
    const v = ta.value;
    const ls = v.lastIndexOf("\n", s - 1) + 1;
    const next = v.slice(0, ls) + prefix + v.slice(ls);
    setEditing({ ...editing, body: next });
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(s + prefix.length, e + prefix.length);
    });
  };

  const insertBlock = (block: string) => {
    const ta = taRef.current;
    if (!ta || !editing) return;
    const s = ta.selectionStart;
    const next = ta.value.slice(0, s) + block + ta.value.slice(ta.selectionEnd);
    setEditing({ ...editing, body: next });
    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(s + block.length, s + block.length);
    });
  };

  // --- разделы ---
  const toggleSec = (id: number) =>
    setOpenSec((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const addSection = async () => {
    const title = window.prompt("Название раздела (например: «Схемы», «Регламенты»):");
    if (!title || !title.trim()) return;
    try {
      const r = await api<{ id: number }>("/docs/sections", { method: "POST", body: JSON.stringify({ title }) });
      flash("Раздел создан");
      const t = await load();
      setOpenSec((prev) => new Set([...prev, r.id]));
      void t;
    } catch (e: any) { setErr(e.message); }
  };

  const renameSection = async (s: DocSectionOut) => {
    const title = window.prompt("Новое название раздела:", s.title);
    if (!title || title === s.title) return;
    try {
      await api(`/docs/sections/${s.id}`, { method: "PUT", body: JSON.stringify({ title }) });
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  const deleteSection = async (s: DocSectionOut) => {
    if (!window.confirm(`Удалить раздел «${s.title}» со всеми документами и файлами?`)) return;
    try {
      await api(`/docs/sections/${s.id}`, { method: "DELETE" });
      if (selPage && s.pages.some((p) => p.id === selPage)) { setSelPage(null); setPage(null); }
      flash("Раздел удалён");
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  // --- страницы ---
  const addPage = async (sectionId: number) => {
    const title = window.prompt("Название документа:");
    if (!title || !title.trim()) return;
    try {
      const r = await api<{ id: number }>("/docs/pages", { method: "POST", body: JSON.stringify({ section_id: sectionId, title }) });
      await load();
      select(r.id);
    } catch (e: any) { setErr(e.message); }
  };

  const renamePage = async (id: number, cur: string) => {
    const title = window.prompt("Новое название документа:", cur);
    if (!title || title === cur) return;
    try {
      await api(`/docs/pages/${id}`, { method: "PUT", body: JSON.stringify({ title }) });
      if (selPage === id) loadPage(id);
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  const deletePage = async (id: number, title: string) => {
    if (!window.confirm(`Удалить документ «${title}» вместе с файлами?`)) return;
    try {
      await api(`/docs/pages/${id}`, { method: "DELETE" });
      if (selPage === id) { setSelPage(null); setPage(null); }
      flash("Документ удалён");
      await load();
    } catch (e: any) { setErr(e.message); }
  };

  // --- редактирование документа ---
  const startEdit = () => {
    if (!page) return;
    setEditing({ title: page.title, body: page.body });
    setShowPreview(false);
  };

  const save = async () => {
    if (!page || !editing) return;
    setSaving(true);
    try {
      await api(`/docs/pages/${page.id}`, { method: "PUT", body: JSON.stringify({ title: editing.title, body: editing.body }) });
      setEditing(null);
      flash("Сохранено");
      await Promise.all([load(), loadPage(page.id)]);
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };

  // --- файлы: загрузка (в страницу или в раздел) ---
  const startUpload = (target: UploadTarget) => {
    setUploadTarget(target);
    fileRef.current?.click();
  };

  const upload = async (files: FileList | null) => {
    if (!files || !files.length || !uploadTarget) return;
    setUploading(true);
    try {
      const url = uploadTarget.kind === "page"
        ? `/api/docs/pages/${uploadTarget.id}/files`
        : `/api/docs/sections/${uploadTarget.id}/files`;
      for (const f of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", f);
        const res = await fetch(url, {
          method: "POST",
          headers: { Authorization: "Bearer " + (getToken() || "") },
          body: fd,
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.detail || res.statusText);
        }
      }
      flash(`Файлов загружено: ${files.length}`);
      await Promise.all([load(), selPage ? loadPage(selPage) : Promise.resolve()]);
    } catch (e: any) { setErr(e.message); }
    setUploading(false);
    setUploadTarget(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  // --- картинки: загрузить в документ и вставить ![](url) в позицию курсора ---
  const pickImages = () => {
    if (!page || imgUploading) return;
    imgRef.current?.click();
  };

  const onImagesPicked = async (files: FileList | null) => {
    if (!files || !files.length || !page) return;
    setImgUploading(true);
    setErr("");
    try {
      const inserted: { name: string; url: string }[] = [];
      for (const f of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", f);
        const res = await fetch(`/api/docs/pages/${page.id}/files`, {
          method: "POST",
          headers: { Authorization: "Bearer " + (getToken() || "") },
          body: fd,
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.detail || res.statusText);
        }
        const d = await res.json();
        inserted.push({ name: d.name, url: d.url });
      }
      const md = inserted.map((u) => `\n![${u.name}](${u.url})\n`).join("");
      const ta = taRef.current;
      if (ta && editing) {
        // вставка в позицию курсора (несколько картинок — одним блоком)
        const s = ta.selectionStart ?? ta.value.length;
        const e = ta.selectionEnd ?? s;
        const next = ta.value.slice(0, s) + md + ta.value.slice(e);
        setEditing({ ...editing, body: next });
        requestAnimationFrame(() => {
          ta.focus();
          ta.setSelectionRange(s + md.length, s + md.length);
        });
      } else if (editing) {
        setEditing({ ...editing, body: editing.body + md });
      }
      flash(`Картинка(и) вставлена в текст: ${inserted.length}`);
      await Promise.all([load(), loadPage(page.id)]);
    } catch (e: any) {
      setErr(e.message);
    }
    setImgUploading(false);
    if (imgRef.current) imgRef.current.value = "";
  };

  // --- файлы: правка содержимого ---
  const openFileEdit = async (f: DocFileOut) => {
    setFileEdit({ file: f, content: "" });
    setFileEditErr("");
    try {
      const res = await fetch(tokHref(f.url));
      if (!res.ok) throw new Error("Не удалось открыть файл");
      const text = await res.text();
      if (text.includes("\uFFFD")) throw new Error("Похоже, файл не текстовый (символы не декодируются)");
      setFileEdit((cur) => (cur ? { ...cur, content: text } : cur));
    } catch (e: any) {
      setFileEditErr(e.message);
    }
  };

  const saveFileEdit = async () => {
    if (!fileEdit) return;
    setSavingFile(true);
    try {
      await api(`/docs/files/${fileEdit.file.id}`, { method: "PUT", body: JSON.stringify({ content: fileEdit.content }) });
      flash("Файл сохранён");
      setFileEdit(null);
      await Promise.all([load(), selPage ? loadPage(selPage) : Promise.resolve()]);
    } catch (e: any) { setFileEditErr(e.message); }
    setSavingFile(false);
  };

  const deleteFile = async (fid: number, name: string) => {
    if (!window.confirm(`Удалить файл «${name}»? Ссылки на него в тексте станут битыми.`)) return;
    try {
      await api(`/docs/files/${fid}`, { method: "DELETE" });
      flash("Файл удалён");
      await Promise.all([load(), selPage ? loadPage(selPage) : Promise.resolve()]);
    } catch (e: any) { setErr(e.message); }
  };

  const insertLink = (url: string, name: string, isImage = false) => {
    if (!page) return;
    const cur = editing ? editing.body : page.body;
    const md = isImage ? `![${name}](${url})` : `[${name}](${url})`;
    setEditing({ title: page.title, body: cur + (cur.endsWith("\n") || cur === "" ? "" : "\n") + md + "\n" });
    setShowPreview(false);
    flash(isImage ? "Картинка вставлена в конец текста — сохраните документ" : "Ссылка вставлена в конец текста — сохраните документ");
  };

  const sectionOf = page ? tree.find((s) => s.id === page.section_id)?.title : "";

  // строка файла (используется и в разделе слева, и в таблице справа)
  const fileActions = (f: DocFileOut) => (
    <span className="docs-file-acts" onClick={(e) => e.stopPropagation()}>
      {f.editable && canWrite && (
        <button className="btn ghost small" title="Править содержимое" onClick={() => openFileEdit(f)}>✎</button>
      )}
      {canWrite && (
        <button className="btn ghost small danger" title="Удалить файл" onClick={() => deleteFile(f.id, f.name)}>🗑</button>
      )}
    </span>
  );

  return (
    <div>
      <div className="page-head">
        <h1>Документация</h1>
        <span className="muted small">разделы → документы (markdown) + файлы</span>
        {msg && <span className="good small" style={{ marginLeft: "auto" }}>{msg}</span>}
      </div>
      {err && <div className="error" style={{ marginBottom: 10 }}>{err} <button className="btn ghost small" onClick={() => setErr("")}>✕</button></div>}

      <div className="docs-layout">
        {/* левая колонка: разделы (раскрываются) и документы */}
        <div className="docs-side card">
          <div className="card-title row">
            Разделы
            {canWrite && <button className="btn ghost small" style={{ marginLeft: "auto" }} onClick={addSection}>+ Раздел</button>}
          </div>
          {tree.length === 0 && (
            <div className="muted small" style={{ padding: 10 }}>
              Пока пусто. {canWrite ? "Создайте раздел, затем документы внутри." : "Документов ещё нет."}
            </div>
          )}
          {tree.map((s) => {
            const open = openSec.has(s.id);
            return (
              <div key={s.id} className="docs-sec">
                <div className="docs-sec-head" onClick={() => toggleSec(s.id)}>
                  <span className="docs-chev">{open ? "▾" : "▸"}</span>
                  <span className="docs-sec-title">{s.title}</span>
                  <span className="muted small">{s.pages.length}</span>
                  {canWrite && (
                    <span className="docs-sec-actions" onClick={(e) => e.stopPropagation()}>
                      <button className="btn ghost small" title="Загрузить файл в раздел" onClick={() => startUpload({ kind: "section", id: s.id })}>📎</button>
                      <button className="btn ghost small" title="Новый документ в разделе" onClick={() => addPage(s.id)}>+</button>
                      <button className="btn ghost small" title="Переименовать" onClick={() => renameSection(s)}>✎</button>
                      <button className="btn ghost small danger" title="Удалить раздел" onClick={() => deleteSection(s)}>🗑</button>
                    </span>
                  )}
                </div>
                {open && (
                  <>
                    {s.pages.map((p) => (
                      <div key={p.id} className={"docs-page" + (selPage === p.id ? " active" : "")} onClick={() => select(p.id)}>
                        <span className="docs-page-title">{p.title}</span>
                        {canWrite && (
                          <span className="docs-page-actions" onClick={(e) => e.stopPropagation()}>
                            <button className="btn ghost small" title="Переименовать" onClick={() => renamePage(p.id, p.title)}>✎</button>
                            <button className="btn ghost small danger" title="Удалить" onClick={() => deletePage(p.id, p.title)}>🗑</button>
                          </span>
                        )}
                      </div>
                    ))}
                    {s.files.length > 0 && (
                      <div className="docs-sec-files">
                        {s.files.map((f) => (
                          <div key={f.id} className="docs-file-row">
                            <a href={tokHref(f.url)} target="_blank" rel="noreferrer" className="docs-file-name" title={f.name}>
                              {f.editable ? "📝" : "📄"} {f.name}
                            </a>
                            <span className="muted small">{fmtSize(f.size)}</span>
                            {fileActions(f)}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>

        {/* правая колонка: документ */}
        <div className="docs-main card">
          {!page && <div className="muted" style={{ padding: 30 }}>Выберите документ слева (или создайте новый)</div>}
          {page && (
            <>
              <div className="docs-doc-head">
                <div>
                  <div className="muted small">{sectionOf}</div>
                  {editing ? (
                    <input className="input docs-title-input" value={editing.title} onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                  ) : (
                    <h2 className="docs-doc-title">{page.title}</h2>
                  )}
                  <div className="muted small">
                    обновлено: {page.updated_at ? fmt(page.updated_at) : "—"}{page.updated_by ? ` · ${page.updated_by}` : ""}
                  </div>
                </div>
                <div className="docs-toolbar">
                  {canWrite && !editing && <button className="btn small" onClick={startEdit}>Править</button>}
                  {canWrite && editing && (
                    <>
                      <button className="btn small primary" onClick={save} disabled={saving}>{saving ? "…" : "Сохранить"}</button>
                      <button className="btn small ghost" onClick={() => { setEditing(null); setShowPreview(false); }}>Отмена</button>
                    </>
                  )}
                  {editing && (
                    <>
                      <button className={"btn small " + (showPreview ? "primary" : "ghost")} onClick={() => setShowPreview(!showPreview)}>Предпросмотр</button>
                      <span className="muted small">markdown</span>
                    </>
                  )}
                </div>
              </div>

              {/* тело документа */}
              {editing ? (
                <div className="docs-edit">
                  {!showPreview && (
                    <>
                      <div className="md-toolbar">
                        <button type="button" title="Полужирный (Ctrl+B)" onClick={() => applyMd("**", "**", "текст")}><b>B</b></button>
                        <button type="button" title="Курсив" onClick={() => applyMd("*", "*", "текст")}><i>I</i></button>
                        <span className="md-sep" />
                        <button type="button" title="Заголовок 1" onClick={() => linePrefix("# ")}>H1</button>
                        <button type="button" title="Заголовок 2" onClick={() => linePrefix("## ")}>H2</button>
                        <button type="button" title="Заголовок 3" onClick={() => linePrefix("### ")}>H3</button>
                        <span className="md-sep" />
                        <button type="button" title="Код (инлайн)" onClick={() => applyMd("`", "`", "код")}>{`<>`}</button>
                        <button type="button" title="Блок кода" onClick={() => applyMd("\n```\n", "\n```\n", "код")}>{`{ }`}</button>
                        <button type="button" title="Ссылка" onClick={() => applyMd("[", "](https://)", "текст ссылки")}>🔗</button>
                        <button type="button" title="Вставить картинку (загрузится в документ)" onClick={pickImages} disabled={imgUploading}>
                          {imgUploading ? "…" : "🖼"}
                        </button>
                        <span className="md-sep" />
                        <button type="button" title="Список" onClick={() => linePrefix("- ")}>•—</button>
                        <button type="button" title="Нумерованный список" onClick={() => linePrefix("1. ")}>1.—</button>
                        <button type="button" title="Цитата" onClick={() => linePrefix("> ")}>❝</button>
                        <button type="button" title="Таблица" onClick={() => insertBlock(TABLE_TPL)}>⊞</button>
                        <button type="button" title="Разделитель" onClick={() => insertBlock("\n---\n")}>―</button>
                      </div>
                      <textarea
                        ref={taRef}
                        className="input docs-body-input"
                        value={editing.body}
                        onChange={(e) => setEditing({ ...editing, body: e.target.value })}
                        placeholder={"# Заголовок\n\nТекст в markdown: **жирный**, `код`, таблицы, списки, ссылки на файлы.\n\nКартинки — кнопкой 🖼 в тулбаре (загрузятся в документ и вставятся в текст)."}
                      />
                    </>
                  )}
                  {showPreview && (
                    <div className="md-body" dangerouslySetInnerHTML={{ __html: renderMd(editing.body) }} />
                  )}
                </div>
              ) : (
                <div className="md-body" dangerouslySetInnerHTML={{ __html: renderMd(page.body) }} />
              )}

              {/* файлы страницы */}
              <div className="docs-files">
                <div className="card-title row" style={{ marginTop: 18 }}>
                  Файлы
                  <span className="muted small">{page.files.length}</span>
                  {canWrite && (
                    <>
                      <button className="btn small" style={{ marginLeft: "auto" }} onClick={() => startUpload({ kind: "page", id: page.id })} disabled={uploading}>
                        {uploading ? "Загрузка…" : "Загрузить"}
                      </button>
                      <input ref={fileRef} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
                    </>
                  )}
                </div>
                <input ref={imgRef} type="file" accept="image/*" multiple hidden onChange={(e) => onImagesPicked(e.target.files)} />
                {page.files.length > 0 && (
                  <table className="table">
                    <thead>
                      <tr><th>Имя</th><th>Размер</th><th>Загружен</th><th></th></tr>
                    </thead>
                    <tbody>
                      {page.files.map((f) => (
                        <tr key={f.id}>
                          <td className="mono small"><a href={tokHref(f.url)} target="_blank" rel="noreferrer">{f.name}</a></td>
                          <td className="muted small">{fmtSize(f.size)}</td>
                          <td className="muted small">{f.uploaded_at ? fmt(f.uploaded_at) : "—"}</td>
                          <td className="actions-cell">
                            {canWrite && <button className="btn ghost small"
                              title={(f.mime || "").startsWith("image/") ? "Вставить картинку в документ (отобразится на странице)" : "Вставить markdown-ссылку в документ"}
                              onClick={() => insertLink(f.url, f.name, (f.mime || "").startsWith("image/"))}>
                              {(f.mime || "").startsWith("image/") ? "картинка в текст" : "ссылка в текст"}
                            </button>}
                            {f.editable && canWrite && (
                              <button className="btn ghost small" title="Править содержимое" onClick={() => openFileEdit(f)}>✎</button>
                            )}
                            {canWrite && <button className="btn ghost small danger" title="Удалить файл" onClick={() => deleteFile(f.id, f.name)}>🗑</button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {page.files.length === 0 && <div className="muted small">файлов нет — загрузите схемы, регламенты, PDF… (до 50 МБ на файл)</div>}
              </div>
            </>
          )}
        </div>
      </div>

      {/* правка файла */}
      {fileEdit && (
        <Modal title={`Правка файла: ${fileEdit.file.name}`} onClose={() => setFileEdit(null)}>
          <div className="muted small" style={{ marginBottom: 8 }}>
            {fmtSize(fileEdit.file.size)} · UTF-8 · сохранение перезаписывает файл
          </div>
          {fileEditErr && <div className="error" style={{ marginBottom: 8 }}>{fileEditErr}</div>}
          <textarea
            className="input docs-body-input"
            value={fileEdit.content}
            spellCheck={false}
            onChange={(e) => setFileEdit({ ...fileEdit, content: e.target.value })}
          />
          <div className="btn-row" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={saveFileEdit} disabled={savingFile}>{savingFile ? "Сохранение…" : "Сохранить"}</button>
            <button className="btn ghost" onClick={() => setFileEdit(null)}>Отмена</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
