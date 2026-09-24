import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState<{ ui_logo?: string; copyright?: string }>({});
  const nav = useNavigate();

  // публичное: логотип и копирайт из настроек сайта
  useEffect(() => {
    api<{ ui_logo?: string; copyright?: string }>("/meta").then(setMeta).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r: any = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setToken(r.token);
      nav("/");
    } catch (e: any) {
      setErr(e.message || "Не удалось войти");
    }
    setBusy(false);
  };

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <div className="logo big">IPAM</div>
        {meta.ui_logo && <img className="login-logo" src={meta.ui_logo} alt="logo" />}
        {err && <div className="error">{err}</div>}
        <input className="input" placeholder="Логин" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        <input className="input" type="password" placeholder="Пароль" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button className="btn primary" disabled={busy}>{busy ? "…" : "Войти"}</button>
        {meta.copyright && <div className="muted small login-copyright">{meta.copyright}</div>}
      </form>
    </div>
  );
}
