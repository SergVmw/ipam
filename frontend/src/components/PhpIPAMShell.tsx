import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { SiteMeta, Subnet, Vlan } from "../types";
import SearchBox from "./SearchBox";

// «Классический» внешний вид (phpipam): горизонтальное меню сверху, а VLAN и
// сети — в колонке слева (как в phpIPAM). Контент страницы — справа.
// Что показывать слева по умолчанию (VLAN или Сети) — личная настройка
// пользователя (/api/me/prefs, side_mode); переключается прямо в колонке.

function barColor(pct: number): string {
  return pct >= 90 ? "#ef4444" : pct >= 70 ? "#f59e0b" : "#38bdf8";
}

function fmtUptime(s: number): string {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}д ${h}ч`;
  if (h) return `${h}ч ${m}м`;
  if (m) return `${m}м`;
  return `${s}с`;
}

function fmtBytes(n: number): string {
  if (!n) return "—";
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + " ГБ";
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(0) + " МБ";
  return (n / 1024).toFixed(0) + " КБ";
}

interface Props {
  meta: SiteMeta;
  role: string;
  me: any;
  sysInfo: any;
  onLogout: () => void;
}

export default function PhpIPAMShell({ meta, role, me, sysInfo, onLogout }: Props) {
  return (
    <div className="layout pip">
      <header className="pip-top">
        <div className="pip-brand">
          <Link to="/" className="logo-link" title="На главную">
            <span className="pip-logo">IPAM</span>
            {meta.ui_logo && <img className="pip-logo-img" src={meta.ui_logo} alt="logo" />}
          </Link>
        </div>
        <nav className="pip-nav">
          <NavLink to="/" end>Обзор</NavLink>
          <NavLink to="/subnets">Сети</NavLink>
          <NavLink to="/vlans">VLAN</NavLink>
          <NavLink to="/racks">Стойки</NavLink>
          <NavLink to="/links">Линии связи</NavLink>
          <NavLink to="/locations">Местоположения</NavLink>
          <NavLink to="/docs">Документация</NavLink>
          <NavLink to="/profile">Мои настройки</NavLink>
          {role === "admin" && <NavLink to="/settings">Настройки</NavLink>}
          {role === "admin" && <NavLink to="/scanner-logs">Скан-логи</NavLink>}
        </nav>
        <div className="pip-actions">
          <div className="pip-search"><SearchBox mode={meta.search_mode === "live" ? "live" : "page"} /></div>
          <div className="pip-user">
            <span className="pip-user-name muted small">{me?.display_name || me?.username || ""}</span>
            <button className="btn small ghost" onClick={onLogout}>Выйти</button>
          </div>
        </div>
      </header>
      <div className="pip-body">
        <VlanSubnetNav meta={meta} sysInfo={sysInfo} />
        <main className="content">
          <Outlet />
          {(meta.copyright || meta.admin_email) && (
            <footer className="page-foot">
              {meta.copyright && <span>{meta.copyright}</span>}
              {meta.copyright && meta.admin_email && <span> · </span>}
              {meta.admin_email && (
                <span>
                  администратор: <a href={`mailto:${meta.admin_email}`}>{meta.admin_email}</a>
                </span>
              )}
            </footer>
          )}
        </main>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Левая колонка: «VLAN» (дерево VLAN → подсети) или «Сети» (плоский список
// всех подсетей). Режим по умолчанию — из личной настройки пользователя,
// переключатель рядом с заголовком колонки сохраняет выбор.
// ---------------------------------------------------------------------------
function VlanSubnetNav({ meta, sysInfo }: { meta: SiteMeta; sysInfo: any }) {
  const [vlans, setVlans] = useState<Vlan[]>([]);
  const [subnets, setSubnets] = useState<Subnet[]>([]);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState<Set<number | string>>(() => new Set());
  const [loaded, setLoaded] = useState(false);
  const [sideMode, setSideMode] = useState<"vlan" | "subnets">("vlan");
  const loc = useLocation();
  const [sp] = useSearchParams();

  const load = useCallback(() => {
    api<Vlan[]>("/vlans")
      .then((v) => {
        setVlans(v);
        return api<Subnet[]>("/subnets");
      })
      .then((s) => { setSubnets(s); setLoaded(true); })
      .catch((e) => { setErr(e.message); setLoaded(true); });
  }, []);
  // перечитываем при смене маршрута (после создания/изменения сетей дерево актуально)
  useEffect(() => { load(); }, [loc.pathname, load]);

  // личная настройка: что показывать слева по умолчанию.
  // Перечитываем и при смене маршрута (чтобы учесть изменение в «Мои настройки»).
  useEffect(() => {
    api<{ side_mode: "vlan" | "subnets" }>("/me/prefs")
      .then((p) => { setSideMode(p.side_mode === "subnets" ? "subnets" : "vlan"); })
      .catch(() => {});
  }, [loc.pathname]);

  const changeSideMode = (m: "vlan" | "subnets") => {
    setSideMode(m);
    api("/me/prefs", { method: "PUT", body: JSON.stringify({ side_mode: m }) }).catch(() => {});
  };

  // активная подсеть (маршрут /subnets/:id) или активный VLAN (?vlan= на /subnets)
  const activeSubnetId = useMemo(() => {
    const m = loc.pathname.match(/^\/subnets\/(\d+)/);
    return m ? Number(m[1]) : null;
  }, [loc.pathname]);
  const activeVlanId = sp.get("vlan") && loc.pathname === "/subnets" ? Number(sp.get("vlan")) : null;

  // авто-разворачивание родителя активной подсети / активного VLAN
  useEffect(() => {
    if (!loaded) return;
    const need = new Set<number | string>();
    if (activeSubnetId != null) {
      const s = subnets.find((x) => x.id === activeSubnetId);
      if (s) need.add(s.vlan_id ?? "__none__");
    }
    if (activeVlanId != null) need.add(activeVlanId);
    if (need.size === 0) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      need.forEach((n) => next.add(n));
      return next;
    });
  }, [loaded, activeSubnetId, activeVlanId, subnets]);

  const byVlan = useMemo(() => {
    const map = new Map<number | string, Subnet[]>();
    for (const s of subnets) {
      const k = s.vlan_id ?? "__none__";
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(s);
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => {
        const pa = a.cidr.split("/"), pb = b.cidr.split("/");
        const na = Number(pa[1]), nb = Number(pb[1]);
        if (na !== nb) return na - nb;
        return pa[0].localeCompare(pb[0], "en");
      });
    }
    return map;
  }, [subnets]);

  const toggle = (k: number | string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  };

  const ordered = useMemo(() => [...vlans].sort((a, b) => a.vid - b.vid), [vlans]);
  const noVlanCount = byVlan.get("__none__")?.length || 0;

  // плоский список всех сетей (для режима «Сети»)
  const flatSubnets = useMemo(() => {
    const list = [...subnets];
    list.sort((a, b) => {
      const an = a.vlan_name || "", bn = b.vlan_name || "";
      if (an !== bn) return an.localeCompare(bn, "ru");
      return a.cidr.localeCompare(b.cidr, "en");
    });
    return list;
  }, [subnets]);

  return (
    <aside className="pip-side">
      <div className="pip-side-head">
        <span className="pip-side-title">IP-адреса</span>
        <span className="pip-seg" title="Что показывать в этой колонке">
          <button className={"pip-seg-btn" + (sideMode === "vlan" ? " on" : "")}
            onClick={() => changeSideMode("vlan")}>VLAN</button>
          <button className={"pip-seg-btn" + (sideMode === "subnets" ? " on" : "")}
            onClick={() => changeSideMode("subnets")}>Сети</button>
        </span>
      </div>
      {err && <div className="muted small" style={{ padding: "4px 10px" }}>{err}</div>}

      <div className="pip-sec">
        <Link to="/subnets" className="pip-sec-link">
          <span>Все сети</span><span className="pip-count mono">{subnets.length}</span>
        </Link>
        {sideMode !== "vlan" && (
          <Link to="/vlans" className="pip-sec-link">
            <span>VLAN</span><span className="pip-count mono">{vlans.length}</span>
          </Link>
        )}
      </div>

      {sideMode === "vlan" && (
        <>
          <div className="pip-tree-title muted small">VLAN и сети</div>
          <div className="pip-tree">
            {ordered.map((v) => {
              const subs = byVlan.get(v.id) || [];
              const open = expanded.has(v.id);
              const isActive = activeVlanId === v.id;
              return (
                <div key={v.id} className="pip-node">
                  <div
                    className={"pip-vlan" + (open ? " open" : "") + (isActive ? " active" : "")}
                    onClick={() => toggle(v.id)}
                    title={`VLAN ${v.vid} — ${subs.length} сетей`}
                  >
                    <i className="pip-arrow">{open ? "▾" : "▸"}</i>
                    <i className="dot" style={{ background: v.color || "#64748b" }} />
                    <span className="pip-vlan-name">{v.name || `VLAN ${v.vid}`}</span>
                    {subs.length > 0 && <span className="pip-count mono">{subs.length}</span>}
                  </div>
                  {open && (
                    <div className="pip-subs">
                      {subs.length === 0 && <div className="pip-none muted small">нет сетей</div>}
                      {subs.map((s) => (
                        <SubnetRow key={s.id} s={s} active={activeSubnetId === s.id} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}

            {noVlanCount > 0 && (
              <div className="pip-node">
                <div className={"pip-vlan" + (expanded.has("__none__") ? " open" : "")} onClick={() => toggle("__none__")}>
                  <i className="pip-arrow">{expanded.has("__none__") ? "▾" : "▸"}</i>
                  <i className="dot" style={{ background: "#46566f" }} />
                  <span className="pip-vlan-name">Без VLAN</span>
                  <span className="pip-count mono">{noVlanCount}</span>
                </div>
                {expanded.has("__none__") && (
                  <div className="pip-subs">
                    {(byVlan.get("__none__") || []).map((s) => (
                      <SubnetRow key={s.id} s={s} active={activeSubnetId === s.id} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {loaded && vlans.length === 0 && noVlanCount === 0 && (
              <div className="pip-none muted small">VLAN и сети пока не добавлены</div>
            )}
          </div>
        </>
      )}

      {sideMode === "subnets" && (
        <>
          <div className="pip-tree-title muted small">Сети ({flatSubnets.length})</div>
          <div className="pip-tree pip-flat">
            {flatSubnets.map((s) => {
              const vcolor = s.vlan_color || "#46566f";
              return (
                <Link
                  key={s.id}
                  to={`/subnets/${s.id}`}
                  className={"pip-sub pip-sub-flat" + (activeSubnetId === s.id ? " active" : "")}
                  title={`${s.name} ${s.cidr} · занято ${s.used + s.reserved} из ${s.total}${s.vlan_name ? ` · ${s.vlan_name}` : ""}`}
                >
                  <i className="dot" style={{ background: vcolor }} />
                  <span className="pip-sub-name">{s.name || s.cidr}</span>
                  <span className="mono muted small pip-sub-cidr">{s.cidr}</span>
                  <i className="pip-bar-outer" title={`занято ${Math.round(s.pct ?? 0)}%`}>
                    <i style={{ width: `${Math.max(3, Math.min(100, s.pct ?? 0))}%` }} />
                  </i>
                </Link>
              );
            })}
            {loaded && flatSubnets.length === 0 && (
              <div className="pip-none muted small">сети пока не добавлены</div>
            )}
          </div>
        </>
      )}

      {/* ссылки из настроек */}
      {meta.ui_links.length > 0 && (
        <div className="pip-tree-title muted small">Ссылки</div>
      )}
      {meta.ui_links.map((l, i) => (
        <a key={i} className="pip-ext-link" href={l.url} target={l.new_window ? "_blank" : "_self"}
          rel={l.new_window ? "noreferrer" : undefined}>{l.title}</a>
      ))}

      {/* системный блок (admin) */}
      {sysInfo && (
        <div className="pip-sys muted small" title="Приложение и БД: ресурсы (обновляется до 30 с)">
          <span className="sys-line"><span className="sys-k">app</span>
            <b>{Math.round(sysInfo.cpu_pct ?? 0)}%</b>
            <i className="sys-bar"><i style={{ width: `${Math.round(sysInfo.cpu_pct ?? 0)}%`, background: barColor(sysInfo.cpu_pct ?? 0) }} /></i>
          </span>
          <span className="sys-line"><span className="sys-k">mem</span>
            <b>{Math.round(sysInfo.mem?.pct ?? 0)}%</b>
            <i className="sys-bar"><i style={{ width: `${Math.round(sysInfo.mem?.pct ?? 0)}%`, background: barColor(sysInfo.mem?.pct ?? 0) }} /></i>
          </span>
          {sysInfo.db && (
            <span className="sys-line" title="Размер базы данных">
              <span className="sys-k">db</span><b>{fmtBytes(sysInfo.db.bytes)}</b>
            </span>
          )}
          <span className="sys-line"><span className="sys-k">up</span><b>{fmtUptime(sysInfo.uptime_s ?? 0)}</b></span>
        </div>
      )}
    </aside>
  );
}

// строка подсети в дереве/списке
function SubnetRow({ s, active }: { s: Subnet; active: boolean }) {
  const pct = Math.max(3, Math.min(100, s.pct ?? 0));
  return (
    <Link
      to={`/subnets/${s.id}`}
      className={"pip-sub" + (active ? " active" : "")}
      title={`${s.name} ${s.cidr} · занято ${s.used + s.reserved} из ${s.total}`}
    >
      <i className="pip-bullet" style={{ width: `${pct}%` }} />
      <span className="pip-sub-name">{s.name || s.cidr}</span>
      <span className="mono muted small pip-sub-cidr">{s.cidr}</span>
    </Link>
  );
}
