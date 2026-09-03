export interface Vlan {
  id: number;
  vid: number;
  name: string;
  color: string | null;
  descr: string | null;
  tags?: string[];
  subnets_count?: number;
}

export interface Subnet {
  id: number;
  cidr: string;
  name: string;
  vlan_id: number | null;
  vlan_name?: string | null;
  vlan_color?: string | null;
  gateway: string | null;
  dhcp_start: string | null;
  dhcp_end: string | null;
  scan_enabled: boolean;
  scan_interval_s: number | null;
  scan_method: string | null;
  tags: string[];
  next_scan_at: string | null;
  descr: string | null;
  total: number;
  used: number;
  free: number;
  reserved: number;
  offline: number;
  cond_free?: number;
  pct: number;
  last_scan_at?: string | null;
  last_error?: string | null;
  busy?: boolean;
  // индикатор доступности: «Я» — последний скан ядром (ok=живь ≥1, off=живых 0,
  // err=ошибка скана, none=не сканировалось); «А» — свежие отчёты агентов
  // (ok=живь ≥1, no=отчёт есть но живых 0, off=агент молчит, none=агента нет)
  core_reach?: { state: "ok" | "off" | "err" | "none"; alive: number | null; at: string | null; error: string | null; total: number };
  agent_reach?: { state: "ok" | "no" | "off" | "none"; at: string | null; hosts: number | null; agent: string | null };
}

export interface Ip {
  ip: string;
  state: "free" | "used" | "reserved" | "offline" | string;
  hostname: string | null;
  hostname_manual: boolean;
  mac: string | null;
  mac_vendor: string | null;
  owner: string | null;
  note: string | null;
  is_gateway: boolean;
  in_dhcp: boolean;
  first_seen: string | null;
  last_seen: string | null;
}

export interface Block {
  cidr: string;
  free: number;
  used: number;
  reserved: number;
  offline: number;
  total: number;
  pct: number;
}

export interface Location {
  id: number;
  name: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  is_transit: boolean; // промежуточная точка (реле/пересадка) — на карте другим цветом
  descr: string | null;
  links_count?: number;
}

// Маршрут линии с промежуточными точками:
// via — промежуточные местоположения по порядку (между точкой А и точкой Б);
// segs — длина каждого участка, км: [А→v1, v1→v2, …, vk→B] (длина = len(via)+1); null = не введён
export interface LinkRoute {
  via: Location[];
  segs: (number | null)[];
}

export interface FiberLink {
  id: number;
  name: string;
  a: Location;
  b: Location;
  capacity: number | null;
  fibers: number | null;
  length: number | null; // длина трассы, км (если нет промежуточных точек)
  route: LinkRoute | null;
  // speed_mode: "all" = «на все волокна» (все волокна суммарно), "pair" = «на пару волокон» (пара волокон),
  // null/отсутствует = прочерки (----; скорость не установлена, на страницу не выводится)
  // extra — дополнительное примечание по назначению (выводится после скорости)
  fiber_usage: { name: string; count: number; speed: number | null; speed_mode?: "all" | "pair" | null; extra?: string | null }[] | null;
  is_active: boolean;
  descr: string | null;
}

// Группа «осколков» мельче /24, объединённая по родителю /24 (для Обзора)
export interface G24Group {
  kind: "g24";
  g24: string;   // "10.0.0.0"
  cidr: string;  // "10.0.0.0/24"
  name: string;  // отображаемое имя (по умолчанию cidr)
  subnets: Subnet[];
  total: number;
  used: number;
  free: number;
  reserved: number;
  offline: number;
  cond_free: number;
  pct: number;
  tags: string[];
  vlan_id: number | null;
  vlan_name: string | null;
  vlan_color: string | null;
  gateway: string | null;
  descr: string | null;
  last_scan_at: string | null;
  last_error: string | null;
}

export type OverviewItem = Subnet | G24Group;

export function isG24(x: OverviewItem): x is G24Group {
  return (x as G24Group).kind === "g24";
}

export interface ScanRun {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  alive: number | null;
  new_ips: number | null;
  freed_ips: number | null;
  error: string | null;
}

export interface EventOut {
  id: number;
  ip: string | null;
  subnet_id: number | null;
  type: string;
  detail: any;
  at: string | null;
}

