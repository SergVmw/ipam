import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import Chart from "../components/Chart";
import IpGrid, { BlockGrid } from "../components/IpGrid";
import { Th, useSort } from "../components/Sort";
import Modal from "../components/Modal";
import type { Block, EventOut, Ip, ScanRun, Subnet, Usage, UsagePoint } from "../types";
import { fmt, fmtDate, displayState, timeAgo, pctColor, COND_FREE_DAYS, isInsideCidr, prefixOf, subnetsWord } from "../util";

const EVENT_RU: Record<string, string> = {
  ip_seen: "новый IP",
  ip_freed: "IP освобождён",
  hostname_changed: "сменён hostname",
  reserved_alive: "резерв жив",
  mac_changed: "сменён MAC",
  conflict: "конфликт",
};

const STATE_RU: Record<string, string> = {
  free: "Свободно",
  used: "Занято",
  reserved: "Резерв",
  offline: "Offline",
  cond_free: "Усл. осв.",
};

function MacCell({ ip }: { ip: Ip }) {
  if (!ip.mac) return <span className="muted">—</span>;
  const tip = `MAC: ${ip.mac}${ip.mac_vendor ? `\nVendor: ${ip.mac_vendor}` : ""}`;
  return (
    <span title={tip}>
      {ip.mac}
      {ip.mac_vendor && <span className="mac-vendor">{ip.mac_vendor}</span>}
    </span>
  );
}

function usageDonutOption(c: any) {
  return {
    tooltip: { trigger: "item" },
    legend: { data: ["Свободно", "Занято", "Резерв", "Усл. осв."], orient: "vertical", right: 4, top: "middle", textStyle: { color: "#8ea0b8", fontSize: 11 }, itemWidth: 10, itemHeight: 10, itemGap: 8 },
    series: [
      {
        type: "pie",
        radius: ["52%", "70%"],
        center: ["36%", "44%"],
        label: { show: false },
        data: [
          { value: c.free, name: "Свободно", itemStyle: { color: "#22304d" } },
          { value: c.used, name: "Занято", itemStyle: { color: "#22c55e" } },
          { value: c.reserved, name: "Резерв", itemStyle: { color: "#f59e0b" } },
          { value: c.cond_free ?? 0, name: "Усл. осв.", itemStyle: { color: "#86efac" } },
        ],
      },
      // Центральное значение рисует сам ECharts (координаты канваса) —
      // всегда точно в центре кольца, даже если канвас и CSS-контейнер
      // расходятся в ширине. Линии rich с одинаковым lineHeight →
      // блок текста оптически по центру.
      {
        type: "pie",
        radius: [0, 0],
        center: ["36%", "44%"],
        silent: true,
        label: {
          show: true,
          position: "center",
          formatter: `{a|${c.pct}%}\n{b|занято}`,
          rich: {
            a: { fontSize: 24, fontWeight: 800, color: pctColor(c.pct), align: "center", lineHeight: 22 },
            b: { fontSize: 11, color: "#8ea0b8", align: "center", lineHeight: 22 },
          },
        },
        data: [{ value: 1, name: "" }],
      },
    ],
  };
}

function lineOption(series: UsagePoint[]) {
  return {
    grid: { left: 42, right: 14, top: 18, bottom: 26 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: series.map((p) => fmtDate(p.at)),
      axisLabel: { color: "#8ea0b8", fontSize: 10 },
      axisLine: { lineStyle: { color: "#1e293b" } },
    },
    yAxis: {
      type: "value",
      max: 100,
      axisLabel: { formatter: "{value}%", color: "#8ea0b8", fontSize: 10 },
      splitLine: { lineStyle: { color: "#16233c" } },
    },
    series: [{
      type: "line",
      data: series.map((p) => p.pct),
      smooth: true,
      symbol: "none",
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2, color: "#38bdf8" },
      itemStyle: { color: "#38bdf8" },
    }],
  };
}

function SRow({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="srow">
      <span>{k}</span>
      <span className="srow-v">{v}</span>
    </div>
  );
}

