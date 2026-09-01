import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import type { AgentOut, AgentReportOut, AppSettings, InstallState, Subnet, UserOut } from "../types";
import { fmt, setTzOffset } from "../util";

const ROLE_RU: Record<string, string> = {
  admin: "Администратор",
  operator: "Оператор",
  viewer: "Только чтение",
};

export default function Settings({ onMetaChanged }: { onMetaChanged?: () => void }) {
  const [form, setForm] = useState<AppSettings | null>(null);
  const [users, setUsers] = useState<UserOut[]>([]);
  const [agents, setAgents] = useState<AgentOut[]>([]);
  const [subnets, setSubnets] = useState<Subnet[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [editUser, setEditUser] = useState<UserOut | null>(null);
  const [showAddUser, setShowAddUser] = useState(false);
  const [testing, setTesting] = useState(false);
  const [agentModal, setAgentModal] = useState<AgentOut | "new" | null>(null);
  const [installModal, setInstallModal] = useState<{ agent: AgentOut; mode: "start" | "log" } | null>(null);
  const [reportsAgent, setReportsAgent] = useState<AgentOut | null>(null);
  const [forceState, setForceState] = useState<Record<number, { state: string; detail: string }>>({});
  const [newLink, setNewLink] = useState({ title: "", url: "", new_window: true });

  // сортировка таблиц: пользователи / агенты
  const { sorted: sortedUsers, sort: userSort } = useSort(users, (u, k) => {
    if (k === "username") return u.username;
    if (k === "provider") return u.provider;
    if (k === "role") return u.role;
    if (k === "created") return u.created_at || "";
    return u.username;
  });
  const { sorted: sortedAgents, sort: agentSort } = useSort(agents, (a, k) => {
    if (k === "name") return a.name;
    if (k === "subnets") return a.subnet_ids ? a.subnet_ids.length : 0;
    if (k === "ssh") return a.ssh_host || "";
    if (k === "last_report") return a.last_report_at || "";
    if (k === "install") return a.last_install_at || "";
    return a.name;
  });

  const addLink = () => {
    if (!form) return;
    const title = newLink.title.trim();
    let url = newLink.url.trim();
    if (!title || !url) return;
    if (!/^https?:\/\//i.test(url)) url = "https://" + url;
    try {
      new URL(url);
    } catch {
      setErr(`Некорректный URL: ${url}`);
      return;
    }
    setForm({ ...form, ui_links: [...(form.ui_links || []), { title, url, new_window: newLink.new_window }] });
    setNewLink({ title: "", url: "", new_window: true });
  };

  const setLink = (i: number, patch: Partial<{ title: string; url: string; new_window: boolean }>) => {
    if (!form) return;
    setForm({ ...form, ui_links: (form.ui_links || []).map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  };

  const doForcePoll = async (a: AgentOut) => {
    setForceState((s) => ({ ...s, [a.id]: { state: "running", detail: "подключение по SSH…" } }));
    try {
      await api(`/agents/${a.id}/force-poll`, { method: "POST" });
      const poll = setInterval(async () => {
        try {
          const st = await api<{ state: string; detail: string }>(`/agents/${a.id}/force-poll-state`);
          setForceState((s) => ({ ...s, [a.id]: st }));
          if (st.state !== "running") clearInterval(poll);
        } catch { clearInterval(poll); }
      }, 2000);
    } catch (e: any) {
      setForceState((s) => ({ ...s, [a.id]: { state: "error", detail: e.message } }));
    }
  };

  const load = async () => {
    try {
      const [st, us, ag, sn] = await Promise.all([
        api<AppSettings>("/settings"), api<UserOut[]>("/users"),
        api<AgentOut[]>("/agents").catch(() => [] as AgentOut[]),
        api<Subnet[]>("/subnets").catch(() => [] as Subnet[]),
      ]);
      setForm({ ...st });
      setUsers(us);
      setAgents(ag);
      setSubnets(sn);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 4000); };

  const save = async () => {
    if (!form) return;
    setErr("");
    try {
      await api("/settings", { method: "PUT", body: JSON.stringify(form) });
      api<any>("/meta").then((m) => setTzOffset(m.tz_offset_min || 0)).catch(() => {});
      onMetaChanged?.();
      flash("Сохранено");
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const onLogoFile = (file: File | null) => {
    if (!file) return;
    if (file.size > 200 * 1024) { setErr("Логотип до 200 КБ"); return; }
    const rd = new FileReader();
    rd.onload = () => setForm((f) => (f ? { ...f, ui_logo: rd.result as string } : f));
    rd.onerror = () => setErr("Не удалось прочитать файл");
    rd.readAsDataURL(file);
  };

  const testMail = async () => {
    setTesting(true);
    setErr("");
    try {
      await api("/settings/test-mail", { method: "POST" });
      flash("Тестовое письмо отправлено");
    } catch (e: any) {
      setErr("Не удалось отправить: " + e.message);
    }
    setTesting(false);
  };

  const setU = async (u: UserOut) => {
    setUsers((list) => list.map((x) => (x.id === u.id ? u : x)));
  };

  if (!form) return <div className="page">{err ? <div className="error">{err}</div> : "Загрузка…"}</div>;

  return (
    <div className="page">
      <div className="page-head"><h1>Настройки</h1></div>
      {err && <div className="error">{err}</div>}
      {msg && <div className="error" style={{ background: "rgba(34,197,94,.1)", borderColor: "rgba(34,197,94,.4)", color: "#86efac" }}>{msg}</div>}

      <div className="card">
        <div className="card-title">DNS</div>
        <div className="kv"><span>DNS-серверы</span>
          <input className="input mono" style={{ maxWidth: 420 }} value={form.dns_servers}
            placeholder="пусто = системный резолвер"
            onChange={(e) => setForm({ ...form, dns_servers: e.target.value })} />
          <span className="muted small">через запятую, напр. 10.0.0.2, 10.0.0.3</span>
        </div>
        <div className="kv"><span>Разрешение адресов</span>
          <label className="small">
            <input type="checkbox" checked={form.resolve_dns} onChange={(e) => setForm({ ...form, resolve_dns: e.target.checked })} />
            разрешать hostname по PTR при сканировании
          </label>
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Скорость обхода (сканер)</div>
        <div className="kv"><span>Сканер</span>
          <select className="input" value={form.scan_method} onChange={(e) => setForm({ ...form, scan_method: e.target.value })}>
            <option value="auto">auto (nmap → fping → tcp)</option>
            <option value="fping">fping (ICMP, самый быстрый; без MAC)</option>
            <option value="nmap">nmap (-sn; MAC + vendor, точнее на сложных сетях)</option>
            <option value="tcp">TCP-проба (без fping/nmap, сети до /22)</option>
          </select>
        </div>
        <div className="kv"><span>Скорость, ping/сек</span>
          <input className="input narrow" type="number" min={1} max={100000} value={form.scan_rate}
            onChange={(e) => setForm({ ...form, scan_rate: Number(e.target.value) || 0 })} />
          <span className="muted small">лимит скорости обхода (fping/nmap); для TCP-пробы не применяется</span>
        </div>
        <div className="kv"><span>Таймаут «живости», мс</span>
          <input className="input narrow" type="number" min={50} max={10000} value={form.scan_timeout_ms}
            onChange={(e) => setForm({ ...form, scan_timeout_ms: Number(e.target.value) || 0 })} />
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Часовой пояс</div>
        <div className="kv"><span>Сдвиг от UTC, минут</span>
          <input className="input narrow" type="number" step={30} min={-720} max={840} value={form.tz_offset_min}
            onChange={(e) => setForm({ ...form, tz_offset_min: Number(e.target.value) || 0 })} />
          <span className="muted small">+180 = UTC+3 (Минск), 0 = UTC, −180 = UTC−3</span>
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Оформление сайта</div>
        <div className="kv"><span>Логотип</span>
          {form.ui_logo && <img className="logo-preview" src={form.ui_logo} alt="logo" />}
          <input type="file" accept="image/*" onChange={(e) => onLogoFile(e.target.files?.[0] ?? null)} />
          {form.ui_logo && (
            <button className="btn small ghost" onClick={() => setForm({ ...form, ui_logo: "" })}>убрать</button>
          )}
          <span className="muted small">изображение до 200 КБ, появится под надписью IPAM</span>
        </div>
        <div className="kv"><span>Название организации</span>
          <input className="input" style={{ maxWidth: 320 }} value={form.org_name || ""}
            placeholder="Компания, Отдел ИТ"
            onChange={(e) => setForm({ ...form, org_name: e.target.value })} />
          <span className="muted small">выводится в title страницы (вкладка браузера)</span>
        </div>
        <div className="kv"><span>Строка копирайта</span>
          <input className="input" style={{ maxWidth: 420 }} value={form.copyright || ""}
            placeholder="© 2026 Компания, Отдел ИТ"
            onChange={(e) => setForm({ ...form, copyright: e.target.value })} />
          <span className="muted small">в самом низу страницы</span>
        </div>
        <div className="kv"><span>Почта администратора</span>
          <input className="input mono" style={{ maxWidth: 320 }} value={form.admin_email || ""}
            placeholder="admin@corp.local"
            onChange={(e) => setForm({ ...form, admin_email: e.target.value })} />
          <span className="muted small">отображается рядом с копирайтом (mailto-ссылка)</span>
        </div>
        <div className="kv"><span>Секция «IP без hostname»</span>
          <label className="muted small" title="Секция «IP без hostname — внести в DNS» на странице сети">
            <input type="checkbox" checked={form.show_no_dns !== false}
              onChange={(e) => setForm({ ...form, show_no_dns: e.target.checked })} />
            отображать
          </label>
          <span className="muted small">вкл/выкл блок «IP без hostname — внести в DNS» на странице сети</span>
        </div>
        <div className="kv"><span>Поиск</span>
          <label className="muted small" title="Классический: ввели значение, нажали Enter — страница с результатами">
            <input type="radio" name="search_mode" checked={form.search_mode !== "live"}
              onChange={() => setForm({ ...form, search_mode: "page" })} />
            классический (страница результатов, по Enter)
          </label>
          <label className="muted small" title="Панель с результатами прямо при вводе">
            <input type="radio" name="search_mode" checked={form.search_mode === "live"}
              onChange={() => setForm({ ...form, search_mode: "live" })} />
            live (панель при вводе)
          </label>
          <span className="muted small">режим поиска в меню слева; по умолчанию — классический</span>
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Ссылки</div>
        <div className="muted small" style={{ marginBottom: 10 }}>
          Показываются в левом меню под «Настройками», над строкой «Выйти».
        </div>
        {(form.ui_links || []).map((l, i) => (
          <div className="kv" key={i}>
            <input className="input" style={{ maxWidth: 180 }} placeholder="Название"
              value={l.title} onChange={(e) => setLink(i, { title: e.target.value })} />
            <input className="input mono" style={{ maxWidth: 340 }} placeholder="https://…"
              value={l.url} onChange={(e) => setLink(i, { url: e.target.value })} />
            <label className="muted small" title="Снято — ссылка откроется в текущем окне">
              <input type="checkbox" checked={l.new_window}
                onChange={(e) => setLink(i, { new_window: e.target.checked })} />
              новое окно
            </label>
            <button className="btn small ghost danger" title="Убрать ссылку (после «Сохранить»)"
              onClick={() => setForm({ ...form, ui_links: (form.ui_links || []).filter((_, j) => j !== i) })}>
              🗑
            </button>
          </div>
        ))}
        {(form.ui_links || []).length === 0 && (
          <div className="muted small" style={{ marginBottom: 8 }}>ссылок пока нет</div>
        )}
        <div className="kv" style={{ marginTop: 10 }}>
          <span>Новая</span>
          <input className="input" style={{ maxWidth: 180 }} placeholder="Название"
            value={newLink.title} onChange={(e) => setNewLink({ ...newLink, title: e.target.value })} />
          <input className="input mono" style={{ maxWidth: 340 }} placeholder="https://…"
            value={newLink.url} onChange={(e) => setNewLink({ ...newLink, url: e.target.value })} />
          <label className="muted small" title="По умолчанию: открывать в новом окне">
            <input type="checkbox" checked={newLink.new_window}
              onChange={(e) => setNewLink({ ...newLink, new_window: e.target.checked })} />
            новое окно
          </label>
          <button className="btn small" onClick={addLink}
            disabled={!newLink.title.trim() || !newLink.url.trim()}>добавить</button>
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Домен (AD / LDAP)</div>
        <div className="kv"><span>Вход через домен</span>
          <label className="small">
            <input type="checkbox" checked={!!form.ldap_enabled} onChange={(e) => setForm({ ...form, ldap_enabled: e.target.checked })} />
            доменные пользователи заходят своим доменным паролем; первый вход — автоматически, с ролью по умолчанию
          </label>
        </div>
        <div className="kv"><span>LDAP-сервер</span>
          <input className="input mono" style={{ maxWidth: 380 }} value={form.ldap_url || ""}
            placeholder="ldap://dc1.corp.local:389 или ldaps://dc1:636"
            onChange={(e) => setForm({ ...form, ldap_url: e.target.value })} />
        </div>
        <div className="kv"><span>Base DN</span>
          <input className="input mono" style={{ maxWidth: 380 }} value={form.ldap_base_dn || ""}
            placeholder="DC=corp,DC=local" onChange={(e) => setForm({ ...form, ldap_base_dn: e.target.value })} />
        </div>
        <div className="kv"><span>Шаблон DN</span>
          <input className="input mono" style={{ maxWidth: 380 }} value={form.ldap_user_dn_template || ""}
            placeholder="{username} или CN={username},OU=Users,DC=corp,DC=local"
            onChange={(e) => setForm({ ...form, ldap_user_dn_template: e.target.value })} />
          <span className="muted small">прямой bind по DN (быстрый путь, без поиска)</span>
        </div>
        <div className="kv"><span>Фильтр поиска</span>
          <input className="input mono" style={{ maxWidth: 380 }} value={form.ldap_search_filter || ""}
            placeholder="(sAMAccountName={username})"
            onChange={(e) => setForm({ ...form, ldap_search_filter: e.target.value })} />
        </div>
        <div className="kv"><span>Служебная учётка</span>
          <input className="input mono" style={{ maxWidth: 280 }} value={form.ldap_bind_dn || ""}
            placeholder="CN=ipam,OU=Service,DC=corp,DC=local (для поиска)"
            onChange={(e) => setForm({ ...form, ldap_bind_dn: e.target.value })} />
          <input className="input mono" style={{ maxWidth: 200 }} type="password" value={form.ldap_bind_password || ""}
            placeholder="пароль (пусто/•••• = не менять)" onChange={(e) => setForm({ ...form, ldap_bind_password: e.target.value })} />
        </div>
        <div className="kv"><span>Роль при первом входе</span>
          <select className="input" value={form.ldap_default_role || "viewer"} onChange={(e) => setForm({ ...form, ldap_default_role: e.target.value })}>
            <option value="viewer">Только чтение</option>
            <option value="operator">Оператор</option>
            <option value="admin">Администратор</option>
          </select>
          <span className="muted small">потом меняется в таблице «Пользователи»</span>
        </div>
        <div className="kv"><span>Допущенные</span>
          <input className="input mono" style={{ maxWidth: 420 }} placeholder="ivanov, petrov, sidorov (через запятую)"
            value={form.ldap_allow_list || ""} onChange={(e) => setForm({ ...form, ldap_allow_list: e.target.value })} />
          <span className="muted small">по умолчанию (пусто) входить могут ЛЮБЫЕ доменные пользователи;
            если список указан — только они. Локальные учётные записи не затрагивает</span>
        </div>
        <div className="muted small" style={{ margin: "4px 0 10px" }}>
          Локальный админ (ADMIN_USER) всегда может войти независимо от состояния домена.
        </div>
        <div className="btn-row"><button className="btn primary" onClick={save}>Сохранить</button></div>
      </div>

      <div className="card">
        <div className="card-title">Почта (уведомления о сканах)</div>
        <div className="kv"><span>Включено</span>
          <label className="small">
            <input type="checkbox" checked={form.mail_enabled} onChange={(e) => setForm({ ...form, mail_enabled: e.target.checked })} />
            отправлять письмо после скана, если есть новые/освобождённые IP или «живой резерв»
          </label>
        </div>
        <div className="kv"><span>SMTP-сервер</span>
          <input className="input mono" style={{ maxWidth: 320 }} value={form.smtp_host} placeholder="smtp.example.com"
            onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
          <input className="input narrow" type="number" min={1} max={65535} value={form.smtp_port} title="Порт (465 = SSL)"
            onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) || 587 })} />
        </div>
        <div className="kv"><span>Пользователь SMTP</span>
          <input className="input mono" style={{ maxWidth: 320 }} value={form.smtp_user}
            onChange={(e) => setForm({ ...form, smtp_user: e.target.value })} />
          <input className="input mono" style={{ maxWidth: 320 }} type="password" value={form.smtp_password}
            placeholder="•••• = не менять"
            onChange={(e) => setForm({ ...form, smtp_password: e.target.value })} />
          <label className="small"><input type="checkbox" checked={form.smtp_starttls}
            onChange={(e) => setForm({ ...form, smtp_starttls: e.target.checked })} /> STARTTLS</label>
        </div>
        <div className="kv"><span>От кого</span>
          <input className="input mono" style={{ maxWidth: 320 }} value={form.mail_from} placeholder="ipam@example.com"
            onChange={(e) => setForm({ ...form, mail_from: e.target.value })} />
        </div>
        <div className="kv"><span>Получатели</span>
          <input className="input mono" style={{ maxWidth: 420 }} value={form.mail_to} placeholder="a@example.com, b@example.com"
            onChange={(e) => setForm({ ...form, mail_to: e.target.value })} />
        </div>
        <div className="btn-row">
          <button className="btn primary" onClick={save}>Сохранить</button>
          <button className="btn" onClick={testMail} disabled={testing}>{testing ? "Отправка…" : "Отправить тестовое письмо"}</button>
        </div>
      </div>

      <div className="card">
        <div className="card-title row">
          Пользователи
          <span className="muted small">админ добавляет тех, кто может входить; доменные заходят своим доменным паролем</span>
          <button className="btn small" style={{ marginLeft: "auto" }} onClick={() => setShowAddUser(true)}>+ Добавить</button>
        </div>
        <table className="table">
          <thead><tr>
            <Th label="Имя" k="username" sort={userSort} />
            <Th label="Тип" k="provider" sort={userSort} />
            <Th label="Роль" k="role" sort={userSort} />
            <Th label="Создан" k="created" sort={userSort} />
            <th></th>
          </tr></thead>
          <tbody>
            {sortedUsers.map((u) => (
              <tr key={u.id}>
                <td>
                  {u.display_name || <span className="mono">{u.username}</span>}
                  {u.display_name && <span className="muted small"> ({u.username})</span>}
                </td>
                <td><span className={`tag ${u.provider === "ldap" ? "tag-ldap" : ""}`}>{u.provider === "ldap" ? "домен" : "локальный"}</span></td>
                <td><span className="tag">{ROLE_RU[u.role] || u.role}</span></td>
                <td className="muted small">{fmt(u.created_at)}</td>
                <td className="actions-cell">
                  <button className="btn ghost small" onClick={() => setEditUser(u)}>изменить</button>
                  <button
                    className="btn ghost small danger"
                    onClick={async () => {
                      if (confirm(`Удалить пользователя ${u.username}?`)) {
                        try {
                          await api(`/users/${u.id}`, { method: "DELETE" });
                          setUsers((list) => list.filter((x) => x.id !== u.id));
                        } catch (e: any) { setErr(e.message); }
                      }
                    }}
                  >удалить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-title row">
          Агенты (L2-данные из VLAN)
          <span className="muted small">скрипт на машине в сети по cron → POST /api/agent/report</span>
          <button className="btn small" style={{ marginLeft: "auto" }} onClick={() => setAgentModal("new")}>+ Добавить</button>
        </div>
        <table className="table">
          <thead><tr>
            <Th label="Имя" k="name" sort={agentSort} />
            <Th label="Сети" k="subnets" sort={agentSort} />
            <Th label="SSH-хост" k="ssh" sort={agentSort} />
            <Th label="Последний отчёт" k="last_report" sort={agentSort} />
            <Th label="Установка" k="install" sort={agentSort} />
            <th>Опрос</th>
            <th></th>
          </tr></thead>
          <tbody>
            {sortedAgents.map((a) => (
              <tr key={a.id}>
                <td>{a.name}{a.descr && <div className="muted small">{a.descr}</div>}</td>
                <td className="muted small">
                  {a.subnet_ids && a.subnet_ids.length
                    ? a.subnet_ids.map((id) => subnets.find((s) => s.id === id)?.cidr || id).join(", ")
                    : "все сети"}
                </td>
                <td className="mono small">{a.ssh_host ? `${a.ssh_user}@${a.ssh_host}${a.ssh_port ? `:${a.ssh_port}` : ""}` : <span className="muted">—</span>}</td>
                <td className="muted small">
                  {a.last_report_at ? fmt(a.last_report_at) : "ещё не было"}
                  {a.last_hosts != null && <div className="muted">{a.last_hosts} хостов</div>}
                  <div><button className="btn ghost small" onClick={() => setReportsAgent(a)}>отчёты</button></div>
                </td>
                <td className="muted small">
                  {a.last_install_at ? `${fmt(a.last_install_at)} ${a.install_log && a.install_log.every((s) => s.ok) ? "✓" : "⚠"}` : "не устанавливался"}
                  {a.install_log && <div><button className="btn ghost small" onClick={() => setInstallModal({ agent: a, mode: "log" })}>лог установки</button></div>}
                </td>
                <td className="muted small">
                  <button className="btn small" disabled={!a.ssh_host || forceState[a.id]?.state === "running"}
                    title={a.ssh_host ? "Ядро по SSH тронет файл-триггер; агент отработает на ближайшем cron-цикле (каждые 5 мин)" : "Сначала укажите SSH-хост в «Изменить»"}
                    onClick={() => doForcePoll(a)}>
                    {forceState[a.id]?.state === "running" ? "отправка…" : "принудительный"}
                  </button>
                  {forceState[a.id] && forceState[a.id].state !== "running" && (
                    <div className={forceState[a.id].state === "error" ? "bad" : "good"}>{forceState[a.id].detail}</div>
                  )}
                </td>
                <td className="actions-cell">
                  <button className="btn small" disabled={!a.ssh_host} title={a.ssh_host ? "Установить агента удалённо по SSH" : "Сначала укажите SSH-хост в «Изменить»"}
                    onClick={() => setInstallModal({ agent: a, mode: "start" })}>установить</button>
                  <button className="btn ghost small" onClick={() => {
                    if (a) window.prompt("Ключ агента (скопируйте в скрипт):", a.key);
                  }}>ключ</button>
                  <button className="btn ghost small" onClick={() => setAgentModal(a)}>изменить</button>
                  <button className="btn ghost small danger" onClick={async () => {
                    if (confirm(`Удалить агента ${a.name}?${a.ssh_host ? "\n(на хосте будет удалён агент: скрипт, env, cron)" : ""}`)) {
                      const res = await api<{ deleted: boolean; remote_cleanup: string }>(`/agents/${a.id}`, { method: "DELETE" });
                      setAgents((l) => l.filter((x) => x.id !== a.id));
                      if (res && res.remote_cleanup) alert(`Агент удалён.\n${res.remote_cleanup}`);
                    }
                  }}>удалить</button>
                </td>
              </tr>
            ))}
            {sortedAgents.length === 0 && <tr><td colSpan={7} className="muted">агентов нет</td></tr>}
          </tbody>
        </table>
        <div className="muted small" style={{ marginTop: 8 }}>
          Скрипт: <span className="mono">agent/ipam_agent.sh</span> (только stdlib). cron-пример в README.
        </div>
      </div>

      <PhpIPAMCard />

      {editUser && <UserModal user={editUser} onClose={() => setEditUser(null)} onSaved={(u) => { setU(u); setEditUser(null); }} onErr={setErr} />}
      {showAddUser && <UserModal user={null} onClose={() => setShowAddUser(false)} onSaved={(u) => { setUsers((l) => [...l, u]); setShowAddUser(false); }} onErr={setErr} />}
      {installModal && (
        <InstallModal
          agent={installModal.agent}
          mode={installModal.mode}
          onClose={() => setInstallModal(null)}
          onAgentsChanged={load}
        />
      )}
      {reportsAgent && <ReportsModal agent={reportsAgent} onClose={() => setReportsAgent(null)} />}
      {agentModal && (
        <AgentModal
          agent={agentModal === "new" ? null : agentModal}
          subnets={subnets}
          onClose={() => setAgentModal(null)}
          onSaved={(a) => {
            setAgents((l) => (l.some((x) => x.id === a.id) ? l.map((x) => (x.id === a.id ? a : x)) : [...l, a]));
            setAgentModal(null);
          }}
          onErr={setErr}
        />
      )}
    </div>
  );
}

function UserModal({ user, onClose, onSaved, onErr }: {
  user: UserOut | null;
  onClose: () => void;
  onSaved: (u: UserOut) => void;
  onErr: (e: string) => void;
}) {
  const [username, setUsername] = useState(user?.username ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(user?.role ?? "operator");
  const [provider, setProvider] = useState(user?.provider ?? "local");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const isLdap = provider === "ldap";

  const clearDb = async () => {
    if (!window.confirm(
      "Очистить базу данных?\n\nБудут удалены ВСЕ данные: сети, IP, VLAN, агенты, документы, сканы, события.\nПользователи и настройки сохранятся.\nДействие необратимо."
    )) return;
    try {
      await api("/admin/clear-db", { method: "POST" });
      window.alert("База данных очищена.");
      window.location.reload();
    } catch (e: any) {
      onErr(e.message);
    }
  };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      if (user) {
        const body: any = { role };
        if (username !== user.username) body.username = username;
        if (password) body.password = password;
        onSaved(await api<UserOut>(`/users/${user.id}`, { method: "PUT", body: JSON.stringify(body) }));
      } else {
        onSaved(await api<UserOut>("/users", {
          method: "POST",
          body: JSON.stringify({ username, password: isLdap ? undefined : password, role, provider }),
        }));
      }
    } catch (e: any) {
      setErr(e.message);
      onErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={user ? `Пользователь: ${user.username}` : "Новый пользователь"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      {!user && (
        <div className="kv"><span>Тип</span>
          <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="local">Локальный (пароль в IPAM)</option>
            <option value="ldap">Доменный (вход через домен)</option>
          </select>
        </div>
      )}
      {user && (
        <div className="kv"><span>Тип</span>
          <span className={`tag ${user.provider === "ldap" ? "tag-ldap" : ""}`}>{user.provider === "ldap" ? "доменный" : "локальный"}</span>
        </div>
      )}
      <div className="kv"><span>Имя</span>
        <input className="input" value={username} disabled={!!user}
          placeholder={isLdap ? "логин в домене (sAMAccountName)" : "имя пользователя"}
          onChange={(e) => setUsername(e.target.value)} />
      </div>
      <div className="kv"><span>Пароль</span>
        {isLdap ? (
          <span className="muted small">пароль задаётся в домене</span>
        ) : (
          <input className="input" type="password" value={password}
            placeholder={user ? "не менять" : "минимум 4 символа"} onChange={(e) => setPassword(e.target.value)} />
        )}
      </div>
      {user && user.username === "admin" && user.provider === "local" && (
        <div className="kv"><span>База данных</span>
          <button className="btn small danger" onClick={clearDb}>Очистить БД</button>
          <span className="muted small">удалит сети, IP, VLAN, агентов, документы и сканы; пользователи и настройки сохранятся</span>
        </div>
      )}
      <div className="kv"><span>Роль</span>
        <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="admin">Администратор</option>
          <option value="operator">Оператор</option>
          <option value="viewer">Только чтение</option>
        </select>
      </div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}

function InstallModal({ agent, mode, onClose, onAgentsChanged }: {
  agent: AgentOut;
  mode: "start" | "log";
  onClose: () => void;
  onAgentsChanged: () => void;
}) {
  const [state, setState] = useState<InstallState | null>(null);
  const [err, setErr] = useState("");
  const refreshed = useRef(false);

  useEffect(() => {
    let started = false;
    const poll = async () => {
      try {
        const s = await api<InstallState>(`/agents/${agent.id}/install-state`);
        setState(s);
        if (s.state !== "running" && s.state !== "idle" && !refreshed.current) {
          refreshed.current = true;
          onAgentsChanged();
        }
      } catch (e: any) {
        setErr(e.message);
      }
    };
    if (mode === "start") {
      api(`/agents/${agent.id}/install`, { method: "POST" })
        .then(() => { started = true; poll(); })
        .catch((e: any) => setErr(e.message));
    } else if (agent.install_log) {
      setState({ state: "done", steps: agent.install_log, finished_at: agent.last_install_at });
    }
    const timer = setInterval(poll, 2000);
    return () => clearInterval(timer);
  }, [agent.id, mode]);

  const allOk = state?.steps.length ? state.steps.every((s) => s.ok) : false;
  return (
    <Modal title={mode === "start" ? `Установка агента: ${agent.name}` : `Лог установки: ${agent.name}`} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      {mode === "start" && state?.state === "running" && (
        <div className="muted small" style={{ marginBottom: 8 }}>
          Подключаемся по SSH ({agent.ssh_user}@{agent.ssh_host}), проверяем компоненты и репозитории, при необходимости ставим недостающее…
        </div>
      )}
      <div className="install-steps">
        {(state?.steps ?? []).map((s, i) => (
          <div key={i} className="install-step">
            <span className={s.ok ? "good" : "bad"}>{s.ok ? "✓" : "✗"}</span>
            <span className="step-name">{s.step}</span>
            <span className="muted small step-detail">{s.detail}</span>
          </div>
        ))}
        {(!state?.steps || state.steps.length === 0) && (
          <div className="muted small">{mode === "start" ? "Ждём первый шаг…" : "лог пуст"}</div>
        )}
      </div>
      {state?.state === "done" && state.steps.length > 0 && (
        allOk
          ? <div className="install-done">Установка завершена успешно. Агент будет присылать отчёт каждые 5 минут.</div>
          : <div className="install-warn">Установка завершилась с ошибками — смотрите шаги. Почините и нажмите «установить» ещё раз.</div>
      )}
    </Modal>
  );
}

function ReportsModal({ agent, onClose }: {
  agent: AgentOut;
  onClose: () => void;
}) {
  const [reports, setReports] = useState<AgentReportOut[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api<AgentReportOut[]>(`/agents/${agent.id}/reports?limit=200`)
      .then(setReports)
      .catch((e: any) => setErr(e.message));
  }, [agent.id]);

  const today = new Date().toDateString();
  const todayCount = reports?.filter((r) => new Date(r.at).toDateString() === today).length ?? 0;

  return (
    <Modal title={`Отчёты агента: ${agent.name}`} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      {!reports && !err && <div className="muted small">загрузка…</div>}
      {reports && (
        <>
          <div className="muted small" style={{ marginBottom: 8 }}>
            всего: {reports.length} · за сегодня: {todayCount} · храним 14 дней
          </div>
          <div style={{ maxHeight: 380, overflowY: "auto" }}>
            <table className="table">
              <thead>
                <tr><th>Время (локальное)</th><th>Хостов</th><th>Применено</th><th>С MAC</th></tr>
              </thead>
              <tbody>
                {reports.map((r, i) => (
                  <tr key={i}>
                    <td className="mono small">{fmt(r.at)}</td>
                    <td className="mono small">{r.hosts}</td>
                    <td className="mono small">{r.applied}</td>
                    <td className="mono small">{r.with_mac}</td>
                  </tr>
                ))}
                {reports.length === 0 && <tr><td colSpan={4} className="muted">пока нет отчётов</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}

function AgentModal({ agent, subnets, onClose, onSaved, onErr }: {
  agent: AgentOut | null;
  subnets: Subnet[];
  onClose: () => void;
  onSaved: (a: AgentOut) => void;
  onErr: (e: string) => void;
}) {
  const [name, setName] = useState(agent?.name ?? "");
  const [subnetIds, setSubnetIds] = useState<number[]>(agent?.subnet_ids ?? []);
  const [descr, setDescr] = useState(agent?.descr ?? "");
  const [regenerate, setRegenerate] = useState(false);
  const [sshHost, setSshHost] = useState(agent?.ssh_host ?? "");
  const [sshPort, setSshPort] = useState(agent?.ssh_port ?? "");
  const [sshUser, setSshUser] = useState(agent?.ssh_user ?? "");
  const [sshPass, setSshPass] = useState("");
  const [pollFile, setPollFile] = useState(agent?.poll_file ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const toggle = (id: number) =>
    setSubnetIds((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const sshBody = {
        ssh_host: sshHost || null,
        ssh_port: sshPort ? Number(sshPort) : null,
        ssh_user: sshUser || null,
        ssh_password: sshPass || undefined,
        poll_file: pollFile || null,
      };
      if (agent) {
        onSaved(await api<AgentOut>(`/agents/${agent.id}`, {
          method: "PUT",
          body: JSON.stringify({ name, subnet_ids: subnetIds.length ? subnetIds : null, descr: descr || null, regenerate_key: regenerate, ...sshBody }),
        }));
      } else {
        const a = await api<AgentOut>("/agents", {
          method: "POST",
          body: JSON.stringify({ name, subnet_ids: subnetIds.length ? subnetIds : null, descr: descr || null, ...sshBody }),
        });
        window.prompt("Ключ агента (сохраните — в скрипт IPAM_AGENT_KEY):", a.key);
        onSaved(a);
      }
    } catch (e: any) {
      setErr(e.message);
      onErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={agent ? `Агент: ${agent.name}` : "Новый агент"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Имя</span>
        <input className="input" value={name} placeholder="vlan-10-core" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="kv"><span>Сети</span>
        <div className="agent-subnets">
          {subnets.map((s) => (
            <label key={s.id} className="small">
              <input type="checkbox" checked={subnetIds.includes(s.id)} onChange={() => toggle(s.id)} />{" "}
              {s.name} <span className="mono muted">{s.cidr}</span>
            </label>
          ))}
          {subnets.length === 0 && <span className="muted small">сети не найдены</span>}
        </div>
        <span className="muted small">ничего не отмечено = все сети</span>
      </div>
      <div className="kv"><span>Описание</span>
        <input className="input" value={descr} onChange={(e) => setDescr(e.target.value)} />
      </div>
      {agent && (
        <div className="kv"><span>Ключ</span>
          <label className="small">
            <input type="checkbox" checked={regenerate} onChange={(e) => setRegenerate(e.target.checked)} />
            пересоздать ключ (старый перестанет работать)
          </label>
        </div>
      )}
      <div className="card-title" style={{ marginTop: 10 }}>SSH — удалённая установка</div>
      <div className="kv"><span>SSH-хост</span>
        <input className="input mono" style={{ maxWidth: 240 }} value={sshHost} placeholder="10.32.11.2 (IP машины агента)"
          onChange={(e) => setSshHost(e.target.value)} />
      </div>
      <div className="kv"><span>Порт / пользователь</span>
        <input className="input narrow" type="number" min={1} max={65535} value={sshPort} placeholder="22"
          onChange={(e) => setSshPort(e.target.value)} />
        <input className="input mono" style={{ maxWidth: 180 }} value={sshUser} placeholder="root"
          onChange={(e) => setSshUser(e.target.value)} />
      </div>
      <div className="kv"><span>SSH-пароль</span>
        <input className="input mono" style={{ maxWidth: 240 }} type="password" value={sshPass}
          placeholder={agent ? "пусто = не менять" : "пароль пользователя"}
          onChange={(e) => setSshPass(e.target.value)} />
      </div>
      <div className="kv"><span>Файл триггера опроса</span>
        <input className="input mono" style={{ maxWidth: 280 }} value={pollFile}
          placeholder="/var/lib/ipam_agent/force_poll" onChange={(e) => setPollFile(e.target.value)} />
        <span className="muted small">«Принудительный опрос» трогает этот файл на агенте по SSH</span>
      </div>
      <div className="muted small" style={{ margin: "0 0 10px" }}>
        Пользователю нужны root-права (установка пакетов, cron, /opt). Кнопка «установить» в таблице:
        проверка ОС, python3/nmap/iproute2/cron, интернет-репозиториев, установка недостающего, деплой скрипта, cron и тест.
      </div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Импорт из phpIPAM (REST API)
// ---------------------------------------------------------------------------
interface PhpIPAMReport {
  vlans_new: number; vlans_existing: number; vlans_dup_skip?: number;
  subnets_new: number; subnets_update: number; subnets_skip: number; subnets_overlap_skip?: number;
  ips_new: number; ips_update: number; ips_skip: number; ips_unused_skip?: number;
  issues: string[];
}

function PhpIPAMCard() {
  const [base, setBase] = useState("");
  const [app, setApp] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [importIps, setImportIps] = useState(false);
  const [insecure, setInsecure] = useState(false);
  const [busy, setBusy] = useState<"" | "check" | "preview" | "apply">("");
  const [checkRes, setCheckRes] = useState<any>(null);
  const [report, setReport] = useState<PhpIPAMReport | null>(null);
  const [err, setErr] = useState("");

  const payload = () => JSON.stringify({ base_url: base, app, username, password, import_ips: importIps, insecure });
  const canRun = base.trim() && app.trim() && username.trim() && password.trim();

  const run = async (kind: "check" | "preview" | "apply") => {
    setErr("");
    if (kind !== "check") setReport(null);
    if (kind === "check") setCheckRes(null);
    setBusy(kind);
    try {
      const r = await api<any>(`/import/phpipam/${kind}`, { method: "POST", body: payload() });
      if (kind === "check") setCheckRes(r);
      else setReport(r);
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy("");
  };

  const rows: [string, keyof PhpIPAMReport | null][] = [
    ["VLAN: новых", "vlans_new"], ["VLAN: уже есть в ядре", "vlans_existing"], ["VLAN: дубликаты в phpIPAM (пропущены)", "vlans_dup_skip"],
    ["Сети: новые", "subnets_new"], ["Сети: обновить (имя/описание)", "subnets_update"], ["Сети: без изменений", "subnets_skip"],
    ["Сети: пересечение с существующей (пропущены)", "subnets_overlap_skip"],
    ["IP: новых", "ips_new"], ["IP: обновить (помечены занятыми/резервом)", "ips_update"], ["IP: без изменений", "ips_skip"],
    ["IP: свободные в phpIPAM (не тронули)", "ips_unused_skip"],
  ];

  return (
    <div className="card">
      <div className="card-title">Импорт из phpIPAM</div>
      <div className="muted small" style={{ marginBottom: 10 }}>
        Массовый перенос VLAN, сетей и IP из phpIPAM (REST API, v1.3–1.8; проверено по v1.8.1).
        В phpIPAM сначала создайте API-приложение: Administration → Edit API settings →
        Apps (имя, права: read) — его имя укажите ниже. Пароль используется только
        для получения сессионного токена и нигде не сохраняется.
      </div>
      <div className="kv"><span>Адрес phpIPAM</span>
        <input className="input mono" style={{ maxWidth: 380 }} placeholder="https://ipam.corp.local/phpipam"
          value={base} onChange={(e) => setBase(e.target.value)} />
      </div>
      <div className="kv"><span>API-приложение</span>
        <input className="input mono" style={{ maxWidth: 380 }} placeholder="имя приложения (напр. ipam_import)"
          value={app} onChange={(e) => setApp(e.target.value)} />
      </div>
      <div className="kv"><span>Пользователь</span>
        <input className="input mono" style={{ maxWidth: 380 }} placeholder="пользователь phpIPAM"
          value={username} onChange={(e) => setUsername(e.target.value)} />
      </div>
      <div className="kv"><span>Пароль</span>
        <input className="input mono" type="password" style={{ maxWidth: 380 }} placeholder="пароль пользователя phpIPAM"
          value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <div className="kv"><span>IP-адреса</span>
        <label className="muted small">
          <input type="checkbox" checked={importIps} onChange={(e) => setImportIps(e.target.checked)} />
          импортировать IP (медленнее: запрос на каждую сеть)
        </label>
      </div>
      <div className="kv"><span>SSL</span>
        <label className="muted small">
          <input type="checkbox" checked={insecure} onChange={(e) => setInsecure(e.target.checked)} />
          самоподписанный сертификат phpIPAM (не проверять SSL)
        </label>
        <span className="muted small">нужно, если у phpIPAM свой/корпоративный самоподписанный сертификат (аналог curl -k)</span>
      </div>
      <div className="btn-row" style={{ marginTop: 4 }}>
        <button className="btn" onClick={() => run("check")} disabled={busy !== "" || !canRun}>
          {busy === "check" ? "…" : "Проверить соединение"}
        </button>
        <button className="btn" onClick={() => run("preview")} disabled={busy !== "" || !canRun}>
          {busy === "preview" ? "Считаю…" : "Предпросмотр"}
        </button>
        <button className="btn primary" disabled={busy !== "" || !report}
          onClick={() => { if (window.confirm("Применить импорт? Данные будут добавлены/обновлены в ядре.")) run("apply"); }}>
          {busy === "apply" ? "Применяю…" : "Применить"}
        </button>
      </div>
      {err && <div className="error" style={{ marginTop: 8 }}>{err}</div>}
      {checkRes && (
        <div className="good small" style={{ marginTop: 8 }}>
          Соединение OK: сетей {checkRes.subnets}, VLAN {checkRes.vlans} ({checkRes.base})
        </div>
      )}
      {report && (
        <div style={{ marginTop: 10 }}>
          <table className="table" style={{ maxWidth: 560 }}>
            <tbody>
              {rows.filter(([, k]) => k !== null && report[k] !== undefined).map(([label, k]) => (
                <tr key={String(k)}><td>{label}</td><td className="mono">{report[k as keyof PhpIPAMReport]}</td></tr>
              ))}
            </tbody>
          </table>
          {report.issues.length > 0 && (
            <div className="muted small" style={{ marginTop: 6 }}>
              замечания:
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {report.issues.map((x, i) => <li key={i}>{x}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
