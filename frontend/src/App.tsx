import { useEffect, useState, type ReactNode } from "react";
import { HashRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { api, getToken, setToken } from "./api";
import type { SiteMeta } from "./types";
import PhpIPAMShell from "./components/PhpIPAMShell";
import SearchBox from "./components/SearchBox";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import ScannerLogs from "./pages/ScannerLogs";
import Settings from "./pages/Settings";
import MySettings from "./pages/MySettings";
import Racks from "./pages/Racks";
import Subnets from "./pages/Subnets";
import SubnetDetail from "./pages/SubnetDetail";
import Vlans from "./pages/Vlans";
import Locations from "./pages/Locations";
import Links from "./pages/Links";
import Docs from "./pages/Docs";
import SearchPage from "./pages/SearchPage";
import { setTzOffset } from "./util";

const defaultMeta: SiteMeta = {
  tz_offset_min: 0, ui_logo: "", copyright: "", admin_email: "",
  ui_links: [], search_mode: "page", org_name: "", show_no_dns: true, ui_layout: "ipam",
};

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
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + " ГБ";
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(0) + " МБ";
  return (n / 1024).toFixed(0) + " КБ";
}

function Layout({ meta, onLogout, onMetaChanged }: { meta: SiteMeta; onLogout: () => void; onMetaChanged: () => void }) {
  // /me при каждом входе в Layout — и на старте, и после логина
  const [me, setMe] = useState<any>(null);
  const [sysInfo, setSysInfo] = useState<any>(null);
  useEffect(() => {
    if (!getToken()) return;
    api<any>("/auth/me").then(setMe).catch(() => setMe(null));
  }, []);
  // личный выбор внешнего вида пользователя («Мои настройки»); "" = по глобальной
  // настройке администратора. Перечитываем при каждой смене маршрута, чтобы выбор
  // применялся сразу после сохранения.
  const loc = useLocation();
  const [myLayout, setMyLayout] = useState<"" | "ipam" | "phpipam">("");
  useEffect(() => {
    if (!getToken()) return;
    let alive = true;
    api<{ ui_layout: "" | "ipam" | "phpipam" }>("/me/prefs")
      .then((p) => { if (alive) setMyLayout(p?.ui_layout || ""); })
      .catch(() => {});
    return () => { alive = false; };
  }, [loc.pathname]);
  // служебная информация (хост, docker) — только администраторам
  const role0 = me?.role || "";
  useEffect(() => {
    if (role0 === "admin" && getToken()) api<any>("/system/info").then(setSysInfo).catch(() => {});
  }, [role0]);

  // сворачивание меню: состояние запоминается в браузере
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("ipam_sidebar_collapsed") === "1"; } catch { return false; }
  });
  const setSidebarCollapsed = (v: boolean) => {
    setCollapsed(v);
    try { localStorage.setItem("ipam_sidebar_collapsed", v ? "1" : "0"); } catch { /* приватный режим */ }
  };

  if (!getToken()) return <Navigate to="/login" replace />;
  const role = me?.role || "";

  // внешний вид: личный выбор пользователя («Мои настройки») важнее глобального
  const effectiveLayout = myLayout || meta.ui_layout || "ipam";
  // «Классический» вид (phpipam): горизонтальное меню сверху, VLAN и сети деревом слева.
  // «Новый» (ipam) — меню слева, ниже.
  if (effectiveLayout === "phpipam") {
    return <PhpIPAMShell meta={meta} role={role} me={me} sysInfo={sysInfo} onLogout={onLogout} />;
  }

  return (
    <div className={"layout" + (collapsed ? " collapsed" : "")}>
      <aside className="sidebar">
        <div className="sidebar-head">
          <Link to="/" className="logo-link" title="На главную (в корень)">
            <div className="logo">IPAM</div>
            {meta.ui_logo && <img className="logo-img" src={meta.ui_logo} alt="logo" />}
          </Link>
          <button className="sidebar-toggle" onClick={() => setSidebarCollapsed(true)} title="Свернуть меню">⟨</button>
        </div>
        <SearchBox mode={meta.search_mode === "live" ? "live" : "page"} />
        <nav>
          <NavLink to="/" end>Обзор</NavLink>
          <NavLink to="/subnets">Сети</NavLink>
          <NavLink to="/vlans">VLAN</NavLink>
          <NavLink to="/racks">Стойки</NavLink>
          <NavLink to="/links">Линии связи</NavLink>
          <NavLink to="/locations" className="nav-sub">Местоположения</NavLink>
          <NavLink to="/docs">Документация</NavLink>
          <NavLink to="/profile">Мои настройки</NavLink>
          {role === "admin" && <NavLink to="/settings">Настройки</NavLink>}
          {role === "admin" && <NavLink to="/scanner-logs">Скан-логи</NavLink>}
        </nav>
        {meta.ui_links.length > 0 && (
          <div className="sidebar-links">
            {meta.ui_links.map((l, i) => (
              <a
                key={i}
                className="sidebar-link"
                href={l.url}
                target={l.new_window ? "_blank" : "_self"}
                rel={l.new_window ? "noreferrer" : undefined}
              >
                {l.title}
              </a>
            ))}
          </div>
        )}
        {/* нижняя группа: системный блок (admin) + строка «Выйти» — прижаты к низу,
            линия над «Выйти» */}
        <div className="sidebar-bottom">
          {role === "admin" && sysInfo && (
            <div className="sidebar-sys" title="Приложение и база данных: ресурсы (обновляется до 30 с)">
              <div className="sys-group-head" title={`${sysInfo.platform || ""} · ${sysInfo.env}`}>
                {sysInfo.env_label || "Приложение"}
              </div>
              <span className="sys-line">
                <span className="sys-k">up</span><b>{fmtUptime(sysInfo.uptime_s)}</b>
              </span>
              <span className="sys-line">
                <span className="sys-k">cpu</span><b>{Math.round(sysInfo.cpu_pct ?? 0)}%</b>
                <i className="sys-bar"><i style={{ width: `${Math.round(sysInfo.cpu_pct ?? 0)}%`, background: barColor(sysInfo.cpu_pct ?? 0) }} /></i>
              </span>
              <span className="sys-line" title={`${sysInfo.mem?.used_gb} / ${sysInfo.mem?.total_gb} ГБ`}>
                <span className="sys-k">mem</span><b>{Math.round(sysInfo.mem?.pct ?? 0)}%</b>
                <i className="sys-bar"><i style={{ width: `${Math.round(sysInfo.mem?.pct ?? 0)}%`, background: barColor(sysInfo.mem?.pct ?? 0) }} /></i>
              </span>
              <span className="sys-line" title={`${sysInfo.disk?.used_gb} / ${sysInfo.disk?.total_gb} ГБ`}>
                <span className="sys-k">disk</span><b>{Math.round(sysInfo.disk?.pct ?? 0)}%</b>
                <i className="sys-bar"><i style={{ width: `${Math.round(sysInfo.disk?.pct ?? 0)}%`, background: barColor(sysInfo.disk?.pct ?? 0) }} /></i>
              </span>
              {sysInfo.db && (
                <>
                  <div className="sys-group-head">База данных</div>
                  <span className="sys-line">
                    <span className="sys-k">up</span><b>{sysInfo.db.up_s != null ? fmtUptime(sysInfo.db.up_s) : "—"}</b>
                  </span>
                  <span className="sys-line" title="Размер базы данных">
                    <span className="sys-k">size</span><b>{fmtBytes(sysInfo.db.bytes)}</b>
                  </span>
                  {sysInfo.db.conns != null && (
                    <span className="sys-line" title="Активные соединения">
                      <span className="sys-k">conn</span><b>{sysInfo.db.conns}</b>
                    </span>
                  )}
                  <span className="sys-line" title={`Доля БД на диске: ${sysInfo.db.pct}%. CPU/RAM процесса БД из контейнера недоступны — docker stats с хоста.`}>
                    <span className="sys-k">disk</span><b>{Math.round(sysInfo.db.pct)}%</b>
                    <i className="sys-bar"><i style={{ width: `${Math.min(100, Math.round(sysInfo.db.pct))}%`, background: barColor(sysInfo.db.pct) }} /></i>
                  </span>
                </>
              )}
            </div>
          )}
          <div className="sidebar-foot">
            <span className="muted small">{me?.display_name || me?.username || ""}</span>
            <button className="btn ghost small" onClick={onLogout}>Выйти</button>
          </div>
        </div>
      </aside>
      {collapsed && (
        <button className="sidebar-open-btn" onClick={() => setSidebarCollapsed(false)} title="Развернуть меню">☰</button>
      )}
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
  );
}

