import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import FiberMap from "../components/FiberMap";
import OsmMap from "../components/OsmMap";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import {
  fmtCapacity, fmtFiberUsageItem, fmtRouteLen, fmtRouteSegsDetail,
  routeSegs,
} from "../util";
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
        (l.route?.via ?? []).some((v) => v.name.toLowerCase().includes(s) || (v.address || "").toLowerCase().includes(s)) ||
        (l.descr || "").toLowerCase().includes(s)
      );
    });
  }, [links, q, status]);

  const { sorted, sort } = useSort(filtered, (l, k) => {
    if (k === "name") return l.name;
    if (k === "route") return `${l.a.name} ${l.b.name}`;
    if (k === "capacity") return l.capacity ?? -1;
    if (k === "length") return routeSegs(l).filter((x): x is number => x != null).reduce((a, b) => a + b, 0) || -1;
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
          <span className="muted small">клик по линии — строка в таблице · промежуточные точки — оранжевым</span>
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
                {/* маршрут выводится без промежуточных точек: только точка А → точка Б */}
                <td>
                  <span title={l.a.address || ""}>{l.a.name}</span>
                  <span className="muted"> → </span>
                  <span title={l.b.address || ""}>{l.b.name}</span>
                </td>
                <td>
                  <div className="mono">{fmtCapacity(l)}</div>
                  {/* каждое назначение своей строкой: «LAN: 8 волокон · 100 Гбит/с (на все)» */}
                  {(l.fiber_usage || []).map((u) => (
                    <div key={u.name} className="muted small mono">{fmtFiberUsageItem(u)}</div>
                  ))}
                </td>
                <td className="mono" title={fmtRouteSegsDetail(l) || undefined}>
                  {fmtRouteLen(l)}
                </td>
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

// «12,5» → 12.5; пустая строка → null
function parseKm(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const v = Number(t.replace(",", "."));
  return isFinite(v) ? v : null;
}

function LinkModal({ link, locations, onClose, onSaved }: { link: FiberLink | null; locations: Location[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: link?.name ?? "",
    a_id: link ? String(link.a.id) : (locations[0] ? String(locations[0].id) : ""),
    b_id: link ? String(link.b.id) : (locations[1] ? String(locations[1].id) : ""),
    capacity: link?.capacity != null ? String(link.capacity) : "", // не редактируется в карточке, значение сохраняется
    fibers: link?.fibers != null ? String(link.fibers) : "",
    is_active: link?.is_active ?? true,
    descr: link?.descr ?? "",
  });
  // промежуточные точки: vias — id по порядку (А → v1 → … → vk → Б);
  // segs — длина КАЖДОГО участка от точки к точке, км (длина = vias.length + 1):
  // [А→v1, v1→v2, …, vk→Б]; без промежуточных точек segs[0] = вся трасса (length)
  const [vias, setVias] = useState<string[]>(link?.route?.via.map((v) => String(v.id)) ?? []);
  const [segs, setSegs] = useState<string[]>(
    link?.route && link.route.segs.length
      ? link.route.segs.map((s) => (s != null ? String(s) : ""))
      : [link?.length != null ? String(link.length) : ""],
  );
  // назначение волокон: названия вводит пользователь сам (LAN, SAN, …);
  // mode — выпадающее меню «на все волокна / на пару волокон / ----» (---- = скорость не устанавливается);
  // extra — поле «дополнительно» (свободный текст, выводится на странице после скорости)
  const [usage, setUsage] = useState<{ name: string; count: string; speed: string; mode: "" | "all" | "pair"; extra: string }[]>(
    link?.fiber_usage?.length
      ? link.fiber_usage.map((u) => ({
          name: u.name, count: String(u.count),
          speed: u.speed != null ? String(u.speed) : "",
          mode: (u.speed_mode ?? "") as "" | "all" | "pair",
          extra: u.extra ?? "",
        }))
      : [{ name: "", count: "", speed: "", mode: "", extra: "" }],
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const aLoc = locations.find((l) => String(l.id) === form.a_id);
  const bLoc = locations.find((l) => String(l.id) === form.b_id);
  const transitLocs = locations.filter((l) => l.is_transit);

  const setSeg = (i: number, v: string) => setSegs((s) => s.map((x, j) => (j === i ? v : x)));

  const addVia = () => {
    setVias((v) => [...v, ""]);
    setSegs((s) => [...s, ""]); // новый участок vk→Б
  };

  const setVia = (i: number, id: string) => setVias((v) => v.map((x, j) => (j === i ? id : x)));

  const removeVia = (i: number) => {
    setVias((v) => v.filter((_, j) => j !== i));
    setSegs((s) => {
      // убираем участок «после» удалённой точки; склеенный участок становится неизвестным
      const ns = s.slice(0, i).concat(s.slice(i + 1));
      if (ns.length > 0) ns[i] = "";
      return ns;
    });
  };

  // смена точки А/Б: если новая А/Б уже стоит как промежуточная — убираем её из маршрута
  const setAB = (field: "a_id" | "b_id", id: string) => {
    setForm((f) => ({ ...f, [field]: id }));
    const idx = vias.indexOf(id);
    if (idx !== -1) removeVia(idx);
  };

  // имя i-й точки маршрута: 0 = А, 1..k = промежуточные, k+1 = Б
  const pointName = (i: number): string => {
    if (i === 0) return aLoc?.name || "А";
    if (i >= vias.length + 1) return bLoc?.name || "Б";
    const v = locations.find((l) => String(l.id) === vias[i - 1]);
    return v?.name || "…";
  };
  const segLabel = (i: number) => `${pointName(i)} → ${pointName(i + 1)}`;

  const totalKnown = segs.map(parseKm).filter((x): x is number => x != null);
  const totalKm = totalKnown.reduce((a, b) => a + b, 0);
  const allSegsSet = segs.length > 0 && segs.every((s) => parseKm(s) != null);

  const setUsageRow = (i: number, patch: { name?: string; count?: string; speed?: string; mode?: "" | "all" | "pair"; extra?: string }) =>
    setUsage((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const removeUsageRow = (i: number) => setUsage((rows) => rows.filter((_, j) => j !== i));

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      // промежуточные точки: все выбраны, без дублей, не совпадают с А/Б
      for (let i = 0; i < vias.length; i++) {
        if (vias[i] === "") throw new Error(`Промежуточная точка ${i + 1}: выберите местоположение`);
        if (vias[i] === form.a_id || vias[i] === form.b_id) throw new Error(`Промежуточная точка ${i + 1}: точка А/Б не может быть промежуточной`);
      }
      if (new Set(vias).size !== vias.length) throw new Error("Промежуточные точки: есть дубликаты");
      // длины участков: пусто или число ≥ 0
      for (let i = 0; i < segs.length; i++) {
        const v = segs[i].trim();
        if (v !== "") {
          const n = Number(v.replace(",", "."));
          if (isNaN(n) || n < 0) throw new Error(`Длина участка «${segLabel(i)}»: число ≥ 0 (км)`);
        }
      }
      const body = {
        name: form.name.trim(),
        a_id: Number(form.a_id),
        b_id: Number(form.b_id),
        capacity: form.capacity.trim() === "" ? null : Number(form.capacity.replace(",", ".")),
        fibers: form.fibers.trim() === "" ? null : Math.round(Number(form.fibers)),
        length: vias.length === 0 ? parseKm(segs[0]) : null, // без промежуточных точек — одно значение
        route: vias.length === 0
          ? null
          : { via: vias.map((v) => Number(v)), segs: segs.map(parseKm) },
        fiber_usage: null as { name: string; count: number; speed: number | null; speed_mode: null | "all" | "pair"; extra: string | null }[] | null,
        is_active: form.is_active,
        descr: form.descr.trim() || null,
      };
      if (body.fibers != null && (isNaN(body.fibers) || body.fibers < 1)) throw new Error("Волокна: целое число ≥ 1");
      const usageClean = usage
        .map((r) => ({ name: r.name.trim(), count: r.count.trim(), speed: r.speed.trim(), mode: r.mode, extra: r.extra.trim() }))
        .filter((r) => r.name || r.count || r.speed || r.extra);
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
            // ---- (прочерки) — скорость не устанавливается вообще
            speed: r.mode === "" ? null : (r.speed === "" ? null : Number(r.speed.replace(",", "."))),
            speed_mode: r.mode === "" ? null : r.mode,
            extra: r.extra || null,
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
  const locLabel = (l: Location) => `${l.name}${l.is_transit ? " · промежуточная" : ""}${l.address ? ` — ${l.address}` : ""}`;

  return (
    <Modal title={link ? `Линия: ${link.name}` : "Новая линия связи"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Название</span>
        <input className="input" value={form.name} placeholder="напр. Минск — Гродно 10Г" onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="kv"><span>Точка А</span>
        <select className="input" value={form.a_id} onChange={(e) => setAB("a_id", e.target.value)}>
          {locations.map((l) => <option key={l.id} value={l.id}>{locLabel(l)}</option>)}
        </select>
        {noCoords(locOpt(form.a_id)) && <span className="warn small">у точки нет координат — на карте не будет видна</span>}
      </div>
      <div className="kv"><span>Точка Б</span>
        <select className="input" value={form.b_id} onChange={(e) => setAB("b_id", e.target.value)}>
          {locations.map((l) => <option key={l.id} value={l.id}>{locLabel(l)}</option>)}
        </select>
        {noCoords(locOpt(form.b_id)) && <span className="warn small">у точки нет координат — на карте не будет видна</span>}
      </div>

      {/* промежуточные точки: трасса идёт через них; длина вносится по каждому участку */}
      <div className="kv"><span>Промежуточные точки</span>
        <div className="small muted" style={{ marginBottom: 4 }}>
          трасса: {aLoc?.name || "А"}{vias.length ? ` → … → ` : " → "}{bLoc?.name || "Б"} · длина вносится от точки к точке;
          доступны только местоположения, отмеченные как «промежуточная точка»;
          на странице «Линии связи» маршрут выводится без промежуточных точек (А → Б)
        </div>
        {vias.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {vias.map((vid, i) => (
              <div key={i}>
                <div className="inline" style={{ gap: 6, alignItems: "center" }}>
                  <span className="muted small" style={{ minWidth: 56 }}>точка {i + 1}:</span>
                  <select className="input" style={{ maxWidth: 240 }} value={vid} onChange={(e) => setVia(i, e.target.value)}>
                    <option value="">— промежуточная точка —</option>
                    {transitLocs
                      .filter((l) => String(l.id) !== form.a_id && String(l.id) !== form.b_id && (!vias.includes(String(l.id)) || String(l.id) === vid))
                      .map((l) => <option key={l.id} value={l.id}>{locLabel(l)}</option>)}
                  </select>
                  {noCoords(locations.find((l) => String(l.id) === vid)) && vid !== "" && (
                    <span className="warn small">нет координат — линия на карте не будет видна</span>
                  )}
                  <button type="button" className="btn ghost small" title="Убрать точку из маршрута" onClick={() => removeVia(i)}>✕</button>
                </div>
                <div className="inline" style={{ gap: 6, alignItems: "center", marginTop: 4, paddingLeft: 20 }}>
                  <span className="muted small" title={`длина участка ${segLabel(i)}`}>участок {segLabel(i)}:</span>
                  <input className="input mono" style={{ maxWidth: 90 }} value={segs[i]} placeholder="км" onChange={(e) => setSeg(i, e.target.value)} />
                  <span className="muted small">км</span>
                </div>
              </div>
            ))}
            <div className="inline" style={{ gap: 6, alignItems: "center", paddingLeft: 20 }}>
              <span className="muted small" title={`длина последнего участка ${segLabel(vias.length)}`}>участок {segLabel(vias.length)}:</span>
              <input className="input mono" style={{ maxWidth: 90 }} value={segs[vias.length]} placeholder="км" onChange={(e) => setSeg(vias.length, e.target.value)} />
              <span className="muted small">км</span>
            </div>
            <div className="small" style={{ paddingLeft: 20 }}>
              Итого трасса: <b className="mono">{totalKm} км</b>
              {!allSegsSet && <span className="muted small"> (введено не все участки)</span>}
            </div>
          </div>
        ) : (
          <div>
            <div className="kv" style={{ marginBottom: 4 }}><span>Длина трассы</span>
              <div className="inline" style={{ gap: 6 }}>
                <input className="input mono" style={{ maxWidth: 120 }} value={segs[0]} placeholder="км, напр. 310" onChange={(e) => setSeg(0, e.target.value)} />
                <span className="muted small">км</span>
              </div>
            </div>
          </div>
        )}
        {transitLocs.length === 0 ? (
          <span className="warn small">нет местоположений, отмеченных как «промежуточная точка» — поставьте галочку в карточке «Местоположения»</span>
        ) : (
          <button type="button" className="btn ghost small" style={{ marginTop: 6 }} onClick={addVia}>
            + Промежуточная точка
          </button>
        )}
      </div>

      <div className="kv"><span>Число волокон</span>
        <input className="input mono" style={{ maxWidth: 56 }} value={form.fibers} placeholder="напр. 32" onChange={(e) => setForm({ ...form, fibers: e.target.value })} />
        <span className="muted small">оптических волокон</span>
      </div>
      <div className="kv"><span>Волокна по назначениям</span>
        <div className="small muted" style={{ marginBottom: 4 }}>назначение · сколько волокон · скорость Гбит/с · как трактуется скорость · дополнительно</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {usage.map((r, i) => (
            <div key={i}>
              <div className="inline" style={{ gap: 6, flexWrap: "wrap" }}>
                <input
                  className="input"
                  style={{ maxWidth: 112 }}
                  value={r.name}
                  placeholder="назначение, напр. LAN или SAN"
                  onChange={(e) => setUsageRow(i, { name: e.target.value })}
                />
                <input
                  className="input mono"
                  style={{ maxWidth: 48 }}
                  value={r.count}
                  placeholder="кол."
                  title="сколько волокон"
                  onChange={(e) => setUsageRow(i, { count: e.target.value })}
                />
                <input
                  className="input mono"
                  style={{ maxWidth: 48 }}
                  value={r.mode === "" ? "" : r.speed}
                  placeholder={r.mode === "" ? "—" : "100"}
                  title="скорость, Гбит/с"
                  onChange={(e) => setUsageRow(i, { speed: e.target.value })}
                />
                {/* выпадающее меню: как трактуется скорость назначения */}
                <select
                  className="input"
                  style={{ maxWidth: 142, minWidth: 0 }}
                  value={r.mode}
                  title="«на все волокна» — все волокна назначения суммарно дают указанную скорость; «на пару волокон» — пара волокон даёт указанную скорость; ---- (прочерки) — скорость не устанавливается"
                  onChange={(e) => {
                    const mode = e.target.value as "" | "all" | "pair";
                    setUsageRow(i, { mode });
                  }}
                >
                  <option value="">----</option>
                  <option value="all">на все волокна</option>
                  <option value="pair">на пару волокон</option>
                </select>
                <button type="button" className="btn ghost small" title="Удалить строку" onClick={() => removeUsageRow(i)}>✕</button>
              </div>
              <div className="inline" style={{ gap: 6, alignItems: "center", marginTop: 4, paddingLeft: 20 }}>
                <span className="muted small">дополнительно:</span>
                <input
                  className="input"
                  style={{ maxWidth: 300 }}
                  value={r.extra}
                  placeholder="свободный текст, напр. WDM, защита 1+1, резервируется… (пусто = не выводить)"
                  onChange={(e) => setUsageRow(i, { extra: e.target.value })}
                />
              </div>
            </div>
          ))}
          <button type="button" className="btn ghost small" onClick={() => setUsage((rows) => [...rows, { name: "", count: "", speed: "", mode: "", extra: "" }])}>
            + Назначение
          </button>
        </div>
        <span className="muted small" style={{ marginTop: 4 }}>
          * «на все волокна» — все волокна назначения суммарно дают указанную скорость (напр. «LAN: 8 волокон · 100 Гбит/с (на все волокна)»);
          «на пару волокон» — пара волокон даёт указанную скорость (напр. «SAN: 8 волокон · 16 Гбит/с (на пару волокон)»);
          ---- (прочерки) — скорость не устанавливается и на страницу не выводится (напр. «LAN: 8 волокон»).
          Поле «дополнительно» выводится на странице «Линии связи» после скорости.
          Названия вносятся вручную (LAN, SAN, резерв, …) — не предопределены.
        </span>
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
