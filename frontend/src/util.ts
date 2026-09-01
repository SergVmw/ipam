import type { G24Group, OverviewItem, Subnet } from "./types";

// Время в БД хранится в UTC (наивные ISO). UI отображает его со сдвигом из настроек.
let tzOffsetMin = 0;

export function setTzOffset(min: number) {
  tzOffsetMin = min || 0;
}

function toUtcDate(s: string): Date | null {
  const iso = /Z$|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return new Date(d.getTime() + tzOffsetMin * 60000);
}

export function fmt(s: string | null | undefined): string {
  if (!s) return "—";
  const d = toUtcDate(s);
  if (!d) return s;
  return d.toLocaleString("ru-RU", {
    timeZone: "UTC",
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = toUtcDate(s);
  if (!d) return s;
  return d.toLocaleDateString("ru-RU", { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "2-digit" });
}

export function fmtShort(s: string | null | undefined): string {
  if (!s) return "—";
  const d = toUtcDate(s);
  if (!d) return s;
  return d.toLocaleString("ru-RU", { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function pctColor(pct: number): string {
  return pct >= 90 ? "#ef4444" : pct >= 70 ? "#f59e0b" : "#22c55e";
}

// IP, не отвечающий меньше COND_FREE_DAYS дней, — «условно освобождён» (светло-зелёный);
// без ответа ≥ COND_FREE_DAYS дней — отображается как свободный.
// Должно совпадать с COND_FREE_DAYS в backend/app/service.py
export const COND_FREE_DAYS = 3;

export type DisplayState = "free" | "used" | "reserved" | "cond_free";

export function displayState(ip: { state: string; last_seen: string | null }): DisplayState {
  if (ip.state === "offline") {
    if (!ip.last_seen) return "free";
    const days = (Date.now() - new Date(ip.last_seen + "Z").getTime()) / 86400000;
    return days < COND_FREE_DAYS ? "cond_free" : "free";
  }
  return ip.state as DisplayState;
}

function plural(n: number, one: string, few: string, many: string): string {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

// «4 волокна / 1 волокно / 5 волокон»
export function fibersWord(n: number): string {
  return plural(n, "волокно", "волокна", "волокон");
}

// === Группировка сетей по «родителю» /24 (объединение мелких сетей на Обзоре) ===
// Маски, которые объединяются в блок /24: ОТ /30 (P2P-линии /30, /31, /32).
// Сети /8…/29 показываются обычными плитками. Если нужно другое пороговое
// значение — поменяйте одну константу.
export const G24_GROUP_MIN_PREFIX = 30;

export function prefixOf(cidr: string): number {
  const p = parseInt((cidr.split("/")[1] ?? "32"), 10);
  return isNaN(p) ? 32 : p;
}

// «10.0.0.42/26» → «10.0.0.0» (первая /24-группа; для /24 и крупнее — её же сеть)
export function g24Parent(cidr: string): string {
  const ip = (cidr.split("/")[0] || "").split(".");
  if (ip.length !== 4) return cidr;
  return `${ip[0]}.${ip[1]}.${ip[2]}.0`;
}

function sumKey(list: Subnet[], k: "total" | "used" | "free" | "reserved" | "offline" | "cond_free"): number {
  return list.reduce((a, s) => a + ((s as any)[k] || 0), 0);
}

function makeG24Group(g24: string, members: Subnet[]): G24Group {
  const total = sumKey(members, "total");
  const used = sumKey(members, "used");
  const reserved = sumKey(members, "reserved");
  const pct = total ? Math.round(((used + reserved) / total) * 1000) / 10 : 0;
  const tags = [...new Set(members.flatMap((s) => s.tags || []))];
  const sameVlan = members.every((s) => s.vlan_id === members[0].vlan_id);
  const lastScan = members.map((s) => s.last_scan_at).filter(Boolean) as string[];
  const descrs = [...new Set(members.map((s) => (s.descr || "").trim()).filter(Boolean))];
  return {
    kind: "g24",
    g24,
    cidr: g24 + "/24",
    name: g24 + "/24",
    subnets: members,
    total, used, reserved,
    free: sumKey(members, "free"),
    offline: sumKey(members, "offline"),
    cond_free: sumKey(members, "cond_free"),
    pct,
    tags,
    vlan_id: sameVlan ? members[0].vlan_id : null,
    vlan_name: sameVlan ? members[0].vlan_name : null,
    vlan_color: sameVlan ? members[0].vlan_color : null,
    gateway: null,
    descr: descrs.length ? descrs.join("; ") : null,
    last_scan_at: lastScan.sort().pop() || null,
    last_error: members.find((s) => s.last_error)?.last_error || null,
  };
}

function ipToInt(ip: string): number {
  const p = ip.split(".").map((x) => parseInt(x, 10) || 0);
  return ((p[0] * 256 + p[1]) * 256 + p[2]) * 256 + p[3];
}

// строго вложена: innerCidr целиком внутри outerCidr (маска мельче)
export function isInsideCidr(innerCidr: string, outerCidr: string): boolean {
  const [iip, ipfx] = innerCidr.split("/");
  const [oop, opfx] = outerCidr.split("/");
  const ipLen = parseInt(ipfx, 10), opLen = parseInt(opfx, 10);
  if (isNaN(ipLen) || isNaN(opLen) || ipLen <= opLen) return false;
  const mask = opLen === 0 ? 0 : (0xFFFFFFFF << (32 - opLen)) >>> 0;
  return (ipToInt(iip) & mask) === (ipToInt(oop) & mask);
}

/**
 * Объединяет сети с маской ОТ G24_GROUP_MIN_PREFIX (по умолчанию /25…/32 —
 * всё мельче /24) по родителю /24. Блок /24 создаётся, если внутри /24 таких
 * сетей 2+ (тогда на Обзоре они не выводятся поодиночке).
 * Сети крупнее порога — всегда обычными плитками.
 */
export function buildG24Items(subnets: Subnet[]): OverviewItem[] {
  const small = subnets.filter((s) => prefixOf(s.cidr) >= G24_GROUP_MIN_PREFIX);
  const normal = subnets.filter((s) => prefixOf(s.cidr) < G24_GROUP_MIN_PREFIX);
  const byG24 = new Map<string, Subnet[]>();
  for (const s of small) {
    const g = g24Parent(s.cidr);
    if (!byG24.has(g)) byG24.set(g, []);
    byG24.get(g)!.push(s);
  }
  const groups: G24Group[] = [];
  const grouped = new Set<string>();
  for (const [g, members] of byG24) {
    if (members.length >= 2) { grouped.add(g); groups.push(makeG24Group(g, members)); }
  }
  const lone = small.filter((s) => !grouped.has(g24Parent(s.cidr)));
  return [...normal, ...lone, ...groups];
}

export function fmtCapacity(l: { capacity: number | null; fibers: number | null }): string {
  const parts: string[] = [];
  if (l.capacity != null) parts.push(`${l.capacity} Гбит/с`);
  if (l.fibers != null) parts.push(`${l.fibers} ${fibersWord(l.fibers)}`);
  return parts.join(" · ") || "—";
}

// назначение волокон: «LAN: 8 волокон · 10 Гбит/с» (скорость не выводится,
// если не указана или 0)
export function fmtFiberUsageItem(u: { name: string; count: number; speed?: number | null }): string {
  let s = `${u.name}: ${u.count} ${fibersWord(u.count)}`;
  if (u.speed != null && u.speed > 0) s += ` · ${u.speed} Гбит/с`;
  return s;
}

// разбивка волокон по назначениям: «LAN: 8 волокон · 10 Гбит/с, SAN: 4 волокна · 4 Гбит/с»
export function fmtFiberUsage(u: { name: string; count: number; speed?: number | null }[] | null | undefined): string {
  if (!u || u.length === 0) return "";
  return u.map(fmtFiberUsageItem).join(", ");
}

// «последний ответ был: 5 ч назад / 2 дня назад / 3 мес. назад»
export function timeAgo(s: string | null | undefined): string {
  if (!s) return "не было";
  const d = toUtcDate(s);
  if (!d) return "—";
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 90) return `${Math.max(1, Math.round(sec / 60))} ${plural(Math.round(sec / 60), "минуту", "минуты", "минут")} назад`;
  if (sec < 5400) return `${Math.round(sec / 3600)} ${plural(Math.round(sec / 3600), "час", "часа", "часов")} назад`;
  if (sec < 86400 * 30) return `${Math.round(sec / 86400)} ${plural(Math.round(sec / 86400), "день", "дня", "дней")} назад`;
  if (sec < 86400 * 365) return `${Math.round(sec / (86400 * 30))} мес. назад`;
  return `${Math.round(sec / (86400 * 365))} ${plural(Math.round(sec / (86400 * 365)), "год", "года", "лет")} назад`;
}
