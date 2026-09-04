import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import { DEMO_TOPOLOGY } from "../data/rackDemo";

// Страница «Стойки»: рисует шкафы (стойки) по данным внешнего Rack Topology API.
// Данные приходят через серверный прокси ядра (/api/racks/status), поэтому CORS
// не нужен, а внутренний адрес источника клиентам не раскрывается.

interface TDevice {
  id: string;
  kind: "rack" | "blade" | "rear";
  site: string;
  rack: string;
  side?: string;
  positionType?: string;
  position?: string;
  uStart?: number | null;
  uEnd?: number | null;
  uHeight?: number | null;
  bay?: number | null;
  rearPosition?: number | null;
  host: string;
  serviceIp?: string;
  managementIps?: string[];
  model?: string;
  serial?: string;
  status: string;
  latencyMs?: number | null;
  bladeKey?: string;
  bladeAlias?: string;
  slotCount?: number;
  displayLocation?: string;
  chassisUrl?: string;
  ipamUrl?: string;
  role?: string;
  generation?: string;
  url?: string;
  note?: string;
}
interface TRow { label: string; racks: string[] }
interface TLocation { site: string; units: number; rows: TRow[] }
interface TTopology {
  schemaVersion?: number;
  generatedAt?: string;
  inventoryRevision?: string;
  requestedSite?: string;
  counts?: Record<string, number>;
  locations: TLocation[];
  devices: TDevice[];
}
interface StatusResp {
  configured: boolean;
  cached: boolean;
  cachedAt: string | null;
  error: string | null;
  ttl_s: number;
  data: TTopology | null;
}

const ST_COLOR: Record<string, string> = {
  up: "#22c55e", down: "#ef4444", partial: "#f59e0b", unknown: "#64748b",
};
const ST_BG: Record<string, string> = {
  up: "rgba(34,197,94,.14)", down: "rgba(239,68,68,.14)",
  partial: "rgba(245,158,11,.16)", unknown: "rgba(100,116,139,.14)",
};
const ST_RU: Record<string, string> = {
  up: "работает", down: "недоступен", partial: "частично", unknown: "неизвестно",
};

function shortHost(h: string): string {
  const s = (h || "").split(".")[0];
  return s.length > 22 ? s.slice(0, 20) + "…" : s;
}

function StatusDot({ st }: { st?: string }) {
  const c = ST_COLOR[st || "unknown"] || "#64748b";
  return <i className="rack-dot" style={{ background: c }} title={ST_RU[st || "unknown"] || st} />;
}

