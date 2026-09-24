import { useMemo, useState } from "react";

export interface SortState {
  key: string | null;
  dir: 1 | -1;
  toggle: (k: string) => void;
}

// сортировка таблицы: клик по заголовку переключает asc/desc (null = исходный порядок)
export function useSort<T>(rows: T[], get: (row: T, key: string) => unknown): { sorted: T[]; sort: SortState } {
  const [key, setKey] = useState<string | null>(null);
  const [dir, setDir] = useState<1 | -1>(1);
  const toggle = (k: string) => {
    if (key === k) setDir((d) => (d === 1 ? -1 : 1));
    else {
      setKey(k);
      setDir(1);
    }
  };
  const sorted = useMemo(() => {
    if (!key) return rows;
    return [...rows].sort((a, b) => {
      const va = get(a, key);
      const vb = get(b, key);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb), "ru", { numeric: true }) * dir;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, key, dir]);
  return { sorted, sort: { key, dir, toggle } };
}

export function Th({ label, k, sort, className = "" }: { label: string; k: string; sort: SortState; className?: string }) {
  const active = sort.key === k;
  return (
    <th className={`sortable ${active ? "active" : ""} ${className}`} onClick={() => sort.toggle(k)} title="Сортировка: клик — по возрастанию/убыванию">
      {label}
      <span className="sort-ind">{active ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
    </th>
  );
}
