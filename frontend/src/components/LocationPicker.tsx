import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

/**
 * Квадратная OSM-карта в карточке «Местоположение»: маркер в точке объекта,
 * КЛИК по карте — ставить/передвигать точку (координаты подставляются в форму).
 * Тайлы не грузятся (нет интернета) — пометка, координаты вводят вручную.
 */
export default function LocationPicker({
  lat,
  lng,
  onChange,
  size = 340,
}: {
  lat: number | null;
  lng: number | null;
  onChange: (lat: number, lng: number) => void;
  size?: number;
}) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.CircleMarker | null>(null);
  const cbRef = useRef(onChange);
  cbRef.current = onChange;
  const fittedKey = useRef("");
  const [noTiles, setNoTiles] = useState(false);

  useEffect(() => {
    if (!divRef.current || mapRef.current) return;
    // attributionControl выключен — без бейджа/логотипа в правом нижнем углу
    const map = L.map(divRef.current, { zoomControl: true, attributionControl: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 })
      .on("tileload", () => setNoTiles(false))
      .on("tileerror", () => setNoTiles(true))
      .addTo(map);
    // клик по карте → координаты в форму
    map.on("click", (e: L.LeafletMouseEvent) => {
      cbRef.current(Number(e.latlng.lat.toFixed(6)), Number(e.latlng.lng.toFixed(6)));
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markerRef.current = null;
      fittedKey.current = "";
    };
  }, []);

  // маркер в точке + вписать при появлении/смене координат
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (lat == null || lng == null) {
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    if (!markerRef.current) {
      markerRef.current = L.circleMarker([lat, lng], {
        radius: 8, weight: 2.5, color: "#0a0f1c", fillColor: "#38bdf8", fillOpacity: 1,
      }).addTo(map);
    } else {
      markerRef.current.setLatLng([lat, lng]);
    }
    const key = `${lat},${lng}`;
    if (fittedKey.current !== key) {
      fittedKey.current = key;
      map.setView([lat, lng], 14);
    }
  }, [lat, lng]);

  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <div ref={divRef} style={{ position: "absolute", inset: 0, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }} />
      {noTiles && (
        <div style={{
          position: "absolute", top: 8, left: 8, right: 8, zIndex: 1100,
          background: "rgba(10, 15, 28, 0.88)", color: "#f59e0b",
          fontSize: 12, padding: "6px 10px", borderRadius: 6,
        }}>
          Тайлы не грузятся (нет интернета?) — координаты можно ввести вручную
        </div>
      )}
    </div>
  );
}
