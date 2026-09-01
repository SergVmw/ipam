import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Overview, OverviewItem } from "../types";
import { isG24 } from "../types";
import { G24_GROUP_MIN_PREFIX, buildG24Items, fmtShort, pctColor, subnetsWord } from "../util";

function Tile({ s, subN }: { s: OverviewItem; subN: number }) {
  const nav = useNavigate();
  const occupied = s.used + s.reserved;
  const grp = isG24(s);
  return (
    <Link to={grp ? `/subnets?g24=${s.g24}` : `/subnets/${s.id}`} className="tile"
      title={grp ? s.subnets.map((x) => x.cidr).join("\n") : s.cidr}>
      <div className="tile-top">
        <span className="tile-name">{grp ? `${s.g24}/24` : s.name}</span>
        {grp ? <span className="badge tile-g24-badge">{s.subnets.length} сет.</span>
          : subN > 0 && (
            <span className="tile-subnets" title="Сети, лежащие внутри"
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); nav(`/subnets?inside=${encodeURIComponent(s.cidr)}`); }}>
              {subN} {subnetsWord(subN)}
            </span>
          )}
        <span className="tile-pct" style={{ color: pctColor(s.pct) }}>{s.pct}%</span>
      </div>
      <div className="tile-cidr mono">
        {grp ? `объединено ${s.subnets.length} подсети /${G24_GROUP_MIN_PREFIX} и мельче` : s.cidr}
        {s.tags.length > 0 && <span className="tile-tags">{s.tags.map((t) => <span key={t} className="chip">{t}</span>)}</span>}
      </div>
      <div className="bar">
        <div className="bar-fill" style={{ width: `${s.pct}%`, background: pctColor(s.pct) }} />
      </div>
      <div className="tile-meta">
        <span>занято {occupied} из {s.total}</span>
        <span className={s.last_error ? "bad" : "muted"}>
          {s.last_scan_at ? (s.last_error ? `⚠ скан ${fmtShort(s.last_scan_at)}` : `скан ${fmtShort(s.last_scan_at)}`)
            : grp ? "—" : (s.scan_enabled ? "авто-скан" : "сканов не было")}
        </span>
      </div>
      {s.vlan_name && (
        <div className="tile-vlan" style={{ cursor: "pointer" }} title="Все сети этого VLAN"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); nav(`/subnets?vlan=${s.vlan_id}`); }}>
          <i className="dot" style={{ background: s.vlan_color || "#64748b" }} />
          {s.vlan_name}
        </div>
      )}
    </Link>
  );
}

export default function Overview() {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api<Overview>("/overview").then(setData).catch((e) => setErr(e.message));
  }, []);

  // все сети одним потоком; список группируем по тегам (сеть с несколькими
  // тегами попадёт в каждый соответствующий раздел; без тегов — свой раздел)
  const rawSubnets = useMemo(
    () => (data ? [...data.vlans.flatMap((v) => v.subnets), ...data.unassigned] : []),
    [data],
  );

  // вложенность (master + подсети): подсети, лежащие внутри другой сети, не
  // выводятся поодиночке — у «родителя» под именем счётчик «N подсетей»;
  // непокрытые мелкие (мельче /24) — блок /24
  const { items: allItems, subCount } = useMemo(() => buildG24Items(rawSubnets), [rawSubnets]);

  // поиск по странице: имя, CIDR, gateway, VLAN, теги — без Ctrl+F
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return allItems;
    return allItems.filter(
      (x) =>
        x.name.toLowerCase().includes(s) ||
        x.cidr.toLowerCase().includes(s) ||
        (x.gateway || "").toLowerCase().includes(s) ||
        (x.vlan_name || "").toLowerCase().includes(s) ||
        (x.tags || []).some((t) => t.toLowerCase().includes(s)),
    );
  }, [allItems, q]);

  const tagSections = useMemo(() => {
    const byTag = new Map<string, OverviewItem[]>();
    const untagged: OverviewItem[] = [];
    for (const s of filtered) {
      if (!s.tags || s.tags.length === 0) {
        untagged.push(s);
        continue;
      }
      for (const t of s.tags) {
        if (!byTag.has(t)) byTag.set(t, []);
        byTag.get(t)!.push(s);
      }
    }
    const tags = [...byTag.keys()].sort((a, b) => a.localeCompare(b, "ru"));
    return { tags, byTag, untagged };
  }, [filtered]);

  if (err) return <div className="page"><div className="error">{err}</div></div>;
  if (!data) return <div className="page">Загрузка…</div>;

  const itemKey = (x: OverviewItem) => (isG24(x) ? "g" + x.g24 : "s" + x.id);
  const tiles = (list: OverviewItem[]) => (
    <div className="tiles">{list.map((s) => (
      <Tile key={itemKey(s)} s={s} subN={isG24(s) ? 0 : (subCount.get(s.id) ?? 0)} />
    ))}</div>
  );

  // секции: теги (по алфавиту) + «без тегов» (если есть)
  const sections = [
    ...tagSections.tags.map((t) => ({
      key: t,
      label: <span className="chip chip-tag">{t}</span>,
      list: tagSections.byTag.get(t)!,
    })),
    ...(tagSections.untagged.length > 0 ? [{
      key: "__untagged",
      label: <span className="muted">без тегов</span>,
      list: tagSections.untagged,
    }] : []),
  ];

  return (
    <div className="page">
      <div className="page-head">
        <h1>Обзор</h1>
        <span className="muted small">сетей: {data.totals.subnets} · адресов: {data.totals.ips} · заполнено {data.totals.pct}%</span>
        {/* поиск в строке заголовка: вне секций — при динамической фильтрации
            страницы инпут не размонтируется и фокус не теряется */}
        {allItems.length > 0 && (
          <div className="overview-head-search">
            <input
              className="input"
              placeholder="Поиск: имя, CIDR, gateway, VLAN, тег…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {q.trim() !== "" && <span className="muted small">найдено: {filtered.length} из {allItems.length}</span>}
            <button className="btn ghost small" onClick={() => setQ("")}>✕</button>
          </div>
        )}
      </div>

      <div className="cards-row stats">
        <div className="card stat"><div className="stat-n">{data.totals.subnets}</div><div className="muted small">сетей</div></div>
        <div className="card stat"><div className="stat-n">{data.totals.ips}</div><div className="muted small">IP-адресов</div></div>
        <div className="card stat"><div className="stat-n">{data.totals.used}</div><div className="muted small">занято / резерв</div></div>
        <div className="card stat">
          <div className="stat-n" style={{ color: pctColor(data.totals.pct) }}>{data.totals.pct}%</div>
          <div className="muted small">заполнено</div>
        </div>
      </div>

      {allItems.length === 0 ? (
        <div className="card muted">Сетей пока нет — добавьте первую на вкладке «Сети».</div>
      ) : (
        sections.length === 0 ? (
          <div className="tile-section">
            <div className="tile-section-head">
              <span className="muted">Ничего не найдено по «{q.trim()}»</span>
            </div>
          </div>
        ) : (
          <>
            {sections.map((sec) => (
              <div key={sec.key} className="tile-section">
                <div className="tile-section-head">
                  {sec.label}
                  <span className="muted small">{sec.list.length} сет.</span>
                </div>
                {tiles(sec.list)}
              </div>
            ))}
          </>
        )
      )}
    </div>
  );
}