// Доступ только для администраторов: сам опрашивает /auth/me,
// пока не узнал роль — «Загрузка…», не-админам редирект на Обзор
function AdminOnly({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api<any>("/auth/me").then((m) => { setRole(m?.role || ""); setLoaded(true); }).catch(() => setLoaded(true));
  }, []);
  if (!loaded) return <div className="page"><div className="muted">Загрузка…</div></div>;
  return role === "admin" ? <>{children}</> : <Navigate to="/" replace />;
}

export default function App() {
  const [meta, setMeta] = useState<SiteMeta>(defaultMeta);
  const loadMeta = () => {
    api<SiteMeta>("/meta").then((m) => {
      setMeta(m);
      setTzOffset(m.tz_offset_min || 0);
    }).catch(() => {});
  };
  useEffect(() => {
    loadMeta();
  }, []);
  // название организации → title страницы (вкладка браузера)
  useEffect(() => {
    document.title = meta.org_name ? `${meta.org_name} — IPAM` : "IPAM";
  }, [meta.org_name]);
  const logout = () => {
    setToken(null);
    window.location.hash = "#/login";
  };
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Layout meta={meta} onLogout={logout} onMetaChanged={loadMeta} />}>
          <Route index element={<Overview />} />
          <Route path="subnets" element={<Subnets />} />
          <Route path="subnets/:id" element={<SubnetDetail />} />
          <Route path="scanner-logs" element={<AdminOnly><ScannerLogs /></AdminOnly>} />
          <Route path="vlans" element={<Vlans />} />
          <Route path="racks" element={<Racks />} />
          <Route path="locations" element={<Locations />} />
          <Route path="links" element={<Links />} />
          <Route path="docs" element={<Docs />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="profile" element={<MySettings />} />
          <Route path="settings" element={<AdminOnly><Settings onMetaChanged={loadMeta} /></AdminOnly>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