// Лог сканера (in-memory, удержание 1 час): диагностика fping/nmap/TCP-пробы
export interface ScanLogEntry {
  at: string;
  subnet_id: number;
  cidr: string;
  name: string;
  trigger: string; // manual | schedule
  method: string | null;          // разрешённый метод: fping | nmap | tcp
  method_requested: string | null; // запрошенный (auto/fping/nmap/tcp)
  params: { timeout_ms: number; rate: number; ports: number[] | null };
  hosts_total: number;
  alive: number;
  new: number;
  freed: number;
  duration_ms: number | null;
  exit_code: number | null;
  stderr: string | null;
  alive_ips: string[] | null;
  error: string | null;
  counts: { free: number; used: number; reserved: number; offline: number };
  dns: DnsScanStats | null;   // какой DNS-сервер отвечал на PTR (или не отвечал)
}

// статистика DNS по одному серверу за скан
export interface DnsServerStat {
  server: string;      // host:port
  port: number;
  queries: number;     // сколько запросов отправлено
  answered: number;    // сколько DNS-ответов получено (rcode любой)
  ok: number;          // ответил + в ответе была PTR-запись (ответ принят)
  empty: number;       // ответил, но записи нет (NXDOMAIN / NOERROR без ответа)
  refused: number;     // ответил REFUSED
  servfail: number;    // ответил SERVFAIL
  timeouts: number;    // НЕ ответил за отведённое время
  errors: number;      // прочие ошибки (сеть и т.п.)
  rtt_avg_ms: number | null;
}

// агрегированная статистика DNS-резолва за скан
export interface DnsScanStats {
  enabled: boolean;                 // PTR-резолв включён в Настройках?
  mode: "custom" | "system";        // пользовательские серверы или системный резолвер
  configured: string[];             // настроенные серверы (host:port)
  attempted: number;                // сколько IP запрошено
  resolved_by_dns: number;          // разрешено DNS-серверами
  resolved_by_fallback: number;     // разрешено системным резолвером (/etc/hosts)
  unresolved: number;               // так и не нашли hostname
  by_server: DnsServerStat[];
}

export interface UsagePoint {
  at: string;
  pct: number;
  used: number;
  free: number;
  reserved: number;
  offline: number;
}

export interface Usage {
  current: {
    total: number;
    used: number;
    free: number;
    reserved: number;
    offline: number;
    cond_free: number;
    pct: number;
  };
  series: UsagePoint[];
}

export interface Overview {
  vlans: (Vlan & { subnets: Subnet[] })[];
  unassigned: Subnet[];
  totals: { subnets: number; ips: number; used: number; pct: number };
}

export interface UiLink {
  title: string;
  url: string;
  new_window: boolean;
}

export interface AppSettings {
  dns_servers: string;
  resolve_dns: boolean;
  scan_method: string;
  scan_rate: number;
  scan_timeout_ms: number;
  tz_offset_min: number;
  ui_logo: string;
  copyright: string;
  admin_email: string;
  ui_links: UiLink[];
  show_no_dns: boolean;
  search_mode: "page" | "live";
  org_name: string;
  agent_report_interval_min?: number;
  ldap_enabled: boolean;
  ldap_url: string;
  ldap_base_dn: string;
  ldap_user_dn_template: string;
  ldap_search_filter: string;
  ldap_bind_dn: string;
  ldap_bind_password: string;
  ldap_default_role: string;
  ldap_allow_list: string;
  mail_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  smtp_starttls: boolean;
  mail_from: string;
  mail_to: string;
}

export interface UserOut {
  id: number;
  username: string;
  role: string;
  provider: string;
  display_name: string | null;
  created_at: string | null;
}

export interface AgentOut {
  id: number;
  name: string;
  key: string;
  subnet_ids: number[] | null;
  enabled: boolean;
  last_report_at: string | null;
  last_hosts: number | null;
  descr: string | null;
  created_at: string | null;
  ssh_host: string | null;
  ssh_port: number | null;
  ssh_user: string | null;
  ssh_password: string;
  poll_file: string;
  report_interval_min: number | null;
  last_install_at: string | null;
  install_log: { step: string; ok: boolean; detail: string }[] | null;
}

export interface InstallState {
  state: string;
  steps: { step: string; ok: boolean; detail: string }[];
  finished_at: string | null;
}

export interface AgentReportOut {
  at: string;
  hosts: number;
  applied: number;
  with_mac: number;
}

export interface DocFileOut {
  id: number;
  name: string;
  size: number;
  mime: string | null;
  url: string;
  editable: boolean;
  uploaded_at: string | null;
}

export interface DocPageOut {
  id: number;
  title: string;
  updated_at: string | null;
  updated_by: string | null;
  files: DocFileOut[];
}

export interface DocSectionOut {
  id: number;
  title: string;
  position: number;
  files: DocFileOut[];
  pages: DocPageOut[];
}

export interface DocPageFull {
  id: number;
  title: string;
  body: string;
  section_id: number;
  section_title: string;
  created_at: string | null;
  updated_at: string | null;
  updated_by: string | null;
  files: DocFileOut[];
}