// ---- один шкаф: вертикальная колонка U; устройства вкладываются по uStart/uEnd ----
function RackFrame({ frame, units, devices, selectedId, onSelect, topDown }: {
  frame: string;
  units: number;
  devices: TDevice[];
  selectedId: string | null;
  onSelect: (d: TDevice) => void;
  topDown: boolean;
}) {
  const main = useMemo(
    () => devices.filter((d) => d.kind === "rack").sort((a, b) => (b.uStart ?? 0) - (a.uStart ?? 0)),
    [devices],
  );
  const blades = devices.filter((d) => d.kind === "blade");
  const rears = devices.filter((d) => d.kind === "rear");
  // список вложенных по bladeKey
  const innerByKey = useMemo(() => {
    const map: Record<string, { blades: TDevice[]; rears: TDevice[] }> = {};
    for (const b of blades) (map[b.bladeKey || "_"] = map[b.bladeKey || "_"] || { blades: [], rears: [] }).blades.push(b);
    for (const r of rears) (map[r.bladeKey || "_"] = map[r.bladeKey || "_"] || { blades: [], rears: [] }).rears.push(r);
    for (const k of Object.keys(map)) {
      map[k].blades.sort((a, b) => (a.bay ?? 0) - (b.bay ?? 0));
      map[k].rears.sort((a, b) => (a.rearPosition ?? 0) - (b.rearPosition ?? 0));
    }
    return map;
  }, [blades, rears]);

  // сетка: units строк, сверху row=1 = U=units (если topDown), либо U=1 сверху
  const rowOf = (u: number) => (topDown ? units - u + 1 : u);
  const unitsTotal = Math.max(units, 42);

  return (
    <div className={"rack-frame" + (selectedId && devices.some((d) => d.id === selectedId) ? " has-sel" : "")}>
      <div className="rack-head">
        <b>{frame}</b>
        <span className="muted small">{units}U</span>
      </div>
      <div className="rack-body" style={{ gridTemplateRows: `repeat(${unitsTotal}, 12px)` }}>
        {/* фоновая разметка U */}
        {Array.from({ length: unitsTotal }, (_, i) => {
          const u = topDown ? unitsTotal - i : i + 1;
          return (
            <div key={u} className="rack-guide">
              {((topDown && (u % 2 === 1 || u === unitsTotal)) || (!topDown && (u % 2 === 1 || u === 1))) && (
                <span className="rack-u mono">{u}</span>
              )}
            </div>
          );
        })}
        {main.map((d) => {
          const s = Math.min(d.uStart ?? 0, d.uEnd ?? 0) || 0;
          const e = Math.max(d.uStart ?? 0, d.uEnd ?? 0) || 0;
          const start = rowOf(e);
          const end = rowOf(s) + 1;
          if (!start || !end || end <= start) return null;
          const st = d.status || "unknown";
          const isChassis = !!d.bladeKey;
          const inner = isChassis ? innerByKey[d.bladeKey || ""] : null;
          const sel = selectedId === d.id;
          return (
            <div
              key={d.id}
              className={"rack-dev" + (sel ? " sel" : "")}
              style={{ gridRow: `${start} / ${end}`, borderColor: ST_COLOR[st], background: ST_BG[st] }}
              onClick={() => onSelect(d)}
              title={`${d.host} [${d.rack}] U${d.uStart ?? "?"}–${d.uEnd ?? "?"} · ${ST_RU[st] || st}${d.model ? " · " + d.model : ""}`}
            >
              <div className="rack-dev-top">
                <StatusDot st={st} />
                <span className="rack-dev-name">{shortHost(d.host) || d.id}</span>
                {isChassis && d.slotCount ? <span className="rack-badge" title="blade-корзина">{d.slotCount}</span> : null}
              </div>
              {isChassis && (
                <div className="rack-chassis">
                  {inner && inner.blades.map((b) => (
                    <div
                      key={b.id}
                      className={"rack-blade" + (selectedId === b.id ? " sel" : "")}
                      style={{ background: ST_BG[b.status || "unknown"] }}
                      title={`Bay ${b.bay}: ${b.host} · ${ST_RU[b.status] || b.status}${b.latencyMs != null ? ` · ${b.latencyMs} мс` : ""}`}
                      onClick={(e) => { e.stopPropagation(); onSelect(b); }}
                    />
                  ))}
                  {inner && inner.rears.map((r) => (
                    <div
                      key={r.id}
                      className="rack-rear"
                      style={{ background: "rgba(139,92,246,.16)" }}
                      title={`Rear ${r.rearPosition}: ${r.host} (rear-модуль)`}
                      onClick={(e) => { e.stopPropagation(); onSelect(r); }}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DeviceDetails({ d, onClose }: { d: TDevice; onClose: () => void }) {
  const rows: [string, ReactNode][] = [
    ["Хост", d.host],
    ["Тип", d.kind === "rack" ? (d.bladeKey ? "Blade-корзина" : "Устройство") : d.kind === "blade" ? "Blade-модуль" : "Rear-модуль"],
    ["Площадка / шкаф", `${d.site} / ${d.rack}${d.side ? " (" + d.side + ")" : ""}`],
    ["Расположение", d.bladeKey
      ? (d.kind === "blade" ? `корзина ${d.bladeAlias || d.bladeKey}, Bay ${d.bay}` : `корзина ${d.bladeAlias || d.bladeKey}, Rear ${d.rearPosition}`)
      : (d.position || (d.uStart ? `U ${d.uStart}–${d.uEnd}` : ""))],
    ["Статус", <span key="s" className="bad" style={{ color: ST_COLOR[d.status] }}>{ST_RU[d.status] || d.status}</span>],
    ["Задержка", d.latencyMs != null ? `${d.latencyMs} мс` : "—"],
    ["Модель", d.model || "—"],
    ["Серийный", d.serial || "—"],
    ["Роль", d.role || "—"],
    ["Service IP", d.serviceIp || "—"],
    ["Management", (d.managementIps || []).join(", ") || "—"],
  ];
  return (
    <div className="rack-detail card">
      <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>{d.host || d.id}</span>
        <button className="btn ghost small" onClick={onClose}>✕</button>
      </div>
      <table className="table">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}><td className="muted small" style={{ width: 130 }}>{k}</td>
              <td className="small">{v}</td></tr>
          ))}
        </tbody>
      </table>
      {(d.url || d.chassisUrl || d.ipamUrl) && (
        <div className="btn-row" style={{ marginTop: 8 }}>
          {d.url && <a className="btn" href={d.url} target="_blank" rel="noreferrer">открыть устройство ↗</a>}
          {d.chassisUrl && <a className="btn" href={d.chassisUrl} target="_blank" rel="noreferrer">консоль корзины ↗</a>}
          {d.ipamUrl && <a className="btn" href={d.ipamUrl} target="_blank" rel="noreferrer">карточка в IPAM ↗</a>}
        </div>
      )}
      {d.note && <div className="muted small" style={{ marginTop: 6 }}>{d.note}</div>}
    </div>
  );
}

export default function Racks() {
  const [resp, setResp] = useState<StatusResp | null>(null);
  const [demo, setDemo] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [site, setSite] = useState<string>("");
  const [row, setRow] = useState<string>("all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<TDevice | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api<StatusResp>("/racks/status")
      .then((r) => { setResp(r); setErr(r?.error || ""); })
      .catch((e) => { setResp(null); setErr(e.message); })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  // автообновление
  useEffect(() => {
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const topo: TTopology | null = demo ? DEMO_TOPOLOGY : (resp?.data || null);
  const configured = demo ? true : (resp?.configured ?? false);

  const sites = useMemo(() => (topo?.locations || []).map((l) => l.site), [topo]);
  useEffect(() => {
    if (!site && sites.length) setSite(sites[0]);
  }, [sites, site]);

  const location = useMemo(() => (topo?.locations || []).find((l) => l.site === site), [topo, site]);
  const rowsSel = useMemo(() => {
    if (!location) return [];
    return location.rows.filter((r) => row === "all" || r.label === row);
  }, [location, row]);

  const devicesFor = useCallback((siteName: string, frame: string) => {
    if (!topo) return [];
    const qq = q.trim().toLowerCase();
    return topo.devices.filter((d) => {
      if (d.site !== siteName || d.rack !== frame) return false;
      if (qq && !d.host.toLowerCase().includes(qq) && !(d.model || "").toLowerCase().includes(qq) && !d.id.toLowerCase().includes(qq)) return false;
      return true;
    });
  }, [topo, q]);

  const counts = topo?.counts || {};
  const countRows: [string, string][] = [
    ["всего", "total"], ["работают", "up"], ["недоступны", "down"],
    ["частично", "partial"], ["нет данных", "unknown"], ["blade", "blade"], ["rear", "rear"],
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Стойки</h1>
        <div className="muted small">
          {topo?.generatedAt
            ? <>данные: <b>{new Date(topo.generatedAt).toLocaleString("ru-RU")}</b>
                {topo.inventoryRevision && <> · revision <span className="mono">{topo.inventoryRevision.slice(0, 8)}</span></>}</>
            : "данные не загружены"}
          {resp?.cached && <span> · кэш (обновляется каждые {resp.ttl_s} с)</span>}
        </div>
      </div>

      {err && !topo && <div className="error">{err}</div>}

      {!loading && !configured && !topo && (
        <div className="card">
          <div className="card-title">Источник не настроен</div>
          <div className="muted small" style={{ lineHeight: 1.7 }}>
            Страница «Стойки» берёт данные из внешнего Rack Topology API.
            Попросите администратора указать адрес источника в
            <b> Настройки → «Стойки (Rack Topology API)»</b>.
          </div>
          <div className="btn-row" style={{ marginTop: 10 }}>
            <button className="btn" onClick={() => { setDemo(true); setSel(null); }}>Показать пример (демо-данные)</button>
          </div>
        </div>
      )}

      {demo && (
        <div className="muted small" style={{ marginBottom: 8 }}>
          Показаны демо-данные (внешний источник не подключён/недоступен).
        </div>
      )}

      {topo && (
        <>
          {/* счётчики */}
          <div className="cards-row racks-cards">
            {countRows.map(([label, key]) => {
              const n = counts[key] ?? 0;
              const c = ST_COLOR[key] || (key === "total" ? undefined : undefined);
              return (
                <div key={key} className="card stat rack-stat">
                  <div className="rack-stat-n" style={c ? { color: c } : undefined}>{n}</div>
                  <div className="rack-stat-l muted small">{label}</div>
                </div>
              );
            })}
          </div>

          {/* управление */}
          <div className="racks-tools">
            {sites.length > 1 && (
              <select className="input" value={site} onChange={(e) => { setSite(e.target.value); setRow("all"); setSel(null); }}>
                {sites.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            )}
            {location && location.rows.length > 1 && (
              <select className="input" value={row} onChange={(e) => { setRow(e.target.value); setSel(null); }}>
                <option value="all">Все ряды</option>
                {location.rows.map((r) => <option key={r.label} value={r.label}>{r.label}</option>)}
              </select>
            )}
            <input className="input racks-search" placeholder="Поиск по хосту/модели…" value={q}
              onChange={(e) => setQ(e.target.value)} />
            <button className="btn ghost" onClick={load} disabled={loading}>⟳ обновить</button>
            {!demo
              ? <button className="btn ghost" onClick={() => setDemo(true)} title="Показать встроенные демо-данные (без обращения к источнику)">пример</button>
              : <button className="btn ghost" onClick={() => { setDemo(false); load(); }} title="Вернуться к данным источника">источник</button>}
          </div>

          {/* шкафы */}
          {location && rowsSel.length === 0 && <div className="muted">Под фильтр ничего не попало</div>}
          {location && rowsSel.map((r) => (
            <section key={r.label} className="racks-row-sec">
              <div className="racks-row-label">{r.label}</div>
              <div className="racks-row">
                {r.racks.map((frame) => (
                  <RackFrame key={frame} frame={frame} units={location.units}
                    devices={devicesFor(location.site, frame)} selectedId={sel?.id || null}
                    onSelect={(d) => setSel(d)} topDown />
                ))}
              </div>
            </section>
          ))}

          {sel && <DeviceDetails d={sel} onClose={() => setSel(null)} />}
        </>
      )}
    </div>
  );
}
