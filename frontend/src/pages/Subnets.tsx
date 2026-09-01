import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import type { OverviewItem, Subnet, Vlan } from "../types";
import { isG24 } from "../types";
import { buildG24Items, fmt, g24Parent, isInsideCidr, pctColor } from "../util";

const emptyForm = {
  name: "", cidr: "", vlan_id: 0, gateway: "", dhcp_start: "", dhcp_end: "",
  scan_enabled: false, scan_interval_s: 3600, descr: "",
};

export default function Subnets() {
  const [items, setItems] = useState<Subnet[]>([]);
  const [vlans, setVlans] = useState<Vlan[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [err, setErr] = useState("");
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const vlanFilter = sp.get("vlan");
  // ?g24=10.0.0.0 — перечень сетей внутри /24 (из блока /24 на Обзоре)
  const g24Filter = sp.get("g24");
  const vlanName = vlans.find((v) => String(v.id) === vlanFilter)?.name;
  const [q, setQ] = useState("");

  const load = () =>
    Promise.all([
      // для /24-группы грузим ВСЕ сети (без VLAN-фильтра) и фильтруем клиентом
      api<Subnet[]>(`/subnets${vlanFilter && !g24Filter ? `?vlan_id=${vlanFilter}` : ""}`),
      api<Vlan[]>("/vlans"),
    ])
      .then(([s, v]) => { setItems(s); setVlans(v); })
      .catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [vlanFilter, g24Filter]);

  // /24-группа: только сети, чей /24-родитель совпадает
  const baseItems = useMemo(
    () => (g24Filter ? items.filter((s) => g24Parent(s.cidr) === g24Filter) : items),
    [items, g24Filter],
  );
  const g24Sum = useMemo(() => {
    if (!g24Filter) return null;
    return {
      count: baseItems.length,
      total: baseItems.reduce((a, s) => a + s.total, 0),
      used: baseItems.reduce((a, s) => a + s.used + s.reserved, 0),
    };
  }, [g24Filter, baseItems]);

  // как на Обзоре: мелкие сети (мельче /24) — блок /24 (кроме страницы-списка /24)
  const grouped = useMemo<OverviewItem[]>(
    () => (g24Filter ? (baseItems as OverviewItem[]) : buildG24Items(baseItems)),
    [baseItems, g24Filter],
  );

  // у обычных сетей: сколько сетей лежит ВНУТРИ этой (для «N подсетей» под именем)
  const subCount = useMemo(() => {
    const m = new Map<number, number>();
    for (const a of items) {
      for (const b of items) {
        if (a.id !== b.id && isInsideCidr(b.cidr, a.cidr)) m.set(a.id, (m.get(a.id) || 0) + 1);
      }
    }
    return m;
  }, [items]);

  // живой поиск: имя, CIDR, VLAN, теги
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return grouped;
    return grouped.filter(
      (x) =>
        x.name.toLowerCase().includes(s) ||
        x.cidr.toLowerCase().includes(s) ||
        (x.vlan_name || "").toLowerCase().includes(s) ||
        (x.tags || []).some((t) => t.toLowerCase().includes(s)),
    );
  }, [grouped, q]);

  const { sorted, sort } = useSort(filtered, (s, k) => {
    if (k === "name") return s.name;
    if (k === "cidr") return s.cidr;
    if (k === "descr") return s.descr || "";
    if (k === "vlan") return s.vlan_name || "";
    if (k === "pct") return s.pct;
    if (k === "scan") return isG24(s) ? -1 : (s.scan_enabled ? Math.round((s.scan_interval_s || 3600) / 60) : -1);
    if (k === "last_scan") return s.last_scan_at || "";
    return s.name;
  });

  const itemKey = (x: OverviewItem) => (isG24(x) ? "g" + x.g24 : "s" + x.id);

  return (
    <div className="page">
      <div className="page-head">
        <h1>
          {g24Filter ? `Сети в ${g24Filter}/24` : "Сети"}{" "}
          {g24Filter && (
            <span className="badge" style={{ marginLeft: 8 }}>
              {g24Sum?.count ?? 0} подсетей
              <a style={{ marginLeft: 6, cursor: "pointer" }} onClick={() => nav("/subnets")}>✕</a>
            </span>
          )}
          {vlanFilter && (
            <span className="badge" style={{ marginLeft: 8 }}>
              VLAN: {vlanName || vlanFilter}
              <a style={{ marginLeft: 6, cursor: "pointer" }} onClick={() => nav("/subnets")}>✕</a>
            </span>
          )}
        </h1>
        <button className="btn primary" onClick={() => setShowAdd(true)}>+ Добавить сеть</button>
      </div>
      {g24Filter && g24Sum && (
        <div className="muted small" style={{ margin: "-6px 0 10px" }}>
          объединённый блок /24: занято {g24Sum.used} из {g24Sum.total} адресов · клик по сети — как обычно
        </div>
      )}
      {err && <div className="error">{err}</div>}

      <div className="vlan-search">
        <input className="input" placeholder="Поиск: имя, CIDR, VLAN, тег…" value={q} onChange={(e) => setQ(e.target.value)} />
        {q.trim() !== "" && <span className="muted small">найдено: {sorted.length} из {baseItems.length}</span>}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <Th label="Имя" k="name" sort={sort} />
              <Th label="CIDR" k="cidr" sort={sort} />
              <Th label="Описание" k="descr" sort={sort} />
              <Th label="VLAN" k="vlan" sort={sort} />
              <Th label="Заполнено" k="pct" sort={sort} />
              <Th label="Авто-скан" k="scan" sort={sort} />
              <Th label="Последний скан" k="last_scan" sort={sort} />
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => {
              const grp = isG24(s);
              const subN = grp ? s.subnets.length : (subCount.get(s.id) ?? 0);
              const openTo = grp ? `/subnets?g24=${s.g24}` : `/subnets/${s.id}`;
              return (
              <tr key={itemKey(s)}>
                <td>
                  <Link to={openTo} className="link">{grp ? s.cidr : s.name}</Link>
                  {subN > 0 && <div className="muted small">{subN} подсетей</div>}
                </td>
                <td className="mono"><Link to={openTo}>{s.cidr}</Link></td>
                <td className="cell-descr muted" title={s.descr || ""}>{s.descr || ""}</td>
                <td>
                  {s.vlan_name
                    ? <Link to={`/subnets?vlan=${s.vlan_id}`} title="Все сети этого VLAN">
                        <span className="badge" style={{ background: (s.vlan_color || "#334155") + "33", color: s.vlan_color || "#cbd5e1" }}>{s.vlan_name}</span>
                      </Link>
                    : "—"}
                </td>
                <td style={{ minWidth: 150 }}>
                  <div className="bar"><div className="bar-fill" style={{ width: `${s.pct}%`, background: pctColor(s.pct) }} /></div>
                  <span className="muted small">{s.used + s.reserved}/{s.total} · {s.pct}%</span>
                </td>
                <td>
                  {grp
                    ? <span className="muted">—</span>
                    : s.scan_enabled
                      ? <span className="tag tag-ok">каждые {Math.round((s.scan_interval_s || 3600) / 60)} мин</span>
                      : <span className="muted">выкл</span>}
                </td>
                <td className="muted small">{s.last_scan_at ? fmt(s.last_scan_at) : "—"}{s.last_error ? " ⚠" : ""}</td>
                <td className="actions-cell">
                  <Link className="btn ghost small" to={openTo}>{grp ? "подсети" : "открыть"}</Link>
                  {!grp && (
                    <button
                      className="btn ghost small danger"
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (confirm(`Удалить сеть ${s.cidr} и все её записи?`)) {
                          await api(`/subnets/${s.id}`, { method: "DELETE" });
                          load();
                        }
                      }}
                    >удалить</button>
                  )}
                </td>
              </tr>
              );
            })}
            {sorted.length === 0 && <tr><td colSpan={8} className="muted">{g24Filter ? "в этом /24 сетей нет" : items.length === 0 ? "сетей пока нет — добавьте первую" : "Ничего не найдено"}</td></tr>}
          </tbody>
        </table>
      </div>
      {showAdd && <AddModal vlans={vlans} onClose={() => setShowAdd(false)} onSaved={() => { setShowAdd(false); load(); }} />}
    </div>
  );
}

