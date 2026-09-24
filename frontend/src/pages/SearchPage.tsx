import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";

interface Results {
  subnets: { id: number; name: string; cidr: string }[];
  vlans: { id: number; vid: number; name: string; color: string | null }[];
  ips: { ip: string; hostname: string | null; owner: string | null; subnet_id: number; subnet_name: string; cidr: string }[];
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const nav = useNavigate();
  const [input, setInput] = useState(q);
  const [res, setRes] = useState<Results | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    setInput(q);
    if (q.trim().length < 2) {
      setRes(null);
      return;
    }
    setLoading(true);
    setErr("");
    api<Results>(`/search?q=${encodeURIComponent(q.trim())}&limit=100`)
      .then(setRes)
      .catch((e: any) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [q]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = input.trim();
    if (v.length < 2) return;
    setParams({ q: v });
  };

  const total = res ? res.subnets.length + res.vlans.length + res.ips.length : 0;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Поиск</h1>
        <span className="muted small">сети, VLAN, IP-адреса (имя хоста, владелец) — до 100 результатов в группе</span>
      </div>

      <form className="search-page-form" onSubmit={submit}>
        <input
          className="input"
          style={{ maxWidth: 480 }}
          placeholder="Введите значение: сеть, CIDR, VLAN, IP, hostname, владелец…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          autoFocus
        />
        <button className="btn primary" type="submit" disabled={input.trim().length < 2 || loading}>
          {loading ? "Ищем…" : "Искать"}
        </button>
      </form>

      {err && <div className="error">{err}</div>}

      {q.trim().length >= 2 && res && !loading && (
        <>
          <div className="muted small" style={{ margin: "10px 0" }}>
            по запросу «{q.trim()}» найдено: <b>{total}</b>
          </div>

          {res.subnets.length > 0 && (
            <div className="card">
              <div className="card-title">Сети ({res.subnets.length})</div>
              <table className="table">
                <thead><tr><th>Имя</th><th>CIDR</th><th></th></tr></thead>
                <tbody>
                  {res.subnets.map((s) => (
                    <tr key={s.id} className="clickable" onClick={() => nav(`/subnets/${s.id}`)}>
                      <td>{s.name}</td>
                      <td className="mono">{s.cidr}</td>
                      <td><Link to={`/subnets/${s.id}`} className="link">открыть →</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {res.vlans.length > 0 && (
            <div className="card">
              <div className="card-title">VLAN ({res.vlans.length})</div>
              <table className="table">
                <thead><tr><th>Имя</th><th>VID</th><th></th></tr></thead>
                <tbody>
                  {res.vlans.map((v) => (
                    <tr key={v.id} className="clickable" onClick={() => nav(`/subnets?vlan=${v.id}`)}>
                      <td><i className="dot" style={{ background: v.color || "#64748b" }} /> {v.name}</td>
                      <td className="mono">{v.vid}</td>
                      <td><Link to={`/subnets?vlan=${v.id}`} className="link">сети VLAN →</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {res.ips.length > 0 && (
            <div className="card">
              <div className="card-title">IP-адреса ({res.ips.length})</div>
              <table className="table">
                <thead><tr><th>IP</th><th>Hostname</th><th>Ответственный</th><th>Сеть</th><th></th></tr></thead>
                <tbody>
                  {res.ips.map((i) => (
                    <tr key={i.ip} className="clickable" onClick={() => nav(`/subnets/${i.subnet_id}?ip=${encodeURIComponent(i.ip)}`)}>
                      <td className="mono">{i.ip}</td>
                      <td>{i.hostname || <span className="muted">—</span>}</td>
                      <td>{i.owner || <span className="muted">—</span>}</td>
                      <td className="muted small">{i.subnet_name} <span className="mono">({i.cidr})</span></td>
                      <td><Link to={`/subnets/${i.subnet_id}?ip=${encodeURIComponent(i.ip)}`} className="link">открыть →</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {total === 0 && (
            <div className="card">
              <div className="muted">Ничего не найдено по «{q.trim()}». Попробуйте CIDR, VLAN, IP, hostname или владельца.</div>
            </div>
          )}
        </>
      )}

      {q.trim().length < 2 && (
        <div className="card">
          <div className="muted">Введите значение и нажмите «Искать» — здесь появится страница с результатами.</div>
        </div>
      )}
    </div>
  );
}
