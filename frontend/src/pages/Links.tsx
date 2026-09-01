import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import FiberMap from "../components/FiberMap";
import OsmMap from "../components/OsmMap";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import { fmtCapacity, fmtFiberUsageItem } from "../util";
import type { FiberLink, Location } from "../types";

export default function Links() {
  const [links, setLinks] = useState<FiberLink[]>([]);
  const [locs, setLocs] = useState<Location[]>([]);
  const [edit, setEdit] = useState<FiberLink | "new" | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const [mapMode, setMapMode] = useState<"osm" | "offline">("osm");
  const [noNet, setNoNet] = useState(false);
  const rowRef = useRef<HTMLTableRowElement | null>(null);

  const load = () => {
    Promise.all([api<FiberLink[]>("/links"), api<Location[]>("/locations")])
      .then(([l, c]) => { setLinks(l); setLocs(c); })
      .catch((e) => setErr(e.message));
  };
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return links.filter((l) => {
      if (status === "active" && !l.is_active) return false;
      if (status === "inactive" && l.is_active) return false;
      if (!s) return true;
      return (
        l.name.toLowerCase().includes(s) ||
        l.a.name.toLowerCase().includes(s) ||
        l.b.name.toLowerCase().includes(s) ||
        (l.a.address || "").toLowerCase().includes(s) ||
        (l.b.address || "").toLowerCase().includes(s) ||
        (l.descr || "").toLowerCase().includes(s)
      );
    });
  }, [links, q, status]);

  const { sorted, sort } = useSort(filtered, (l, k) => {
    if (k === "name") return l.name;
    if (k === "route") return `${l.a.name} ${l.b.name}`;
    if (k === "capacity") return l.capacity ?? -1;
    if (k === "length") return l.length ?? -1;
    if (k === "descr") return l.descr || "";
    return l.name;
  });

  const onMapClick = (id: number) => {
    setHighlightId(id);
    setTimeout(() => rowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  };

  const activeN = links.filter((l) => l.is_active).length;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Линии связи</h1>
        <span className="muted small">оптические каналы: точка А — точка Б · линий: {links.length} · активных: {activeN}</span>
        <button className="btn primary" onClick={() => setEdit("new")} disabled={locs.length < 2}>+ Линия</button>
        {locs.length < 2 && <span className="muted small">нужно минимум 2 местоположения</span>}
      </div>
      {err && <div className="error">{err}</div>}

      {/* карта с наложением линий: OSM из интернета (как в phpIPAM) или встроенная offline */}
      <div className="card">
        <div className="card-title row">
          Карта линий
          <div className="map-mode-btns">
            <button className={"btn small " + (mapMode === "osm" ? "primary" : "ghost")} onClick={() => setMapMode("osm")}>OSM (интернет)</button>
            <button className={"btn small " + (mapMode === "offline" ? "primary" : "ghost")} onClick={() => setMapMode("offline")}>Встроенная (offline)</button>
          </div>
          <span className="muted small">клик по линии — строка в таблице</span>
        </div>
        {mapMode === "osm" ? (
          <OsmMap
            locations={locs}
            links={links}
            highlightId={highlightId}
            onLinkClick={onMapClick}
            onNoInternet={() => {
              setNoNet(true);
              setMapMode("offline"); // нет интернета — сами переключаем на встроенную
            }}
            onLoaded={() => setNoNet(false)}
          />
        ) : (
          <FiberMap locations={locs} links={links} highlightId={highlightId} onLinkClick={onMapClick} />
        )}
        {noNet && (
          <div className="warn small" style={{ marginTop: 8 }}>
            Тайлы OSM не загрузились (скорее всего, нет интернета) — показана встроенная карта.
            Когда интернет появится — нажмите «OSM (интернет)».
          </div>
        )}
      </div>

      <div className="vlan-search">
        <input className="input" placeholder="Поиск: название, точка, адрес, описание…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">Все статусы</option>
          <option value="active">Активные</option>
          <option value="inactive">Не активные</option>
        </select>
        {q.trim() !== "" && <span className="muted small">найдено: {sorted.length} из {links.length}</span>}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <Th label="Название" k="name" sort={sort} />
              <Th label="Маршрут" k="route" sort={sort} />
              <Th label="Ёмкость" k="capacity" sort={sort} />
              <Th label="Длина" k="length" sort={sort} />
              <Th label="Описание" k="descr" sort={sort} />
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((l) => (
              <tr key={l.id} ref={highlightId === l.id ? rowRef : undefined} className={highlightId === l.id ? "row-selected" : "clickable"} onClick={() => setHighlightId(l.id)}>
                <td>{l.name}</td>
                <td>
                  <span title={l.a.address || ""}>{l.a.name}</span>
                  <span className="muted"> → </span>
                  <span title={l.b.address || ""}>{l.b.name}</span>
                </td>
                <td>
                  <div className="mono">{fmtCapacity(l)}</div>
                  {/* каждое назначение своей строкой: «LAN: 8 волокон · 10 Гбит/с» */}
                  {(l.fiber_usage || []).map((u) => (
                    <div key={u.name} className="muted small mono">{fmtFiberUsageItem(u)}</div>
                  ))}
                </td>
                <td className="mono">{l.length != null ? `${l.length} км` : <span className="muted">—</span>}</td>
                <td className="muted">{l.descr || ""}</td>
                <td className="actions-cell" onClick={(e) => e.stopPropagation()}>
                  <button className="btn ghost small" onClick={() => setEdit(l)}>изменить</button>
                  <button
                    className="btn ghost small danger"
                    onClick={async () => {
                      if (confirm(`Удалить линию «${l.name}»?`)) {
                        await api(`/links/${l.id}`, { method: "DELETE" });
                        load();
                      }
                    }}
                  >удалить</button>
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={6} className="muted">{links.length === 0 ? "Линий пока нет — добавьте местоположения и первую линию" : "Ничего не найдено"}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {edit && <LinkModal link={edit === "new" ? null : edit} locations={locs} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function LinkModal({ link, locations, onClose, onSaved }: { link: FiberLink | null; locations: Location[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: link?.name ?? "",
    a_id: link ? String(link.a.id) : (locations[0] ? String(locations[0].id) : ""),
    b_id: link ? String(link.b.id) : (locations[1] ? String(locations[1].id) : ""),
    capacity: link?.capacity != null ? String(link.capacity) : "", // не редактируется в карточке, значение сохраняется
    fibers: link?.fibers != null ? String(link.fibers) : "",
    length: link?.length != null ? String(link.length) : "",
    is_active: link?.is_active ?? true,
    descr: link?.descr ?? "",
  });
  // назначение волокон: названия вводит пользователь сам (LAN, SAN, …);
  // speed — ёмкость назначения в Гбит/с (пусто/0 → не выводится)
  const [usage, setUsage] = useState<{ name: string; count: string; speed: string }[]>(
    link?.fiber_usage?.length
      ? link.fiber_usage.map((u) => ({ name: u.name, count: String(u.count), speed: u.speed != null ? String(u.speed) : "" }))
      : [{ name: "", count: "", speed: "" }],
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const setUsageRow = (i: number, patch: { name?: string; count?: string; speed?: string }) =>
    setUsage((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeUsageRow = (i: number) => setUsage((rows) => rows.filter((_, j) => j !== i));

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const body = {
        name: form.name.trim(),
        a_id: Number(form.a_id),
        b_id: Number(form.b_id),
        capacity: form.capacity.trim() === "" ? null : Number(form.capacity.replace(",", ".")),
        fibers: form.fibers.trim() === "" ? null : Math.round(Number(form.fibers)),
        length: form.length.trim() === "" ? null : Number(form.length.replace(",", ".")),
        fiber_usage: null as { name: string; count: number; speed: number | null }[] | null,
        is_active: form.is_active,
        descr: form.descr.trim() || null,
      };
      if (body.fibers != null && (isNaN(body.fibers) || body.fibers < 1)) throw new Error("Волокна: целое число ≥ 1");
      if (body.length != null && (isNaN(body.length) || body.length < 0)) throw new Error("Длина трассы: число ≥ 0 (км)");
      const usageClean = usage
        .map((r) => ({ name: r.name.trim(), count: r.count.trim(), speed: r.speed.trim() }))
        .filter((r) => r.name || r.count || r.speed);
      for (const r of usageClean) {
        if (!r.name) throw new Error("Назначение: укажите название (LAN, SAN, …)");
        const n = Math.round(Number(r.count.replace(",", ".")));
        if (isNaN(n) || n < 1) throw new Error(`Назначение «${r.name}»: волокна — целое число ≥ 1`);
        if (r.speed !== "") {
          const sp = Number(r.speed.replace(",", "."));
          if (isNaN(sp) || sp < 0) throw new Error(`Назначение «${r.name}»: скорость — число ≥ 0 (Гбит/с)`);
        }
      }
      body.fiber_usage = usageClean.length
        ? usageClean.map((r) => ({
            name: r.name,
            count: Math.round(Number(r.count.replace(",", "."))),
            speed: r.speed === "" ? null : Number(r.speed.replace(",", ".")),
          }))
        : null;
      if (link) await api(`/links/${link.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/links", { method: "POST", body: JSON.stringify(body) });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const locOpt = (id: string) => locations.find((l) => String(l.id) === id);
  const noCoords = (loc: Location | undefined) => !loc || loc.lat == null || loc.lng == null;

  return (
    <Modal title={link ? `Линия: ${link.name}` : "Новая линия связи"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Название</span>
        <input className="input" value={form.name} placeholder="напр. Минск — Гродно 10Г" onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="kv"><span>Точка А</span>
        <select className="input" value={form.a_id} onChange={(e) => setForm({ ...form, a_id: e.target.value })}>
          {locations.map((l) => <option key={l.id} value={l.id}>{l.name}{l.address ? ` — ${l.address}` : ""}</option>)}
        </select>
        {noCoords(locOpt(form.a_id)) && <span className="warn small">у точки нет координат — на карте не будет видна</span>}
      </div>
      <div className="kv"><span>Точка Б</span>
        <select className="input" value={form.b_id} onChange={(e) => setForm({ ...form, b_id: e.target.value })}>
          {locations.map((l) => <option key={l.id} value={l.id}>{l.name}{l.address ? ` — ${l.address}` : ""}</option>)}
        </select>
        {noCoords(locOpt(form.b_id)) && <span className="warn small">у точки нет координат — на карте не будет видна</span>}
      </div>
      <div className="kv"><span>Число волокон</span>
        <input className="input mono" value={form.fibers} placeholder="кол-во оптических волокон, напр. 32" onChange={(e) => setForm({ ...form, fibers: e.target.value })} />
      </div>
      <div className="kv"><span>Длина трассы</span>
        <input className="input mono" value={form.length} placeholder="км, напр. 310" onChange={(e) => setForm({ ...form, length: e.target.value })} />
      </div>
      <div className="kv"><span>Волокна по назначениям</span>
        <div className="small muted" style={{ marginBottom: 4 }}>назначение · сколько волокон · скорость (ёмкость) в Гбит/с</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {usage.map((r, i) => (
            <div key={i} className="inline" style={{ gap: 6 }}>
              <input
                className="input"
                style={{ maxWidth: 170 }}
                value={r.name}
                placeholder="назначение, напр. LAN или SAN"
                onChange={(e) => setUsageRow(i, { name: e.target.value })}
              />
              <input
                className="input mono"
                style={{ maxWidth: 90 }}
                value={r.count}
                placeholder="волокон"
                onChange={(e) => setUsageRow(i, { count: e.target.value })}
              />
              <input
                className="input mono"
                style={{ maxWidth: 110 }}
                value={r.speed}
                placeholder="Гбит/с"
                onChange={(e) => setUsageRow(i, { speed: e.target.value })}
              />
              <button type="button" className="btn ghost small" title="Удалить строку" onClick={() => removeUsageRow(i)}>✕</button>
            </div>
          ))}
          <button type="button" className="btn ghost small" onClick={() => setUsage((rows) => [...rows, { name: "", count: "", speed: "" }])}>
            + Назначение
          </button>
        </div>
        <span className="muted small">названия вносятся вручную (LAN, SAN, резерв, …) — не предопределены; скорость, если не указана или 0, на страницу не выводится</span>
      </div>
      <div className="kv"><span>Статус</span>
        <label className="small">
          <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />{" "}
          линия активна
        </label>
      </div>
      <div className="kv"><span>Описание</span>
        <input className="input" value={form.descr} onChange={(e) => setForm({ ...form, descr: e.target.value })} />
      </div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy || !form.name.trim() || form.a_id === form.b_id}>
          {busy ? "…" : "Сохранить"}
        </button>
        {form.a_id === form.b_id && <span className="warn small">точка А и точка Б должны быть разными</span>}
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}