function AddModal({ vlans, onClose, onSaved }: { vlans: Vlan[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      await api("/subnets", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          vlan_id: Number(form.vlan_id) || null,
          scan_interval_s: Number(form.scan_interval_s) || 3600,
        }),
      });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title="Новая сеть" onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Имя</span><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Office-LAN" /></div>
      <div className="kv"><span>CIDR</span><input className="input mono" value={form.cidr} onChange={(e) => set("cidr", e.target.value)} placeholder="192.168.1.0/24" /></div>
      <div className="kv"><span>VLAN</span>
        <select className="input" value={form.vlan_id} onChange={(e) => set("vlan_id", e.target.value)}>
          <option value={0}>— без VLAN —</option>
          {vlans.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.vid})</option>)}
        </select>
      </div>
      <div className="kv"><span>Gateway</span><input className="input mono" value={form.gateway} onChange={(e) => set("gateway", e.target.value)} placeholder="192.168.1.1" /></div>
      <div className="kv"><span>DHCP от / до</span>
        <div className="inline">
          <input className="input mono" value={form.dhcp_start} onChange={(e) => set("dhcp_start", e.target.value)} placeholder="192.168.1.10" />
          <input className="input mono" value={form.dhcp_end} onChange={(e) => set("dhcp_end", e.target.value)} placeholder="192.168.1.200" />
        </div>
      </div>
      <div className="kv"><span>Описание</span><input className="input" value={form.descr} onChange={(e) => set("descr", e.target.value)} /></div>
      <div className="kv"><span>Авто-скан</span>
        <div className="inline">
          <label className="small"><input type="checkbox" checked={form.scan_enabled} onChange={(e) => set("scan_enabled", e.target.checked)} /> вкл</label>
          {form.scan_enabled && (
            <span className="small muted">
              каждые <input className="input narrow" type="number" min={60} value={form.scan_interval_s} onChange={(e) => set("scan_interval_s", e.target.value)} /> с
            </span>
          )}
        </div>
      </div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "Создание…" : "Создать"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}
