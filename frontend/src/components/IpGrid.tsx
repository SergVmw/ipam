import { useState } from "react";
import type { Block, Ip } from "../types";
import { displayState, timeAgo } from "../util";

const STATE_RU: Record<string, string> = {
  free: "Свободно",
  used: "Занято",
  reserved: "Резерв",
  offline: "Offline",
  cond_free: "Усл. осв.",
};

const octet = (ip: string) => ip.split(".").pop() || "";

function tip(ip: Ip): string {
  const ds = displayState(ip);
  const lines = [ip.ip, STATE_RU[ds] || ds];
  if (ip.state === "offline" && ip.last_seen) lines.push("последний ответ был: " + timeAgo(ip.last_seen));
  if (ip.hostname) lines.push(ip.hostname + (ip.hostname_manual ? " (ручной)" : " (DNS)"));
  if (ip.mac) lines.push("MAC " + ip.mac);
  if (ip.owner) lines.push("Ответственный: " + ip.owner);
  if (ip.note) lines.push(ip.note);
  return lines.join("\n");
}

export default function IpGrid({
  ips,
  onPick,
  highlight,
}: {
  ips: Ip[];
  onPick: (ip: Ip) => void;
  highlight?: Set<string>;
}) {
  const [hover, setHover] = useState<Ip | null>(null);
  return (
    <div className="grid-wrap">
      <div className="ipgrid">
        {ips.map((ip) => (
          <div
            key={ip.ip}
            className={
              "cell " + displayState(ip) +
              (ip.is_gateway ? " gw" : "") +
              (ip.in_dhcp ? " dhcp" : "") +
              (highlight && !highlight.has(ip.ip) ? " dim" : "")
            }
            title={tip(ip)}
            onClick={() => onPick(ip)}
            onMouseEnter={() => setHover(ip)}
            onMouseLeave={() => setHover(null)}
          >
            {octet(ip.ip)}
          </div>
        ))}
      </div>
      <div className="grid-tip">{hover ? tip(hover).split("\n").join(" · ") : " "}</div>
    </div>
  );
}

function blockColor(pct: number): string {
  const h = Math.max(0, 145 - 145 * pct / 100);
  return `hsl(${h}, 65%, ${20 + 20 * pct / 100}%)`;
}

export function BlockGrid({ blocks, onPick }: { blocks: Block[]; onPick: (b: Block) => void }) {
  return (
    <div className="ipgrid blocks">
      {blocks.map((b) => (
        <div
          key={b.cidr}
          className="cell block"
          style={{ background: blockColor(b.pct) }}
          title={`${b.cidr}\nзанято ${b.used} · резерв ${b.reserved} · free ${b.free} / ${b.total} (${b.pct}%)`}
          onClick={() => onPick(b)}
        >
          {b.cidr.split(".")[3]}
        </div>
      ))}
    </div>
  );
}
