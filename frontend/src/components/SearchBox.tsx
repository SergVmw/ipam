import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

interface Results {
  subnets: { id: number; name: string; cidr: string }[];
  vlans: { id: number; vid: number; name: string; color: string | null }[];
  ips: { ip: string; hostname: string | null; owner: string | null; subnet_id: number; subnet_name: string; cidr: string }[];
}

// mode:
//  - "page" (классический, по умолчанию): ввели значение → Enter → страница /search с результатами
//  - "live" (как раньше): панель с результатами прямо при вводе
export default function SearchBox({ mode = "page" }: { mode?: "page" | "live" }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Results | null>(null);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const boxRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (mode !== "live") return;
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [mode]);

  // --- классический: Enter → страница результатов ---
  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = q.trim();
    if (v.length < 2) return;
    nav(`/search?q=${encodeURIComponent(v)}`);
    setQ("");
  };

  // --- live: панель при вводе ---
  const onInput = (value: string) => {
    setQ(value);
    window.clearTimeout(timer.current);
    if (value.trim().length < 2) {
      setRes(null);
      setOpen(false);
      return;
    }
    timer.current = window.setTimeout(async () => {
      try {
        const r = await api<Results>(`/search?q=${encodeURIComponent(value.trim())}`);
        setRes(r);
        setOpen(true);
      } catch {
        setRes(null);
      }
    }, 250);
  };

  const go = (path: string) => {
    setOpen(false);
    setQ("");
    setRes(null);
    nav(path);
  };

  const has = !!res && (res.subnets.length > 0 || res.vlans.length > 0 || res.ips.length > 0);

  if (mode !== "live") {
    return (
      <form className="search-wrap search-form" onSubmit={onSubmit}>
        <input
          className="input"
          placeholder="Поиск: сеть, VLAN, IP… (Enter)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Поиск"
        />
        <button type="submit" className="btn small ghost" title="Показать страницу результатов" disabled={q.trim().length < 2}>🔍</button>
      </form>
    );
  }

  return (
    <div className="search-wrap" ref={boxRef}>
      <input
        className="input"
        placeholder="Поиск: сеть, VLAN, hostname…"
        value={q}
        onChange={(e) => onInput(e.target.value)}
        onFocus={() => { if (res && q.trim().length >= 2) setOpen(true); }}
      />
      {open && res && (
        <div className="search-drop">
          {res.subnets.length > 0 && <div className="search-sec">Сети</div>}
          {res.subnets.map((s) => (
            <div key={"s" + s.id} className="search-item" onClick={() => go(`/subnets/${s.id}`)}>
              <span>{s.name}</span>
              <span className="mono muted small">{s.cidr}</span>
            </div>
          ))}
          {res.vlans.length > 0 && <div className="search-sec">VLAN</div>}
          {res.vlans.map((v) => (
            <div key={"v" + v.id} className="search-item" onClick={() => go(`/subnets?vlan=${v.id}`)}>
              <span><i className="dot" style={{ background: v.color || "#64748b" }} /> {v.name}</span>
              <span className="mono muted small">VLAN {v.vid}</span>
            </div>
          ))}
          {res.ips.length > 0 && <div className="search-sec">IP-адреса</div>}
          {res.ips.map((i) => (
            <div key={i.ip} className="search-item" onClick={() => go(`/subnets/${i.subnet_id}?ip=${encodeURIComponent(i.ip)}`)}>
              <span className="mono">
                {i.ip}
                {i.hostname ? <span className="muted"> {i.hostname}</span> : null}
              </span>
              <span className="muted small">{i.subnet_name}</span>
            </div>
          ))}
          {!has && <div className="search-empty">ничего не найдено</div>}
        </div>
      )}
    </div>
  );
}
