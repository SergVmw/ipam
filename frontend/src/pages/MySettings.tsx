import { useEffect, useState } from "react";
import { api } from "../api";
import type { MyPrefs } from "../types";

const ROLE_RU: Record<string, string> = {
  admin: "Администратор",
  operator: "Редактор (оператор)",
  viewer: "Пользователь (только чтение)",
};

const LAYOUT_RU: Record<string, string> = { ipam: "новый", phpipam: "классический" };

// Личные настройки текущего пользователя (в отличие от глобальных «Настроек»
// админ-раздела). Доступны всем ролям.
export default function MySettings() {
  const [prefs, setPrefs] = useState<MyPrefs | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [email, setEmail] = useState("");
  const [uiLayout, setUiLayout] = useState<"ipam" | "phpipam">("ipam");
  const [sideMode, setSideMode] = useState<"vlan" | "subnets">("vlan");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<MyPrefs>("/me/prefs")
      .then((p) => {
        setPrefs(p);
        setEmail(p.email || "");
        setUiLayout((p.ui_layout || p.ui_layout_effective || "ipam") as "ipam" | "phpipam");
        setSideMode(p.side_mode || "vlan");
      })
      .catch((e) => setErr(e.message));
  }, []);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3500); };

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const p = await api<MyPrefs>("/me/prefs", {
        method: "PUT",
        body: JSON.stringify({ email: email.trim(), ui_layout: uiLayout, side_mode: sideMode }),
      });
      setPrefs(p);
      setMsg("");
      flash("Сохранено — внешний вид применится при переходе по меню");
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const resetLayout = async () => {
    setBusy(true);
    setErr("");
    try {
      const p = await api<MyPrefs>("/me/prefs", {
        method: "PUT",
        body: JSON.stringify({ ui_layout: "" }),
      });
      setPrefs(p);
      setUiLayout(p.ui_layout_effective);
      flash("Внешний вид — как у администратора");
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  const fromDomain = () => {
    if (prefs?.email_suggested) {
      setEmail(prefs.email_suggested);
      flash("Подставлена почта из домена — нажмите «Сохранить»");
    }
  };

  if (!prefs) {
    return <div className="page">{err ? <div className="error">{err}</div> : "Загрузка…"}</div>;
  }

  const roleLabel = ROLE_RU[prefs.role] || prefs.role;
  const globalLayout = prefs.ui_layout_global || "ipam";

  return (
    <div className="page">
      <div className="page-head"><h1>Мои настройки</h1></div>
      {err && <div className="error">{err}</div>}
      {msg && <div className="error" style={{ background: "rgba(34,197,94,.1)", borderColor: "rgba(34,197,94,.4)", color: "#86efac" }}>{msg}</div>}

      <div className="card">
        <div className="card-title">Профиль</div>
        <div className="kv"><span>Имя входа</span>
          <b>{prefs.username}</b>
          <span className="tag" style={{ marginLeft: 4 }}>{roleLabel}</span>
          <span className="muted small">
            {prefs.provider === "ldap" ? "доменная учётная запись (AD/LDAP)" : "локальная учётная запись"}
          </span>
        </div>
        <div className="kv"><span>Почта</span>
          <input className="input mono" style={{ maxWidth: 320 }} value={email}
            placeholder={prefs.email_suggested || "не задана"}
            onChange={(e) => setEmail(e.target.value)} />
          {prefs.email_suggested && !email && (
            <button className="btn small" onClick={fromDomain} title={`Подставить ${prefs.email_suggested}`}>
              взять из домена
            </button>
          )}
          {prefs.email_suggested && email === prefs.email_suggested && (
            <span className="muted small">почта из домена</span>
          )}
          <span className="muted small">
            {prefs.provider === "ldap" && !prefs.email_suggested && email === ""
              ? "домен для почты не определён"
              : "используется для уведомлений"}
          </span>
        </div>
        <div className="kv"><span>Отображаемое имя</span>
          <b>{prefs.display_name || "—"}</b>
          <span className="muted small">{prefs.provider === "ldap" ? "берётся из домена при входе" : ""}</span>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Внешний вид</div>

        <div className="kv"><span>Вид интерфейса</span>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span className="pip-seg seg-big" title="Какой внешний вид использовать">
              <button className={"pip-seg-btn" + (uiLayout === "ipam" ? " on" : "")}
                onClick={() => setUiLayout("ipam")}>новый</button>
              <button className={"pip-seg-btn" + (uiLayout === "phpipam" ? " on" : "")}
                onClick={() => setUiLayout("phpipam")}>классический</button>
            </span>
            {prefs.ui_layout && (
              <button className="btn small ghost" onClick={resetLayout} title="Вернуться к глобальной настройке администратора">
                по умолчанию
              </button>
            )}
            <span className="muted small">
              {prefs.ui_layout
                ? <>личный выбор ({LAYOUT_RU[uiLayout]}) · у администратора: {LAYOUT_RU[globalLayout] || "—"}</>
                : <>по умолчанию — как у администратора: {LAYOUT_RU[globalLayout] || "—"}</>}
            </span>
          </div>
        </div>

        {uiLayout === "phpipam" ? (
          <div className="kv" style={{ alignItems: "flex-start" }}>
            <span>Классический вид — левая колонка</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label className="small" title="В левой колонке — VLAN, внутри каждой — её сети">
                <input type="radio" name="side_mode" checked={sideMode === "vlan"}
                  onChange={() => setSideMode("vlan")} /> VLAN
                <span className="muted" style={{ marginLeft: 8 }}>по умолчанию раскрывать VLAN</span>
              </label>
              <label className="small" title="В левой колонке — плоский список всех сетей">
                <input type="radio" name="side_mode" checked={sideMode === "subnets"}
                  onChange={() => setSideMode("subnets")} /> Сети
                <span className="muted" style={{ marginLeft: 8 }}>по умолчанию показывать списком все сети</span>
              </label>
              <span className="muted small">
                что показывать в левой колонке классического вида (меню сверху) по умолчанию.
                Переключается и прямо в самой колонке — выбор запоминается.
              </span>
            </div>
          </div>
        ) : (
          <div className="muted small" style={{ margin: "-2px 0 12px" }}>
            В «новом» виде (меню слева) левая колонка с VLAN/сетями не используется —
            настройка выше появится при выборе «классический».
          </div>
        )}

        <div className="btn-row">
          <button className="btn primary" onClick={save} disabled={busy}>{busy ? "…" : "Сохранить"}</button>
        </div>
      </div>

      {prefs.role !== "admin" && (
        <div className="muted small" style={{ marginTop: 6, lineHeight: 1.6 }}>
          Административные настройки (импорт из phpIPAM, оформление сайта, ссылки, домен AD/LDAP,
          агенты L2) доступны только администратору в разделе «Настройки».
        </div>
      )}
    </div>
  );
}
