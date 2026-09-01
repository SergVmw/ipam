import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import LocationPicker from "../components/LocationPicker";
import Modal from "../components/Modal";
import { Th, useSort } from "../components/Sort";
import type { Location } from "../types";

export default function Locations() {
  const [items, setItems] = useState<Location[]>([]);
  const [edit, setEdit] = useState<Location | "new" | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");

  const load = () => api<Location[]>("/locations").then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter(
      (l) =>
        l.name.toLowerCase().includes(s) ||
        (l.address || "").toLowerCase().includes(s) ||
        (l.descr || "").toLowerCase().includes(s),
    );
  }, [items, q]);

  const { sorted, sort } = useSort(filtered, (l, k) => {
    if (k === "name") return l.name;
    if (k === "address") return l.address || "";
    if (k === "coords") return (l.lat != null && l.lng != null ? `${l.lat} ${l.lng}` : "~~");
    if (k === "links") return l.links_count ?? 0;
    if (k === "descr") return l.descr || "";
    return l.name;
  });

  return (
    <div className="page">
      <div className="page-head">
        <h1>Местоположения</h1>
        <span className="muted small">точки начала/конца линий связи; одна точка может быть в нескольких линиях</span>
        <button className="btn primary" onClick={() => setEdit("new")}>+ Местоположение</button>
      </div>
      {err && <div className="error">{err}</div>}

      <div className="vlan-search">
        <input className="input" placeholder="Поиск: название, адрес, описание…" value={q} onChange={(e) => setQ(e.target.value)} />
        {q.trim() !== "" && <span className="muted small">найдено: {sorted.length} из {items.length}</span>}
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <Th label="Название" k="name" sort={sort} />
              <Th label="Адрес" k="address" sort={sort} />
              <Th label="Координаты" k="coords" sort={sort} />
              <Th label="Линий" k="links" sort={sort} />
              <Th label="Описание" k="descr" sort={sort} />
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((l) => (
              <tr key={l.id}>
                <td>{l.name}</td>
                <td className="muted">{l.address || "—"}</td>
                <td className="mono">{l.lat != null && l.lng != null ? `${l.lat}, ${l.lng}` : <span className="muted">нет</span>}</td>
                <td>{l.links_count ?? 0}</td>
                <td className="muted">{l.descr || ""}</td>
                <td className="actions-cell">
                  <button className="btn ghost small" onClick={() => setEdit(l)}>изменить</button>
                  <button
                    className="btn ghost small danger"
                    onClick={async () => {
                      if (confirm(`Удалить местоположение «${l.name}»?`)) {
                        try {
                          await api(`/locations/${l.id}`, { method: "DELETE" });
                          setErr("");
                          load();
                        } catch (e: any) {
                          alert(e.message);
                        }
                      }
                    }}
                  >удалить</button>
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={6} className="muted">{items.length === 0 ? "Местоположений пока нет — добавьте первую точку" : "Ничего не найдено"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {edit && <LocationModal location={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function LocationModal({ location, onClose, onSaved }: { location: Location | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: location?.name ?? "",
    address: location?.address ?? "",
    lat: location?.lat != null ? String(location.lat) : "",
    lng: location?.lng != null ? String(location.lng) : "",
    descr: location?.descr ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // координаты для карты: только валидные числа
  const latNum = (() => { const v = Number(form.lat.replace(",", ".")); return form.lat.trim() !== "" && isFinite(v) ? v : null; })();
  const lngNum = (() => { const v = Number(form.lng.replace(",", ".")); return form.lng.trim() !== "" && isFinite(v) ? v : null; })();

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const body = {
        name: form.name.trim(),
        address: form.address.trim() || null,
        lat: form.lat.trim() === "" ? null : Number(form.lat.replace(",", ".")),
        lng: form.lng.trim() === "" ? null : Number(form.lng.replace(",", ".")),
        descr: form.descr.trim() || null,
      };
      if ((body.lat == null) !== (body.lng == null)) setErr("Координаты: нужны обе — широта и долгота");
      else if ((body.lat != null && (isNaN(body.lat) || body.lat < -90 || body.lat > 90)) || (body.lng != null && (isNaN(body.lng) || body.lng < -180 || body.lng > 180))) setErr("Проверьте координаты: широта −90…90, долгота −180…180");
      else {
        if (location) await api(`/locations/${location.id}`, { method: "PUT", body: JSON.stringify(body) });
        else await api("/locations", { method: "POST", body: JSON.stringify(body) });
        onSaved();
      }
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <Modal title={location ? `Местоположение: ${location.name}` : "Новое местоположение"} onClose={onClose}>
      {err && <div className="error">{err}</div>}
      <div className="kv"><span>Название</span>
        <input className="input" value={form.name} placeholder="напр. ЦОД-1, Минск" onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </div>
      <div className="kv"><span>Адрес</span>
        <input className="input" value={form.address} placeholder="улица, дом, корпус…" onChange={(e) => setForm({ ...form, address: e.target.value })} />
      </div>
      <div className="kv"><span>Координаты</span>
        <div className="inline">
          <input className="input mono" value={form.lat} placeholder="широта, напр. 53.9006" onChange={(e) => setForm({ ...form, lat: e.target.value })} />
          <input className="input mono" value={form.lng} placeholder="долгота, напр. 27.5550" onChange={(e) => setForm({ ...form, lng: e.target.value })} />
        </div>
        <span className="muted small">координаты нужны, чтобы точка была видна на карте линий</span>
      </div>
      <div className="kv"><span>Карта объекта</span>
        <LocationPicker
          lat={latNum}
          lng={lngNum}
          onChange={(la, ln) => setForm((f) => ({ ...f, lat: String(la), lng: String(ln) }))}
        />
        <span className="muted small">клик по карте — точка и координаты выставляются автоматически</span>
      </div>
      <div className="kv"><span>Описание</span>
        <input className="input" value={form.descr} onChange={(e) => setForm({ ...form, descr: e.target.value })} />
      </div>
      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy || !form.name.trim()}>{busy ? "…" : "Сохранить"}</button>
        <button className="btn ghost" onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  );
}
