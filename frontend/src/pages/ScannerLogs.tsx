import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { fmt } from "../util";
import type { ScanLogEntry } from "../types";

// Лог сканера по всем сетям. Хранится ТОЛЬКО 1 час, в памяти процесса
// (после перезапуска контейнера — чистый). Для анализа работы (не работы)
// fping/nmap/TCP-пробы: метод, exit-code, stderr, живые IP, длительность.

function MethodBadge({ e }: { e: ScanLogEntry }) {
  if (!e.method) return <span className="muted">—</span>;
  const cls = e.method === "fping" ? "tag-ok" : e.method === "nmap" ? "tag-ldap" : "tag-reserved_alive";
  const auto = e.method_requested && e.method_requested !== "auto" ? null : (e.method_requested === "auto" ? " · auto" : "");
  return <span className={"tag " + cls}>{e.method}{auto}</span>;
}

export default function ScannerLogs() {
  const [items, setItems] = useState<ScanLogEntry[]>([]);
  const [retentionS, setRetentionS] = useState(3600);
  const [err, setErr] = useState("");
  const [auto, setAuto] = useState(true);
  const [subnetId, setSubnetId] = useState("all");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [showAllAlive, setShowAllAlive] = useState(false);

  const load = useCallback(() => {
    api<{ retention_s: number; count: number; items: ScanLogEntry[] }>("/system/scan-logs?limit=1000")
      .then((d) => { setItems(d.items); setRetentionS(d.retention_s); setErr(""); })
      .catch((e) => setErr(e.message));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [auto, load]);

  // сети, встречавшиеся в логе (для фильтра)
  const subnets = useMemo(() => {
    const m = new Map<number, { id: number; name: string; cidr: string }>();
    for (const e of items) if (!m.has(e.subnet_id)) m.set(e.subnet_id, { id: e.subnet_id, name: e.name, cidr: e.cidr });
    return [...m.values()].sort((a, b) => a.cidr.localeCompare(b.cidr, "ru"));
  }, [items]);

  const filtered = useMemo(() => items.filter((e) => {
    if (subnetId !== "all" && String(e.subnet_id) !== subnetId) return false;
    if (errorsOnly && !e.error) return false;
    return true;
  }), [items, subnetId, errorsOnly]);

  const errs = items.filter((e) => e.error).length;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Лог сканера</h1>
        <span className="muted small">
          сканы всех сетей · хранится <b>{Math.round(retentionS / 60)} мин</b> в памяти (в БД не пишется; после перезапуска — чистый)
          {items.length > 0 && <> · записей: {items.length}{errs > 0 && <span className="warn"> · с ошибками: {errs}</span>}</>}
        </span>
        <label className="small" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> авто (5 с)
        </label>
        <button className="btn ghost" onClick={load}>⟳ обновить</button>
      </div>
      {err && <div className="error">{err}</div>}

      <div className="vlan-search">
        <select className="input" value={subnetId} onChange={(e) => setSubnetId(e.target.value)}>
          <option value="all">Все сети</option>
          {subnets.map((s) => <option key={s.id} value={s.id}>{s.name} — {s.cidr}</option>)}
        </select>
        <label className="small" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> только с ошибками
        </label>
        {errorsOnly && <span className="muted small">с ошибками: {filtered.length}</span>}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Время</th>
              <th>Сеть</th>
              <th>Запуск</th>
              <th>Метод</th>
              <th>Живых</th>
              <th>Ново</th>
              <th>Освобождено</th>
              <th>Длит., мс</th>
              <th>Exit</th>
              <th>Ошибка / детали</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => {
              const key = e.at + "-" + e.subnet_id + "-" + (e.method || "");
              const open = openId === key;
              return (
                <FragmentRow
                  key={key} e={e} open={open} showAllAlive={showAllAlive}
                  onToggle={() => setOpenId(open ? null : key)}
                  onShowAll={() => setShowAllAlive((v) => !v)}
                />
              );
            })}
            {filtered.length === 0 && (
              <tr><td colSpan={10} className="muted">
                {items.length === 0
                  ? "Записей пока нет (или все старше 1 часа). Запустите скан на странице «Сети» — запись появится здесь."
                  : "Под фильтр ничего не попало"}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="muted small" style={{ marginTop: 8, lineHeight: 1.6 }}>
        Метод «auto» разрешается как nmap → fping → TCP-проба (по наличию бинарников).
        fping: exit 0 — все живы, 1 — часть не ответила (норма), 2+ — ошибка (см. детали: stderr fping).
        nmap без NET_RAW (docker) делает host discovery TCP-пробами и пишет «unreliable without root» —
        живыми считаются хосты с открытыми портами. Клик по строке — детали (stderr, список живых IP, параметры).
      </div>
    </div>
  );
}

function FragmentRow({ e, open, showAllAlive, onToggle, onShowAll }: {
  e: ScanLogEntry; open: boolean; showAllAlive: boolean;
  onToggle: () => void; onShowAll: () => void;
}) {
  const alive = e.alive_ips || [];
  const shownAlive = showAllAlive ? alive : alive.slice(0, 100);
  return (
    <>
      <tr className={"clickable" + (e.error ? " row-error" : "")} onClick={onToggle} title="клик — детали">
        <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmt(e.at)}</td>
        <td><div>{e.name}</div><div className="muted small mono">{e.cidr}</div></td>
        <td>{e.trigger === "schedule" ? <span className="tag">авто</span> : <span className="tag tag-ok">ручной</span>}</td>
        <td><MethodBadge e={e} /></td>
        <td className="mono">{e.alive}<span className="muted">/{e.hosts_total}</span>
          {e.hosts_total > 0 && e.alive === e.hosts_total && <span className="warn" title="живы ВСЕ адреса — проверьте метод (TCP-проба/nmap без root считают живыми хосты с открытыми портами)"> 100%</span>}
        </td>
        <td className="mono">{e.new}</td>
        <td className="mono">{e.freed}</td>
        <td className="mono">{e.duration_ms != null ? e.duration_ms : "—"}</td>
        <td className="mono">{e.exit_code != null ? e.exit_code : "—"}</td>
        <td className="muted" style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={e.error || undefined}>
          {e.error ? <span className="bad">{e.error}</span> : open ? "↑ свернуть" : "детали…"}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10} style={{ background: "var(--panel-2)" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 14, padding: "4px 2px" }}>
              <div>
                <div className="muted small" style={{ marginBottom: 4 }}>Параметры</div>
                <div className="small mono" style={{ lineHeight: 1.7 }}>
                  <div>метод: {e.method_requested} → {e.method || "?"}</div>
                  <div>timeout: {e.params?.timeout_ms} мс · rate: {e.params?.rate}/с</div>
                  {e.params?.ports && <div>порты TCP-пробы: {e.params.ports.join(", ")}</div>}
                  <div>состояния после: свободно {e.counts?.free} · занято {e.counts?.used} · резерв {e.counts?.reserved} · offline {e.counts?.offline}</div>
                </div>
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="muted small" style={{ marginBottom: 4 }}>stderr ({e.method || "sweep"})</div>
                {e.stderr
                  ? <pre className="log-pre">{e.stderr}</pre>
                  : <div className="muted small">пусто</div>}
              </div>
            </div>
            {alive.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="muted small" style={{ marginBottom: 4 }}>
                  Живые IP ({alive.length}){!showAllAlive && alive.length > 100 && <> — показано 100, <a onClick={(ev) => { ev.stopPropagation(); onShowAll(); }}>показать все</a></>}
                </div>
                <div className="log-pre" style={{ maxHeight: 180 }}>{shownAlive.join(" ")}</div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
