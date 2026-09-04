// Демо-данные Rack Topology (контракт версии 1) — используются на странице
// «Стойки» для предпросмотра, когда внешний источник не настроен/недоступен.
export const DEMO_TOPOLOGY: any = {
  api: "rack-topology",
  schemaVersion: 1,
  generatedAt: "2026-09-04T08:15:00Z",
  inventoryRevision: "3f2c9b41d0a1e7b6c8d4f5a69e3b0c2d7a8f1e4b6c9d2e5f8a0b1c3d4e5f6a7",
  requestedSite: "",
  counts: { total: 27, up: 19, down: 2, partial: 3, unknown: 3, blade: 8, rear: 3 },
  locations: [
    {
      site: "Охотская",
      units: 42,
      rows: [
        { label: "Ряд A", racks: ["101", "102", "103"] },
        { label: "Ряд B", racks: ["201", "202"] },
      ],
    },
    {
      site: "БЦ Маяк",
      units: 42,
      rows: [
        { label: "Ряд 1", racks: ["M1", "M2"] },
      ],
    },
  ],
  devices: [
    // ---------- Охотская, 101: ядро сети ----------
    { id: "sw-101-1", kind: "rack", site: "Охотская", rack: "101", side: "front", positionType: "u", position: "42", uStart: 42, uEnd: 42, uHeight: 1, host: "охотская-core-1", serviceIp: "10.10.0.1", managementIps: ["10.200.0.1"], model: "MikroTik CCR2216", serial: "CCR-2216-01", status: "up", latencyMs: 0.8, role: "core" },
    { id: "sw-101-2", kind: "rack", site: "Охотская", rack: "101", side: "front", positionType: "u", position: "41", uStart: 41, uEnd: 41, uHeight: 1, host: "охотская-core-2", serviceIp: "10.10.0.2", managementIps: ["10.200.0.2"], model: "MikroTik CCR2216", serial: "CCR-2216-02", status: "up", latencyMs: 0.9, role: "core" },
    { id: "sw-101-3", kind: "rack", site: "Охотская", rack: "101", side: "front", positionType: "u", position: "38-39", uStart: 39, uEnd: 38, uHeight: 2, host: "охотская-dist-1", serviceIp: "10.10.0.5", managementIps: ["10.200.0.5"], model: "MikroTik CRS354", serial: "CRS-354-01", status: "partial", latencyMs: 2.1, role: "distribution" },
    { id: "sw-101-4", kind: "rack", site: "Охотская", rack: "101", side: "front", positionType: "u", position: "36-37", uStart: 37, uEnd: 36, uHeight: 2, host: "охотская-dist-2", serviceIp: "10.10.0.6", managementIps: ["10.200.0.6"], model: "MikroTik CRS354", serial: "CRS-354-02", status: "down", latencyMs: null, role: "distribution" },
    { id: "ups-101", kind: "rack", site: "Охотская", rack: "101", side: "front", positionType: "u", position: "3-5", uStart: 5, uEnd: 3, uHeight: 3, host: "ИБП-101", managementIps: ["10.200.0.50"], model: "APC SRT3000", status: "up", latencyMs: 4.0, role: "ups" },
    // ---------- Охотская, 102: серверный (blade-корзина + обычные) ----------
    { id: "srv-102-1", kind: "rack", site: "Охотская", rack: "102", side: "front", positionType: "u", position: "40-41", uStart: 41, uEnd: 40, uHeight: 2, host: "охотская-web-01", serviceIp: "10.20.0.10", managementIps: ["10.200.0.10"], model: "Dell R650", serial: "R650-001", status: "up", latencyMs: 1.2, role: "web" },
    { id: "srv-102-2", kind: "rack", site: "Охотская", rack: "102", side: "front", positionType: "u", position: "38-39", uStart: 39, uEnd: 38, uHeight: 2, host: "охотская-app-01", serviceIp: "10.20.0.11", managementIps: ["10.200.0.11"], model: "Dell R650", serial: "R650-002", status: "up", latencyMs: 1.4, role: "app" },
    // Blade-корзина (полноразмерный chassis-блок): сам устройство kind=rack с bladeKey
    { id: "ch-102-1", kind: "rack", site: "Охотская", rack: "102", side: "front", positionType: "u", position: "28-35", uStart: 35, uEnd: 28, uHeight: 8, host: "охотская-m1000e", managementIps: ["10.200.0.20"], model: "Dell PowerEdge M1000e", serial: "M1K-001", status: "up", bladeKey: "ch102", bladeAlias: "Корзина M1000e", slotCount: 16, chassisUrl: "https://охотская-m1000e.example.local", role: "blade-chassis" },
    { id: "bl-102-01", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 1, host: "охотская-bl-01", serviceIp: "10.20.0.21", managementIps: ["10.200.0.21"], model: "M620", status: "up", latencyMs: 0.7, role: "web" },
    { id: "bl-102-02", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 2, host: "охотская-bl-02", serviceIp: "10.20.0.22", managementIps: ["10.200.0.22"], model: "M620", status: "up", latencyMs: 0.6, role: "web" },
    { id: "bl-102-03", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 3, host: "охотская-bl-03", serviceIp: "10.20.0.23", managementIps: ["10.200.0.23"], model: "M620", status: "down", latencyMs: null, role: "app" },
    { id: "bl-102-04", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 4, host: "охотская-bl-04", serviceIp: "10.20.0.24", managementIps: ["10.200.0.24"], model: "M620", status: "partial", latencyMs: 3.4, role: "app" },
    { id: "bl-102-05", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 5, host: "охотская-bl-05", serviceIp: "10.20.0.25", managementIps: ["10.200.0.25"], model: "M630", status: "unknown", role: "db" },
    { id: "bl-102-06", kind: "blade", site: "Охотская", rack: "102", positionType: "bay", bladeKey: "ch102", bay: 6, host: "охотская-bl-06", serviceIp: "10.20.0.26", managementIps: ["10.200.0.26"], model: "M630", status: "up", latencyMs: 0.8, role: "db" },
    { id: "re-102-1", kind: "rear", site: "Охотская", rack: "102", positionType: "rear", bladeKey: "ch102", rearPosition: 1, host: "KVM rear-1", status: "up", role: "rear" },
    { id: "re-102-2", kind: "rear", site: "Охотская", rack: "102", positionType: "rear", bladeKey: "ch102", rearPosition: 2, host: "Dell M1000e I/O-A", model: "M-IO", status: "partial", role: "rear" },
    { id: "re-102-3", kind: "rear", site: "Охотская", rack: "102", positionType: "rear", bladeKey: "ch102", rearPosition: 3, host: "Dell M1000e I/O-B", model: "M-IO", status: "up", role: "rear" },
    // ---------- Охотская, 103: хранилище (пустой верх + дисковые полки) ----------
    { id: "st-103-1", kind: "rack", site: "Охотская", rack: "103", side: "front", positionType: "u", position: "40-42", uStart: 42, uEnd: 40, uHeight: 3, host: "охотская-nas-1", managementIps: ["10.200.0.30"], model: "Synology RS4021xs+", status: "up", role: "storage" },
    { id: "st-103-2", kind: "rack", site: "Охотская", rack: "103", side: "front", positionType: "u", position: "36-39", uStart: 39, uEnd: 36, uHeight: 4, host: "охотская-nas-2", managementIps: ["10.200.0.31"], model: "Synology RS4021xs+", status: "up", role: "storage" },
    // ---------- Охотская, 201: пустой частично ----------
    { id: "sw-201-1", kind: "rack", site: "Охотская", rack: "201", side: "front", positionType: "u", position: "42", uStart: 42, uEnd: 42, uHeight: 1, host: "охотская-edge-1", serviceIp: "10.10.0.250", managementIps: ["10.200.0.60"], model: "MikroTik CCR1036", status: "up", role: "edge" },
    { id: "fw-201-1", kind: "rack", site: "Охотская", rack: "201", side: "front", positionType: "u", position: "40-41", uStart: 41, uEnd: 40, uHeight: 2, host: "охотская-fw-1", managementIps: ["10.200.0.61"], model: "UserGate F8000", status: "up", role: "firewall" },
    { id: "srv-201-1", kind: "rack", site: "Охотская", rack: "201", side: "front", positionType: "u", position: "2-3", uStart: 3, uEnd: 2, uHeight: 2, host: "охотская-backup", serviceIp: "10.20.0.99", managementIps: ["10.200.0.62"], model: "Dell R750", status: "up", role: "backup" },
    // ---------- Охотская, 202: только ИБП + патч ----------
    { id: "pdu-202-1", kind: "rack", site: "Охотская", rack: "202", side: "front", positionType: "u", position: "42", uStart: 42, uEnd: 42, uHeight: 1, host: "PDU-A", status: "up", role: "pdu" },
    { id: "ups-202", kind: "rack", site: "Охотская", rack: "202", side: "front", positionType: "u", position: "1-3", uStart: 3, uEnd: 1, uHeight: 3, host: "ИБП-202", status: "up", role: "ups" },
    // ---------- БЦ Маяк ----------
    { id: "m1-1", kind: "rack", site: "БЦ Маяк", rack: "M1", side: "front", positionType: "u", position: "42-41", uStart: 42, uEnd: 41, uHeight: 2, host: "маяк-core-1", serviceIp: "172.16.0.1", managementIps: ["10.200.1.1"], model: "MikroTik CCR2004", status: "up", role: "core" },
    { id: "m1-2", kind: "rack", site: "БЦ Маяк", rack: "M1", side: "front", positionType: "u", position: "38-39", uStart: 39, uEnd: 38, uHeight: 2, host: "маяк-srv-01", serviceIp: "172.16.0.10", managementIps: ["10.200.1.10"], model: "HP DL380", status: "partial", role: "app" },
    { id: "m2-1", kind: "rack", site: "БЦ Маяк", rack: "M2", side: "front", positionType: "u", position: "40-42", uStart: 42, uEnd: 40, uHeight: 3, host: "маяк-fw-1", managementIps: ["10.200.1.20"], model: "UserGate F5000", status: "down", role: "firewall" },
  ],
};