export default function SubnetDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const sid = Number(id);
  const [searchParams] = useSearchParams();
  const ipParam = searchParams.get("ip");
  const [subnet, setSubnet] = useState<Subnet | null>(null);
  const [allSubnets, setAllSubnets] = useState<Subnet[]>([]);
  const [ips, setIps] = useState<Ip[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [netView, setNetView] = useState<string | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [events, setEvents] = useState<EventOut[]>([]);
  const [runs, setRuns] = useState<ScanRun[]>([]);
  const [q, setQ] = useState("");
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState("");
  const [showEdit, setShowEdit] = useState(false);
  const [modalIp, setModalIp] = useState<Ip | null>(null); // редактирование через клики по сетке

  // таблица: выделенная строка + inline-редактирование
  const [selectedIp, setSelectedIp] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ hostname: "", owner: "", note: "", state: "used" });
  const [saving, setSaving] = useState(false);
  const [tableQ, setTableQ] = useState("");
  const [tableState, setTableState] = useState("all");
  const [tablePage, setTablePage] = useState(1);
  const tableRef = useRef<HTMLDivElement>(null);

  const prefix = subnet ? parseInt(subnet.cidr.split("/")[1] || "24", 10) : 24;
  const isLarge = prefix <= 21;

  const load = useCallback(async () => {
    try {
      const [s, u, ev, rn, all] = await Promise.all([
        api<Subnet>(`/subnets/${sid}`),
        api<Usage>(`/subnets/${sid}/usage?days=30`),
        api<EventOut[]>(`/events?subnet_id=${sid}&limit=30`),
        api<ScanRun[]>(`/subnets/${sid}/scan-runs?limit=5`),
        api<Subnet[]>("/subnets"),
      ]);
      setSubnet(s);
      setUsage(u);
      setEvents(ev);
      setRuns(rn);
      setAllSubnets(all);
      setErr("");
      if (s.total > 1024) {
        if (netView) {
          const r = await api<{ items: Ip[] }>(`/subnets/${sid}/ips?net=${encodeURIComponent(netView)}&size=10000`);
          setIps(r.items);
        } else {
          setIps([]);
          setBlocks(await api<Block[]>(`/subnets/${sid}/blocks`));
        }
      } else {
        setBlocks([]);
        const r = await api<{ items: Ip[] }>(`/subnets/${sid}/ips?size=10000`);
        setIps(r.items);
      }
    } catch (e: any) {
      setErr(e.message);
    }
  }, [sid, netView]);

  useEffect(() => { load(); }, [load]);

  const doScan = async () => {
    setScanning(true);
    setErr("");
    let lastKnownId = 0;
    try {
      const prev = await api<ScanRun[]>(`/subnets/${sid}/scan-runs?limit=1`);
      lastKnownId = prev[0]?.id ?? 0;
    } catch { /* нет сканов — ок */ }
    try {
      await api(`/subnets/${sid}/scan`, { method: "POST" });
    } catch (e: any) {
      setErr(e.message);
      setScanning(false);
      return;
    }
    const timer = setInterval(async () => {
      try {
        const rn = await api<ScanRun[]>(`/subnets/${sid}/scan-runs?limit=1`);
        const last = rn[0];
        if (last && last.id > lastKnownId && last.finished_at) {
          clearInterval(timer);
          setScanning(false);
          if (last.error) setErr(`Скан завершился с ошибкой: ${last.error}`);
          load();
        }
      } catch {
        /* повторим позже */
      }
    }, 2000);
    setTimeout(() => {
      clearInterval(timer);
      setScanning(false);
    }, 180000);
  };

  // подсветка в сетке
  const highlight = useMemo(() => {
    if (!q) return undefined;
    const s = q.toLowerCase();
    return new Set(
      ips
        .filter((i) => i.ip.includes(s) || (i.hostname || "").toLowerCase().includes(s) || (i.owner || "").toLowerCase().includes(s))
        .map((i) => i.ip),
    );
  }, [q, ips]);

  // таблица: фильтр
  const tableRows = useMemo(() => {
    let rows = ips;
    if (tableState !== "all") rows = rows.filter((i) => displayState(i) === tableState);
    if (tableQ) {
      const s = tableQ.toLowerCase();
      rows = rows.filter((i) => i.ip.includes(s) || (i.hostname || "").toLowerCase().includes(s) || (i.owner || "").toLowerCase().includes(s));
    }
    return rows;
  }, [ips, tableQ, tableState]);

  // таблица: сортировка по клику на заголовок
  const { sorted: sortedRows, sort } = useSort(tableRows, (ip, k) => {
    if (k === "ip") return ip.ip.split(".").reduce((acc, o) => acc * 256 + parseInt(o, 10), 0);
    if (k === "state") return ip.state;
    if (k === "hostname") return ip.hostname || "";
    if (k === "mac") return ip.mac || "";
    if (k === "owner") return ip.owner || "";
    if (k === "note") return ip.note || "";
    return ip.ip;
  });

  // события: сортировка по клику на заголовок
  const { sorted: sortedEvents, sort: evSort } = useSort(events, (ev, k) => {
    if (k === "at") return ev.at || "";
    if (k === "ip") return ev.ip || "";
    if (k === "type") return ev.type;
    return ev.at || "";
  });

  // таблица: блоки по 64 строки (254 строки → 4 страницы)
  const TABLE_PAGE_SIZE = 64;
  const pageCount = Math.max(1, Math.ceil(sortedRows.length / TABLE_PAGE_SIZE));
  const page = Math.min(tablePage, pageCount);
  const pageRows = sortedRows.slice((page - 1) * TABLE_PAGE_SIZE, page * TABLE_PAGE_SIZE);

  useEffect(() => { setTablePage(1); }, [tableQ, tableState, sid, netView]);

  // живые IP без hostname (нет в DNS) — что нужно внести; gateway не включаем
  const noDns = useMemo(
    () => ips.filter((i) => i.state === "used" && !i.hostname && !i.hostname_manual && !i.is_gateway),
    [ips],
  );

  // Подсети, лежащие ВНУТРИ этой сети (иерархия master/подсети, как в phpIPAM):
  // «дети» — непосредственные (без промежуточных), счётчик — все потомки.
  const ipIntOf = (cidr: string) => {
    const p = (cidr.split("/")[0] || "0.0.0.0").split(".").map((x) => parseInt(x, 10) || 0);
    return ((p[0] * 256 + p[1]) * 256 + p[2]) * 256 + p[3];
  };
  const descCount = useMemo(() => {
    const m = new Map<number, number>();
    for (const a of allSubnets)
      for (const b of allSubnets)
        if (a.id !== b.id && isInsideCidr(a.cidr, b.cidr)) m.set(b.id, (m.get(b.id) || 0) + 1);
    return m;
  }, [allSubnets]);
  const children = useMemo(() => {
    if (!subnet) return [] as Subnet[];
    const inside = allSubnets.filter((o) => o.id !== subnet.id && isInsideCidr(o.cidr, subnet.cidr));
    const direct = inside.filter((c) => !inside.some((m) => m.id !== c.id && isInsideCidr(c.cidr, m.cidr)));
    return direct.sort((a, b) => ipIntOf(a.cidr) - ipIntOf(b.cidr) || prefixOf(a.cidr) - prefixOf(b.cidr));
  }, [subnet, allSubnets]);
  // сколько адресов этой сети «переехало» в подсети (не показаны в её IP-таблице)
  const hiddenInSubs = useMemo(() => {
    if (!subnet) return 0;
    const p = prefixOf(subnet.cidr);
    const full = p === 32 ? 1 : p === 31 ? 2 : Math.pow(2, 32 - p) - 2;
    return Math.max(0, full - subnet.total);
  }, [subnet]);

  // Настройки → «Оформление сайта»: показывать ли секцию «IP без hostname — внести в DNS»
  const [showNoDns, setShowNoDns] = useState(true);
  useEffect(() => {
    api<any>("/meta").then((m) => setShowNoDns(m.show_no_dns !== false)).catch(() => {});
  }, []);

  const selectRow = (ip: Ip) => {
    setSelectedIp(ip.ip);
    setEditForm({ hostname: ip.hostname || "", owner: ip.owner || "", note: ip.note || "", state: ip.state });
  };

  // переход из глобального поиска: ?ip=10.x.x.x -> подсветить строку
  useEffect(() => {
    if (ipParam && ips.length) {
      const row = ips.find((i) => i.ip === ipParam);
      if (row) {
        selectRow(row);
        setTimeout(() => tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
      }
    }
  }, [ipParam, ips]);

  const saveEdit = async () => {
    if (!selectedIp) return;
    setSaving(true);
    setErr("");
    try {
      await api(`/ips/${selectedIp}`, {
        method: "PUT",
        body: JSON.stringify({
          hostname: editForm.hostname || null,
          owner: editForm.owner || null,
          note: editForm.note || null,
          state: editForm.state,
        }),
      });
      setSelectedIp(null);
      load();
    } catch (e: any) {
      setErr(e.message);
    }
    setSaving(false);
  };

  const copyNoDns = async () => {
    const text = noDns.map((i) => i.ip).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setErr("");
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Скопируйте IP-адреса:", text);
    }
  };
  const [copied, setCopied] = useState(false);

  if (!subnet) return <div className="page">{err ? <div className="error">{err}</div> : "Загрузка…"}</div>;

  const cur = usage?.current;

  return (
    <div className="page">
      {netView && (
        <div className="breadcrumb">
          <a onClick={() => setNetView(null)}>← {subnet.cidr}</a>
          <span> / {netView}</span>
        </div>
      )}
      <div className="page-head">
        <h1>{subnet.name} <span className="mono muted">{subnet.cidr}</span></h1>
        <div className="actions">
          <button className="btn" onClick={doScan} disabled={scanning}>{scanning ? "Сканирование…" : "Сканировать"}</button>
          <label className="small">
            <input
              type="checkbox"
              checked={subnet.scan_enabled}
              onChange={(e) => api(`/subnets/${sid}`, { method: "PUT", body: JSON.stringify({ scan_enabled: e.target.checked }) }).then(load)}
            />{" "}
            авто-скан
          </label>
          {subnet.scan_enabled && (
            <label className="small">
              каждые{" "}
              <input
                key={subnet.scan_interval_s}
                type="number"
                className="input narrow"
                min={60}
                defaultValue={subnet.scan_interval_s || 3600}
                onBlur={(e) =>
                  api(`/subnets/${sid}`, { method: "PUT", body: JSON.stringify({ scan_interval_s: Number(e.target.value) || 3600 }) }).then(load)
                }
              />{" "}
              с
            </label>
          )}
          <button className="btn ghost" onClick={() => setShowEdit(true)}>Изменить</button>
        </div>
      </div>

      {err && <div className="error">{err}</div>}

      {/* подсети внутри этой сети (иерархия master/подсети) — как в phpIPAM */}
      {children.length > 0 && (
        <div className="card">
          <div className="card-title row">
            Подсети
            <span className="muted small">{children.length} {subnetsWord(children.length)} внутри этой сети · клик — открыть</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>CIDR</th>
                <th>Имя</th>
                <th>Описание</th>
                <th>Подсети</th>
                <th>Заполнено</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {children.map((c) => {
                const cc = descCount.get(c.id) ?? 0;
                return (
                  <tr key={c.id} className="clickable" onClick={() => nav(`/subnets/${c.id}`)}>
                    <td className="mono"><Link to={`/subnets/${c.id}`}>{c.cidr}</Link></td>
                    <td><Link to={`/subnets/${c.id}`} className="link">{c.name}</Link></td>
                    <td className="cell-descr muted" title={c.descr || ""}>{c.descr || ""}</td>
                    <td>
                      {cc > 0
                        ? <Link to={`/subnets?inside=${encodeURIComponent(c.cidr)}`} className="small" style={{ color: "var(--accent)" }} onClick={(e) => e.stopPropagation()}>{cc} {subnetsWord(cc)}</Link>
                        : <span className="muted">—</span>}
                    </td>
                    <td style={{ minWidth: 150 }}>
                      <div className="bar"><div className="bar-fill" style={{ width: `${c.pct}%`, background: pctColor(c.pct) }} /></div>
                      <span className="muted small">{c.used + c.reserved}/{c.total} · {c.pct}%</span>
                    </td>
                    <td className="actions-cell" onClick={(e) => e.stopPropagation()}>
                      <Link className="btn ghost small" to={`/subnets/${c.id}`}>открыть</Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* верх: использование (+график 30 дней) + сводка */}
      <div className="cards-row2">
        <div className="card">
          <div className="card-title row">
            Использование
            {cur && <span className="usage-pct">{cur.used + cur.reserved} из {cur.total} адресов</span>}
          </div>
          {cur && <Chart height={210} option={usageDonutOption(cur)} />}
          <div className="usage-hist-title">заполняемость, 30 дней</div>
          {usage && (usage.series.length > 1
            ? <Chart height={180} option={lineOption(usage.series)} />
            : <div className="muted small" style={{ paddingTop: 20 }}>история появится после сканов (точек: {usage.series.length})</div>)}
        </div>
        <div className="card">
          <div className="card-title">Сводка по сети</div>
          <SRow k="CIDR" v={<span className="mono">{subnet.cidr}</span>} />
          <SRow k="VLAN" v={subnet.vlan_name
            ? <Link to={`/subnets?vlan=${subnet.vlan_id}`} title="Все сети этого VLAN"><i className="dot" style={{ background: subnet.vlan_color || "#64748b" }} /> {subnet.vlan_name}</Link>
            : <span className="muted">—</span>} />
          <SRow k="Gateway" v={subnet.gateway ? <span className="mono">{subnet.gateway}</span> : <span className="muted">—</span>} />
          <SRow k="DHCP" v={subnet.dhcp_start && subnet.dhcp_end
            ? <span className="mono">{subnet.dhcp_start} – {subnet.dhcp_end}</span>
            : <span className="muted">—</span>} />
          <div className="srow-divider" />
          {cur && (
            <>
              <SRow k="Всего адресов" v={cur.total} />
              <SRow k="Свободно" v={<span style={{ color: "#9fb3d1" }}>{cur.free}</span>} />
              <SRow k="Занято" v={<span style={{ color: "#22c55e" }}>{cur.used}</span>} />
              <SRow k="Резерв" v={<span style={{ color: "#f59e0b" }}>{cur.reserved}</span>} />
              <SRow k="Усл. осв." v={<span style={{ color: "#86efac" }}>{cur.cond_free ?? 0}</span>} />
              <div className="bar" style={{ margin: "8px 0" }}>
                <div className="bar-fill" style={{ width: `${cur.pct}%`, background: cur.pct >= 90 ? "#ef4444" : cur.pct >= 70 ? "#f59e0b" : "#22c55e" }} />
              </div>
            </>
          )}
          <div className="srow-divider" />
          <SRow k="Авто-скан" v={
            <span className="scan-tip-wrap">
              {subnet.scan_enabled ? `каждые ${Math.round((subnet.scan_interval_s || 3600) / 60)} мин` : "выкл"}
              {runs.length > 0 && <span className="scan-tip-badge" title="наведите — последние сканы">ⓘ</span>}
              <span className="scan-tip">
                <div className="scan-tip-title">Последние сканы</div>
                {runs.slice(0, 6).map((r) => (
                  <div key={r.id} className="scan-tip-row">
                    <span className="mono">{fmt(r.started_at)}</span>
                    <span>{r.alive ?? "…"}</span>
                    <span className="good">+{r.new_ips ?? 0}</span>
                    <span className="warn">−{r.freed_ips ?? 0}</span>
                    {r.error ? <span className="bad" title={r.error}>⚠</span> : null}
                  </div>
                ))}
                {runs.length === 0 && <div className="muted small">сканов ещё не было</div>}
                {subnet.last_error && <div className="bad small" style={{ marginTop: 4 }}>ошибка: {subnet.last_error}</div>}
              </span>
            </span>
          } />
          <SRow k="Сканер" v={subnet.scan_method || <span className="muted">по умолчанию (из Настроек)</span>} />
          {subnet.tags.length > 0 && (
            <SRow k="Теги" v={<span className="chips">{subnet.tags.map((t) => <span key={t} className="chip">{t}</span>)}</span>} />
          )}
          <SRow k="Следующий скан" v={subnet.next_scan_at ? fmt(subnet.next_scan_at) : <span className="muted">—</span>} />
          <SRow k="Последний скан" v={
            subnet.last_scan_at
              ? <>{fmt(subnet.last_scan_at)}{subnet.last_error ? <span className="bad"> ⚠</span> : null}</>
              : <span className="muted">—</span>
          } />
          {subnet.descr && <SRow k="Описание" v={subnet.descr} />}
        </div>
      </div>

      {/* таблица IP с inline-редактированием */}
      <div className="card" ref={tableRef}>
        <div className="card-title row">
          IP-адреса
          <span className="muted small">{tableRows.length} строк</span>
          {hiddenInSubs > 0 && (
            <span className="muted small" title="Эти адреса принадлежат подсетям, перечисленным в блоке «Подсети»">
              + {hiddenInSubs} в подсетях
            </span>
          )}
          <div className="table-controls">
            <input
              className="input"
              placeholder="Поиск: IP / hostname / ответственный…"
              value={tableQ}
              onChange={(e) => setTableQ(e.target.value)}
            />
            <select className="input" value={tableState} onChange={(e) => setTableState(e.target.value)}>
              <option value="all">Все статусы</option>
              <option value="used">Занято</option>
              <option value="reserved">Резерв</option>
              <option value="cond_free">Усл. осв.</option>
              <option value="free">Свободно</option>
            </select>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr>
              <Th label="IP-адрес" k="ip" sort={sort} />
                <Th label="Статус" k="state" sort={sort} />
                <Th label="Имя хоста" k="hostname" sort={sort} />
                <Th label="MAC" k="mac" sort={sort} />
                <Th label="Ответственный" k="owner" sort={sort} />
                <Th label="Описание" k="note" sort={sort} />
                <th></th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((ip) => {
                const sel = selectedIp === ip.ip;
                return (
                  <tr key={ip.ip} className={sel ? "row-selected" : "clickable"} onClick={() => selectRow(ip)}>
                    <td className="mono">{ip.ip}{ip.is_gateway ? <span className="tag tag-gw" title="gateway"> G</span> : null}{ip.in_dhcp ? <span className="tag tag-dhcp" title="DHCP-диапазон"> D</span> : null}</td>
                    <td>
                      {sel ? (
                        <select className="input" value={editForm.state} onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setEditForm({ ...editForm, state: e.target.value })}>
                          <option value="used">Занято</option>
                          <option value="reserved">Резерв</option>
                          <option value="free">Свободно</option>
                        </select>
                      ) : (
                        <>
                          <span className={`tag tag-state-${displayState(ip)}`}>{STATE_RU[displayState(ip)] || displayState(ip)}</span>
                          {ip.state === "offline" && ip.last_seen && (
                            <div className="muted small" style={{ marginTop: 2 }}>посл. ответ был: {timeAgo(ip.last_seen)}</div>
                          )}
                        </>
                      )}
                    </td>
                    <td>
                      {sel ? (
                        <input className="input" value={editForm.hostname} onClick={(e) => e.stopPropagation()}
                          placeholder="hostname" onChange={(e) => setEditForm({ ...editForm, hostname: e.target.value })} />
                      ) : (
                        <>{ip.hostname || <span className="muted">—</span>}
                          {ip.hostname && <span className="muted small"> ({ip.hostname_manual ? "ручной" : "DNS"})</span>}</>
                      )}
                    </td>
                    <td className="mono"><MacCell ip={ip} /></td>
                    <td>
                      {sel ? (
                        <input className="input" value={editForm.owner} onClick={(e) => e.stopPropagation()}
                          placeholder="кто отвечает" onChange={(e) => setEditForm({ ...editForm, owner: e.target.value })} />
                      ) : (
                        ip.owner || <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      {sel ? (
                        <input className="input" value={editForm.note} onClick={(e) => e.stopPropagation()}
                          placeholder="описание" onChange={(e) => setEditForm({ ...editForm, note: e.target.value })} />
                      ) : (
                        ip.note || <span className="muted">—</span>
                      )}
                    </td>
                    <td className="actions-cell" onClick={(e) => e.stopPropagation()}>
                      {sel && (
                        <>
                          <button className="btn small primary" onClick={saveEdit} disabled={saving}>{saving ? "…" : "Сохранить"}</button>
                          <button className="btn small ghost" onClick={() => setSelectedIp(null)}>Отмена</button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
              {tableRows.length === 0 && <tr><td colSpan={7} className="muted">ничего не найдено</td></tr>}
            </tbody>
          </table>
          {pageCount > 1 && (
            <div className="pager">
              <span className="muted small">
                блок {page} из {pageCount} · строки {(page - 1) * TABLE_PAGE_SIZE + 1}–{Math.min(page * TABLE_PAGE_SIZE, tableRows.length)} из {tableRows.length}
              </span>
              <div className="pager-btns">
                <button className="btn ghost small" disabled={page === 1} onClick={() => setTablePage(1)}>«</button>
                <button className="btn ghost small" disabled={page === 1} onClick={() => setTablePage(page - 1)}>‹</button>
                {pageList(page, pageCount).map((p, i) =>
                  p === "…"
                    ? <span key={`e${i}`} className="muted">…</span>
                    : <button key={p} className={"btn small" + (p === page ? " primary" : " ghost")} onClick={() => setTablePage(p)}>{p}</button>
                )}
                <button className="btn ghost small" disabled={page === pageCount} onClick={() => setTablePage(page + 1)}>›</button>
                <button className="btn ghost small" disabled={page === pageCount} onClick={() => setTablePage(pageCount)}>»</button>
              </div>
            </div>
          )}
        <div className="muted small" style={{ marginTop: 6 }}>клик по строке — редактирование (hostname, ответственный, описание, статус); MAC сохраняется сканером, если доступен</div>
      </div>

      {/* цветная сетка */}
      <div className="card">
        <div className="card-title row">
          IP-адреса
          {!isLarge && <span className="muted small">{ips.length} адресов</span>}
          {isLarge && !netView && <span className="muted small">сводка по /24 — кликните блок, чтобы открыть</span>}
          <input
            className="input grid-search"
            placeholder="Поиск по сетке…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        {!isLarge || netView ? (
          <IpGrid ips={ips} onPick={(ip) => setModalIp(ip)} highlight={highlight} />
        ) : (
          <BlockGrid blocks={blocks} onPick={(b) => setNetView(b.cidr)} />
        )}
        <div className="legend">
          <span><i className="sw free" /> свободно</span>
          <span><i className="sw used" /> занято</span>
          <span><i className="sw reserved" /> резерв</span>
          <span><i className="sw cond_free" /> усл. осв.</span>
          <span><i className="sw gw" /> gateway</span>
          <span><i className="sw dhcp" /> DHCP</span>
        </div>
      </div>

      {/* IP без hostname в DNS (Настройки → Оформление: можно скрыть) */}
      {showNoDns && (
      <div className="card">
        <div className="card-title row">
          IP без hostname — внести в DNS
          <span className="muted small">живые адреса без PTR-записи ({noDns.length})</span>
          <button className="btn small" style={{ marginLeft: "auto" }} onClick={copyNoDns} disabled={noDns.length === 0}>
            {copied ? "Скопировано ✓" : "Копировать IP"}
          </button>
        </div>
        {noDns.length === 0 ? (
          <div className="muted small">все живые IP имеют hostname — DNS в порядке</div>
        ) : (
          <div>
            <table className="table">
              <thead>
                <tr><th>IP-адрес</th><th>MAC</th><th>Ответственный</th><th>Описание</th></tr>
              </thead>
              <tbody>
                {noDns.map((ip) => (
                <tr key={ip.ip}>
                  <td className="mono">{ip.ip}</td>
                  <td className="mono"><MacCell ip={ip} /></td>
                  <td>{ip.owner || <span className="muted">—</span>}</td>
                  <td>{ip.note || <span className="muted">—</span>}</td>
                </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      )}

      {/* низ: заполняемость + сканы */}
      {/* события */}
      <div className="card">
        <div className="card-title">События (последние 30)</div>
        <table className="table">
          <thead>
            <tr>
              <Th label="Время" k="at" sort={evSort} />
              <Th label="IP" k="ip" sort={evSort} />
              <Th label="Событие" k="type" sort={evSort} />
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {sortedEvents.map((ev) => (
              <tr key={ev.id}>
                <td className="mono muted">{fmt(ev.at)}</td>
                <td className="mono">{ev.ip || "—"}</td>
                <td><span className={`tag tag-${ev.type}`}>{EVENT_RU[ev.type] || ev.type}</span></td>
                <td className="muted small">{ev.detail && typeof ev.detail === "object" && Object.keys(ev.detail).length ? JSON.stringify(ev.detail) : ""}</td>
              </tr>
            ))}
            {sortedEvents.length === 0 && <tr><td colSpan={4} className="muted">пока нет событий</td></tr>}
          </tbody>
        </table>
      </div>

      {showEdit && <EditModal subnet={subnet} onClose={() => setShowEdit(false)} onSaved={() => { setShowEdit(false); load(); }} />}
      {modalIp && <IpModal ip={modalIp} onClose={() => setModalIp(null)} onSaved={() => { setModalIp(null); load(); }} />}
    </div>
  );
}

function IpModal({ ip, onClose, onSaved }: { ip: Ip; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    state: ip.state === "offline" ? "free" : ip.state,
    hostname: ip.hostname || "",
    owner: ip.owner || "",
    note: ip.note || "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      await api(`/ips/${ip.ip}`, {
        method: "PUT",
        body: JSON.stringify({
          state: form.state,
          hostname: form.hostname || null,
          owner: form.owner || null,
          note: form.note || null,
        }),
      });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={`IP ${ip.ip}`} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Статус</span>
        <select className="input" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))}>
          <option value="used">Занято</option>
          <option value="reserved">Резерв</option>
          <option value="free">Свободно</option>
        </select>
        {displayState(ip) === "cond_free" && <span className="tag tag-state-cond_free">условно освобождён</span>}
        {displayState(ip) === "cond_free" && ip.last_seen && <span className="muted small">последний ответ был: {timeAgo(ip.last_seen)}</span>}
        {displayState(ip) === "free" && ip.state === "offline" && ip.last_seen && <span className="muted small">последний ответ был: {timeAgo(ip.last_seen)}</span>}
      </div>
      {ip.is_gateway && <div className="kv"><span>Gateway</span><b>да</b></div>}
      {ip.in_dhcp && <div className="kv"><span>DHCP-диапазон</span><b>да</b></div>}
      <div className="kv"><span>MAC</span><span className="mono"><MacCell ip={ip} /></span></div>
      <div className="kv"><span>Hostname</span>
        <input className="input" value={form.hostname} placeholder="пусто = из DNS"
          onChange={(e) => setForm((f) => ({ ...f, hostname: e.target.value }))} />
      </div>
      <div className="kv"><span>Ответственный</span>
        <input className="input" value={form.owner} onChange={(e) => setForm((f) => ({ ...f, owner: e.target.value }))} />
      </div>
      <div className="kv"><span>Заметка</span>
        <input className="input" value={form.note} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
      </div>
      <div className="kv"><span>Последний ответ</span>
        {ip.last_seen
          ? <span><b>{timeAgo(ip.last_seen)}</b> <span className="muted small">· {fmt(ip.last_seen)}</span></span>
          : <span className="muted small">не было</span>}
      </div>
      <div className="kv"><span>Впервые замечен</span>
        <span className="muted small">{fmt(ip.first_seen)}</span>
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Закрыть</button>
      </div>
    </Modal>
  );
}

function EditModal({ subnet, onClose, onSaved }: { subnet: Subnet; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: subnet.name,
    vlan_id: String(subnet.vlan_id || 0),
    gateway: subnet.gateway || "",
    dhcp_start: subnet.dhcp_start || "",
    dhcp_end: subnet.dhcp_end || "",
    scan_method: subnet.scan_method || "",
    tags: (subnet.tags || []).join(", "),
    descr: subnet.descr || "",
  });
  const [vlans, setVlans] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { api("/vlans").then(setVlans).catch(() => {}); }, []);

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      await api(`/subnets/${subnet.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: form.name,
          vlan_id: Number(form.vlan_id),  // 0 = снять VLAN (null бэкенд трактует как «не менять»)
          gateway: form.gateway || null,
          dhcp_start: form.dhcp_start || null,
          dhcp_end: form.dhcp_end || null,
          scan_method: form.scan_method,
          tags: form.tags,
          descr: form.descr || null,
        }),
      });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={`Изменить: ${subnet.cidr}`} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Имя</span><input className="input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></div>
      <div className="kv"><span>VLAN</span>
        <select className="input" value={form.vlan_id} onChange={(e) => setForm((f) => ({ ...f, vlan_id: e.target.value }))}>
          <option value={0}>— без VLAN —</option>
          {vlans.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.vid})</option>)}
        </select>
      </div>
      <div className="kv"><span>Gateway</span><input className="input mono" value={form.gateway} onChange={(e) => setForm((f) => ({ ...f, gateway: e.target.value }))} /></div>
      <div className="kv"><span>DHCP от / до</span>
        <div className="inline">
          <input className="input mono" value={form.dhcp_start} onChange={(e) => setForm((f) => ({ ...f, dhcp_start: e.target.value }))} />
          <input className="input mono" value={form.dhcp_end} onChange={(e) => setForm((f) => ({ ...f, dhcp_end: e.target.value }))} />
        </div>
      </div>
      <div className="kv"><span>Сканер</span>
        <select className="input" value={form.scan_method} onChange={(e) => setForm((f) => ({ ...f, scan_method: e.target.value }))}>
          <option value="">по умолчанию (из Настроек)</option>
          <option value="fping">fping (ICMP; без MAC)</option>
          <option value="nmap">nmap (-sn; MAC + vendor)</option>
          <option value="tcp">TCP-проба</option>
        </select>
        <span className="muted small">метод обхода именно для этой сети</span>
      </div>
      <div className="kv"><span>Теги</span>
        <input className="input" value={form.tags} placeholder="prod, finance (через запятую)"
          onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} />
      </div>
      <div className="kv"><span>Описание</span><input className="input" value={form.descr} onChange={(e) => setForm((f) => ({ ...f, descr: e.target.value }))} /></div>
      <div className="muted small" style={{ margin: "8px 0" }}>CIDR менять нельзя — создайте новую сеть и удалите старую. По тегам работает глобальный поиск.</div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}

// номера страниц: 1 … 4 5 6 … 20
function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const wanted = [1, 2, current - 1, current, current + 1, total - 1, total].filter((p) => p >= 1 && p <= total);
  const uniq = [...new Set(wanted)].sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const p of uniq) {
    if (p - prev > 1) out.push("…");
    out.push(p);
    prev = p;
  }
  return out;
}
