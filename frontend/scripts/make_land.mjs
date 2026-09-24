// land-110m TopoJSON -> SVG path (equirectangular "degree space": x=lng+180, y=90-lat, 360x180)
import { readFileSync, writeFileSync } from "node:fs";

const topo = JSON.parse(readFileSync("/tmp/land110.json", "utf8"));
// transform в world-atlas: {scale: [sx, sy], translate: [tx, ty]}
const t = topo.transform;
const [sx, sy] = t.scale, [tx, ty] = t.translate;
const arcs = topo.arcs.map((arc) => {
  let x = 0, y = 0;
  return arc.map(([dx, dy]) => {
    x += dx; y += dy;
    return [x * sx + tx, y * sy + ty]; // [lng, lat]
  });
});
const geom = topo.objects.land.geometries[0];

function ringPath(arcIdxs) {
  const pts = [];
  for (const idx of arcIdxs) {
    const a = idx < 0 ? arcs[(~idx)].slice().reverse() : arcs[idx];
    for (const [lng, lat] of a) {
      if (pts.length && Math.abs(pts[pts.length - 1][0] - lng) < 1e-9 && Math.abs(pts[pts.length - 1][1] - lat) < 1e-9) continue;
      pts.push([lng + 180, 90 - lat]);
    }
  }
  if (!pts.length) return "";
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) d += `L${pts[i][0].toFixed(1)} ${pts[i][1].toFixed(1)}`;
  return d + "Z";
}

const polys = geom.type === "Polygon" ? [geom.arcs] : geom.arcs;
const ds = [];
let totalPts = 0;
for (const poly of polys) {
  let d = "";
  for (const ring of poly) {
    d += ringPath(ring);
  }
  if (d) { ds.push(d); totalPts += (d.match(/L/g) || []).length + 1; }
}
console.log("polygons:", ds.length, "points:", totalPts, "bytes:", ds.join("").length);
const out = `// Генератор: scripts/make_land.mjs (world-atlas land-110m, Natural Earth 1:110m).
// Пространство: equirectangular, x = lng+180, y = 90-lat; мировой холст 360x180.
export const LAND_PATHS: string[] = [
${ds.map((d) => `  ${JSON.stringify(d)},`).join("\n")}
];
`;
writeFileSync("/home/user/ipam/frontend/src/data/world-land.ts", out);
console.log("written, file size:", out.length);
