import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LAND_PATHS } from "../data/world-land";
import { fmtCapacity, fmtFiberUsage, fmtRouteLen, fmtRouteSegs, routeAllPoints, routeSegs } from "../util";
import type { FiberLink, Location } from "../types";

// Equirectangular, «пространство градусов»: x = lng+180, y = 90-lat; мировой холст 360x180.
const px = (lng: number) => lng + 180;
const py = (lat: number) => 90 - lat;

interface View { x: number; y: number; w: number; h: number }
const WORLD: View = { x: 0, y: 0, w: 360, h: 180 };

export default function FiberMap({
  locations,
  links,
  highlightId,
  onLinkClick,
}: {
  locations: Location[];
  links: FiberLink[];
  highlightId?: number | null;
  onLinkClick?: (id: number) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState<View>(WORLD);
  const [fittedEmpty, setFittedEmpty] = useState(false);
  const [fittedPts, setFittedPts] = useState(false);
  const [drag, setDrag] = useState<{ px: number; py: number; view: View } | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; link: FiberLink } | null>(null);

  const pts = useMemo(
    () =>
      locations
        .filter((l) => l.lat != null && l.lng != null)
        .map((l) => ({ id: l.id, name: l.name, x: px(l.lng!), y: py(l.lat!), transit: !!l.is_transit })),
    [locations],
  );

  // линия как цепочка участков: А → v1 → … → vk → Б; каждый участок — лёгкая дуга.
  // Если у любой точки маршрута нет координат — линия на карте не рисуется.
  const segs = useMemo(
    () =>
      links
        .map((l) => {
          const all = routeAllPoints(l);
          if (all.some((p) => p.lat == null || p.lng == null)) return null;
          let prev: [number, number] | null = null;
          let d = "";
          let segIdx = 0;
          for (const p of all) {
            let x = px(p.lng!), y = py(p.lat!);
            if (prev) {
              // через антиподмеридиан — сдвигаем, чтобы дуга не обходила мир
              if (x - prev[0] > 180) x -= 360;
              if (prev[0] - x > 180) x += 360;
            }
            if (prev) {
              const [ax, ay] = prev;
              const mx = (ax + x) / 2, my = (ay + y) / 2;
              const dx = x - ax, dy = y - ay;
              const dist = Math.hypot(dx, dy) || 1e-9;
              const multi = all.length > 2;
              const sign = (l.id % 2 === 0 ? 1 : -1) * (segIdx % 2 === 0 ? 1 : -1);
              const off = (multi ? Math.min(dist * 0.15, 15) : Math.min(dist * 0.18, 25)) * sign;
              d += ` Q ${mx + (-dy / dist) * off} ${my + (dx / dist) * off} ${x} ${y}`;
              segIdx++;
            } else {
              d = `M ${x} ${y}`;
            }
            prev = [x, y];
          }
          return { link: l, d };
        })
        .filter((s): s is { link: FiberLink; d: string } => s != null),
    [links],
  );

  // вписать карту в контейнер (соотношение сторон 1:1 → без «полос»)
  const fit = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ar = el.clientWidth / Math.max(1, el.clientHeight);
    let x: number, y: number, w: number, h: number;
    if (pts.length) {
      const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
      const minX = Math.min(...xs), maxX = Math.max(...xs);
      const minY = Math.min(...ys), maxY = Math.max(...ys);
      w = Math.max(maxX - minX, 0.25) + 1.2;
      h = Math.max(maxY - minY, 0.25) + 1.2;
      x = (minX + maxX) / 2 - w / 2;
      y = (minY + maxY) / 2 - h / 2;
    } else {
      ({ x, y, w, h } = WORLD);
    }
    // подгонка под соотношение сторон контейнера (box шире контейнера → растим высоту,
    // box выше → ширину; центр не смещаем)
    if (w / h < ar) { const nw = h * ar; x -= (nw - w) / 2; w = nw; }
    else { const nh = w / ar; y -= (nh - h) / 2; h = nh; }
    setView({ x, y, w, h });
  }, [pts]);

  // первый показ: мир (пока точек нет) и авто-вписывание, когда точки загрузятся
  useEffect(() => {
    if (!fittedEmpty) { fit(); setFittedEmpty(true); }
  }, [fit, fittedEmpty]);
  useEffect(() => {
    if (pts.length && !fittedPts) { fit(); setFittedPts(true); }
  }, [pts, fit, fittedPts]);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const ar = el.clientWidth / Math.max(1, el.clientHeight);
      setView((v) => {
        const cx = v.x + v.w / 2, cy = v.y + v.h / 2;
        let { w, h } = v;
        if (w / h < ar) w = h * ar; else h = w / ar;
        return { x: cx - w / 2, y: cy - h / 2, w, h };
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const zoomAt = (factor: number, cxFrac = 0.5, cyFrac = 0.5) => {
    const el = wrapRef.current;
    if (!el) return;
    const ar = el.clientWidth / Math.max(1, el.clientHeight);
    setView((v) => {
      const mx = v.x + v.w * cxFrac, my = v.y + v.h * cyFrac;
      let w = Math.min(360 * 3, Math.max(0.02, v.w * factor));
      let h = w / ar;
      return { x: mx - v.w * cxFrac, y: my - v.h * cyFrac, w, h };
    });
  };

  const onWheel = (e: React.WheelEvent) => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    zoomAt(e.deltaY > 0 ? 1.25 : 0.8, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDrag({ px: e.clientX, py: e.clientY, view });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const el = wrapRef.current;
    if (!el) return;
    const k = drag.view.w / el.clientWidth;
    setView((v) => ({ ...v, x: drag.view.x - (e.clientX - drag.px) * k, y: drag.view.y - (e.clientY - drag.py) * k }));
  };
  const onPointerUp = () => setDrag(null);

  const showTip = (e: React.MouseEvent, link: FiberLink) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return;
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, link });
  };

  const rPt = view.h * 0.006;   // радиус точки — константный в экранных пикселях
  const fLabel = view.h * 0.019; // шрифт подписей
  const r = wrapRef.current?.getBoundingClientRect();

  return (
    <div className="fiber-map-wrap" ref={wrapRef}>
      <svg
        ref={svgRef}
        className="fiber-map"
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <rect x={view.x - view.w} y={view.y - view.h} width={view.w * 3} height={view.h * 3} fill="#0c1424" />
        {/* суша (Natural Earth 110m) — три копии по X для панорамирования за края */}
        {[-360, 0, 360].map((off) =>
          LAND_PATHS.map((d, i) => (
            <path key={off + "-" + i} d={d} transform={`translate(${off} 0)`} className="fiber-land" />
          )),
        )}
        {segs.map((s) => (
          <g key={s.link.id} onClick={() => onLinkClick?.(s.link.id)}
            onMouseMove={(e) => showTip(e, s.link)} onMouseLeave={() => setTip(null)}
            style={{ cursor: onLinkClick ? "pointer" : "default" }}>
            <path d={s.d} className="fiber-line-hit" />
            <path
              d={s.d}
              className={
                "fiber-line" +
                (s.link.is_active ? " active" : " inactive") +
                (highlightId === s.link.id ? " hl" : "")
              }
            />
          </g>
        ))}
        {pts.map((p) => (
          <g key={p.id}>
            <circle cx={p.x} cy={p.y} r={p.transit ? rPt * 1.25 : rPt} className={"fiber-pt" + (p.transit ? " transit" : "")} />
            <text x={p.x + rPt * 1.6} y={p.y - rPt * 1.1} className={"fiber-lbl" + (p.transit ? " transit" : "")} fontSize={fLabel} strokeWidth={fLabel * 0.3}>{p.name}</text>
          </g>
        ))}
      </svg>

      <div className="fiber-map-zoom">
        <button className="btn ghost small" onClick={() => zoomAt(0.6)} title="Приблизить">+</button>
        <button className="btn ghost small" onClick={() => zoomAt(1.6)} title="Отдалить">−</button>
        <button className="btn ghost small" onClick={fit} title="Вписать все точки">⌂</button>
      </div>
      <div className="fiber-map-legend">
        <span><i className="fiber-legend-line active" /> активна</span>
        <span><i className="fiber-legend-line inactive" /> не активна</span>
        <span><i className="fiber-legend-pt" /> местоположение</span>
        <span><i className="fiber-legend-pt transit" /> промежуточная точка</span>
      </div>

      {pts.length === 0 && (
        <div className="fiber-map-empty">
          Нет точек с координатами.<br />
          Добавьте координаты в «Местоположения» — линии появятся на карте.
        </div>
      )}

      {tip && (
        <div className="fiber-map-tip" style={{ left: Math.min(tip.x + 12, (r?.width ?? 400) - 260), top: tip.y + 12 }}>
          <b>{tip.link.name}</b>
          <div>{routeAllPoints(tip.link).map((p) => p.name).join(" → ")}</div>
          <div>{fmtCapacity(tip.link)}</div>
          {tip.link.fiber_usage && tip.link.fiber_usage.length > 0 && (
            <div className="muted">{fmtFiberUsage(tip.link.fiber_usage)}</div>
          )}
          <div className="muted">
            Длина: {fmtRouteLen(tip.link)}
            {fmtRouteSegs(tip.link) && <> <span title={routeSegs(tip.link).join(" + ")}>({fmtRouteSegs(tip.link)})</span></>}
          </div>
        </div>
      )}
    </div>
  );
}
