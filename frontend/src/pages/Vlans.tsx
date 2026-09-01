import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import type { Vlan } from "../types";

export default function Vlans() {
  const [items, setItems] = useState<Vlan[]>([]);
  const [edit, setEdit] = useState<Vlan | "new" | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");

  const load = () => api<Vlan[]>("/vlans").then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  // живой поиск по VID / имени / описанию / тегам
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter(
      (v) =>
        String(v.vid).includes(s) ||
        v.name.toLowerCase().includes(s) ||
        (v.descr || "").toLowerCase().includes(s) ||
        (v.tags || []).some((t) => t.toLowerCase().includes(s)),
    );
  }, [items, q]);

  const { sorted, sort } = useSort(filtered, (v, k) => {
    if (k === "vid") return v.vid;
    if (k === "name") return v.name;
    if (k === "tags") return (v.tags || []).join(", ");
    if (k === "count") return v.subnets_count ?? 0;
    if (k === "descr") return v.descr || "";
    return v.name;
  });

  return (
    <div className="page">
      <div className="page-head">
        <h1>VLAN</h1>
        <button className="btn primary" onClick={() => setEdit("new")}>+ Добавить VLAN</button>
      </div>
      {err && <div className="error">{err}</div>}

      <div className="vlan-search">
        <input className="input" placeholder="Поиск: VID, имя, описание, тег…" value={q} onChange={(e) => setQ(e.target.value)} />
        {q.trim() !== "" && <span className="muted small">найдено: {sorted.length} из {items.length}</span>}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <Th label="VID" k="vid" sort={sort} />
              <Th label="Имя" k="name" sort={sort} />
              <th>Цвет</th>
              <Th label="Сетей" k="count" sort={sort} />
              <Th label="Теги" k="tags" sort={sort} />
              <Th label="Описание" k="descr" sort={sort} />
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((v) => (
              <tr key={v.id}>
                <td className="mono">{v.vid}</td>
                <td>
                  <Link to={`/subnets?vlan=${v.id}`} title="Открыть сети этого VLAN" className="link">
                    <i className="dot" style={{ background: v.color || "#64748b" }} /> {v.name}
                  </Link>
                </td>
                <td>
                  <input
                    type="color"
                    className="color"
                    value={v.color || "#38bdf8"}
                    onChange={(e) =>
                      api(`/vlans/${v.id}`, {
                        method: "PUT",
                        body: JSON.stringify({ vid: v.vid, name: v.name, color: e.target.value, descr: v.descr || null, tags: (v.tags || []).join(", ") }),
                      }).then(load)
                    }
                  />
                </td>
                <td>
                  <Link to={`/subnets?vlan=${v.id}`} title="Открыть сети этого VLAN" className="link">{v.subnets_count ?? 0}</Link>
                </td>
                <td>{(v.tags || []).length > 0 ? <span className="chips">{v.tags!.map((t) => <span key={t} className="chip">{t}</span>)}</span> : <span className="muted">—</span>}</td>
                <td className="muted">{v.descr || ""}</td>
                <td className="actions-cell">
                  <button className="btn ghost small" onClick={() => setEdit(v)}>изменить</button>
                  <button
                    className="btn ghost small danger"
                    onClick={async () => {
                      if (confirm(`Удалить VLAN ${v.vid} «${v.name}»? Сети останутся без VLAN.`)) {
                        await api(`/vlans/${v.id}`, { method: "DELETE" });
                        load();
                      }
                    }}
                  >удалить</button>
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={7} className="muted">{items.length === 0 ? "VLAN пока нет" : "Ничего не найдено"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {edit && <VlanModal vlan={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function VlanModal({ vlan, onClose, onSaved }: { vlan: Vlan | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    vid: vlan?.vid ?? "",
    name: vlan?.name ?? "",
    color: vlan?.color || "#38bdf8",
    descr: vlan?.descr ?? "",
    tags: (vlan?.tags || []).join(", "),
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const save = async () => {
    setBusy(true);
    setErr("");
    const body = { vid: Number(form.vid), name: form.name, color: form.color, descr: form.descr || null, tags: form.tags };
    try {
      if (vlan) await api(`/vlans/${vlan.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/vlans", { method: "POST", body: JSON.stringify(body) });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={vlan ? `VLAN ${vlan.vid}` : "Новый VLAN"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>VID</span><input className="input narrow" type="number" min={1} max={4094} value={form.vid} onChange={(e) => setForm((f) => ({ ...f, vid: e.target.value }))} /></div>
      <div className="kv"><span>Имя</span><input className="input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></div>
      <div className="kv"><span>Цвет</span><input type="color" className="color" value={form.color} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} /></div>
      <div className="kv"><span>Теги</span><input className="input mono" placeholder="prod, finance (через запятую)" value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} /></div>
      <div className="kv"><span>Описание</span><input className="input" value={form.descr} onChange={(e) => setForm((f) => ({ ...f, descr: e.target.value }))} /></div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}
