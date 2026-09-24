import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { fmtCapacity, fmtFiberUsage, fmtRouteLen, fmtRouteSegs, routeAllPoints } from "../util";
import type { FiberLink, Location } from "../types";

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * Онлайн-карта (тайлы OpenStreetMap — как в phpIPAM). Нужен интернет
 * в браузере пользователя; если тайлы не грузятся (no internet) —
 * onNoInternet(), страница сама предлагает/переключает на встроенную карту.
 */
export default function OsmMap({
  locations,
  links,
  highlightId,
  onLinkClick,
  onNoInternet,
  onLoaded,
}: {
  locations: Location[];
  links: FiberLink[];
  highlightId?: number | null;
  onLinkClick?: (id: number) => void;
  onNoInternet?: () => void;
  onLoaded?: () => void;
}) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const tileLoaded = useRef(false);
  const tileFailed = useRef(false);
  const propsRef = useRef({ locations, links, highlightId, onLinkClick, onNoInternet, onLoaded });
  propsRef.current = { locations, links, highlightId, onLinkClick, onNoInternet, onLoaded };
  const fittedCount = useRef(-1);

  // инициализация один раз
  // инициализация один раз; attributionControl выключен —
  // без бейджа/логотипа в правом нижнем углу
  useEffect(() => {
    if (!divRef.current || mapRef.current) return;
    const map = L.map(divRef.current, { zoomControl: true, attributionControl: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 })
      .on("tileload", () => {
        if (!tileLoaded.current) {
          tileLoaded.current = true;
          propsRef.current.onLoaded?.();
        }
      })
      .on("tileerror", () => {
        // тайлы не грузятся и ни один не загрузился → скорее всего нет интернета
        if (!tileLoaded.current && !tileFailed.current) {
          tileFailed.current = true;
          propsRef.current.onNoInternet?.();
        }
      })
      .addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    redraw();
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      tileLoaded.current = false;
      tileFailed.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // перерисовка точек/линий при смене данных или подсветки
  useEffect(() => { redraw(); }, [locations, links, highlightId]);

  // вписать точки — только когда меняются сами точки (не при подсветке)
  useEffect(() => {
    const n = locations.filter((l) => l.lat != null && l.lng != null).length;
    if (n > 0 && fittedCount.current !== n) {
      fittedCount.current = n;
      const b = L.latLngBounds(locations.filter((l) => l.lat != null && l.lng != null).map((l) => [l.lat!, l.lng!] as [number, number]));
      mapRef.current?.fitBounds(b, { padding: [40, 40], maxZoom: 12 });
    }
  }, [locations]);

  function redraw() {
    const map = mapRef.current;
    const g = layerRef.current;
    const { locations, links, highlightId, onLinkClick } = propsRef.current;
    if (!map || !g) return;
    g.clearLayers();

    for (const l of locations) {
      if (l.lat == null || l.lng == null) continue;
      // промежуточная точка — другим цветом (оранжевая)
      L.circleMarker([l.lat, l.lng], {
        radius: l.is_transit ? 7 : 6, weight: 1.5, color: "#0a0f1c",
        fillColor: l.is_transit ? "#fb923c" : "#38bdf8", fillOpacity: 1,
      })
        .bindTooltip(
          `<b>${esc(l.name)}</b>${l.address ? `<br>${esc(l.address)}` : ""}` +
          (l.is_transit ? `<br><i>промежуточная точка</i>` : ""),
          { sticky: true },
        )
        .addTo(g);
    }

    for (const f of links) {
      // маршрут: А → промежуточные → Б; без координат хотя бы одной точки — не рисуем
      const all = routeAllPoints(f);
      if (all.some((p) => p.lat == null || p.lng == null)) continue;
      const coords: [number, number][] = all.map((p) => [p.lat!, p.lng!] as [number, number]);
      const hl = highlightId === f.id;
      // на светлых OSM-тайлах: активная — ЧЁРНАЯ (зелёный терялся),
      // не активная — тёмно-серый пунктир, выделенная — синяя
      const line = L.polyline(coords, {
        color: hl ? "#2563eb" : f.is_active ? "#111111" : "#475569",
        weight: hl ? 5 : f.is_active ? 4 : 3,
        opacity: 0.9,
        dashArray: f.is_active ? undefined : "8 6",
      });
      const usage = fmtFiberUsage(f.fiber_usage);
      const routeLine = esc(all.map((p) => p.name).join(" &rarr; "));
      const segsLine = fmtRouteSegs(f);
      line.bindTooltip(
        `<b>${esc(f.name)}</b><br>${routeLine}<br>` +
        `${esc(fmtCapacity(f))}${usage ? `<br>${esc(usage)}` : ""}` +
        `<br>Длина: ${esc(fmtRouteLen(f))}${segsLine ? ` <i>(${esc(segsLine)})</i>` : ""}`,
        { sticky: true },
      );
      if (onLinkClick) line.on("click", () => onLinkClick(f.id));
      line.addTo(g);
    }
  }

  return <div className="osm-map" ref={divRef} />;
}
