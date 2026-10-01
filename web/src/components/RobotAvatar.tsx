/**
 * Little factory-worker robots, drawn as SVG. No assets, no WebGL, sharp at any
 * size, and deterministic per agent name so each agent keeps the same face.
 * Palette borrowed from Kenney's Mini Characters colormap (CC0).
 */
import { useMemo } from "react";

const BODIES = ["#ff7e44", "#61cb8b", "#6794d9", "#a878e8", "#ffc044", "#cf534f"];
const ACCENT = "#a0a8c9";

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

export function RobotAvatar({
  name,
  kind,
  status,
  size = 40,
  title,
}: {
  name: string;
  kind?: string;
  status: string;
  size?: number;
  title?: string;
}) {
  const seed = useMemo(() => hash(name || "agent"), [name]);
  const body = BODIES[seed % BODIES.length];
  const face = ["round", "square", "visor"][seed % 3];
  const antennae = (seed >> 2) % 3; // 0 none, 1 one, 2 two
  const working = status === "working";
  const needs = status === "blocked" || status === "needs";
  const idle = status === "idle" || status === "done";
  const eye = needs ? "#cf534f" : "#1b1a17";

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label={title ? `${title} (${status})` : `${name} (${status})`}
      className={`robot robot-${status}`}
    >
      {title && <title>{title}</title>}
      {/* shadow */}
      <ellipse cx="32" cy="58" rx="16" ry="4" fill="#00000022" />
      {/* antennae */}
      {antennae >= 1 && <line x1="24" y1="12" x2="24" y2="6" stroke={ACCENT} strokeWidth="2" />}
      {antennae >= 1 && <circle cx="24" cy="5" r="2.4" fill={needs ? "#cf534f" : "#ffc044"} className="antenna-tip" />}
      {antennae === 2 && <line x1="40" y1="12" x2="40" y2="6" stroke={ACCENT} strokeWidth="2" />}
      {antennae === 2 && <circle cx="40" cy="5" r="2.4" fill={needs ? "#cf534f" : "#61cb8b"} className="antenna-tip" />}
      {/* head */}
      <rect x="18" y="12" width="28" height="20" rx={face === "round" ? 9 : 4} fill={ACCENT} stroke="#1b1a17" strokeWidth="2" />
      {face === "visor" && <rect x="21" y="19" width="22" height="8" rx="3" fill="#1b1a17" />}
      {face !== "visor" && <circle cx="26" cy="22" r="2.6" fill={eye} className="robot-eye" />}
      {face !== "visor" && <circle cx="38" cy="22" r="2.6" fill={eye} className="robot-eye" />}
      {face === "visor" && <circle cx="32" cy="23" r="2" fill={needs ? "#cf534f" : "#61cb8b"} className="robot-eye" />}
      {/* body */}
      <rect x="16" y="34" width="32" height="18" rx="4" fill={body} stroke="#1b1a17" strokeWidth="2" />
      <rect x="24" y="40" width="16" height="7" rx="2" fill="#ffffff55" />
      {/* harness badge: a hint of which CLI this robot runs */}
      {kind === "claude" && <circle cx="32" cy="43" r="3" fill="#1b1a17" />}
      {kind === "codex" && <rect x="29" y="40" width="6" height="6" rx="1" fill="#1b1a17" />}
      {kind === "opencode" && <path d="M29 46 L32 40 L35 46 Z" fill="#1b1a17" />}
      {/* arms */}
      <line
        x1="16"
        y1="38"
        x2="9"
        y2={working ? 30 : 46}
        stroke="#1b1a17"
        strokeWidth="3"
        strokeLinecap="round"
        className="robot-arm robot-arm-l"
      />
      <line
        x1="48"
        y1="38"
        x2="55"
        y2={working ? 30 : 46}
        stroke="#1b1a17"
        strokeWidth="3"
        strokeLinecap="round"
        className="robot-arm robot-arm-r"
      />
      {/* legs */}
      <rect x="22" y="52" width="7" height="7" rx="2" fill="#1b1a17" />
      <rect x="35" y="52" width="7" height="7" rx="2" fill="#1b1a17" />
      {/* status marks */}
      {needs && <circle cx="50" cy="14" r="6" fill="#cf534f" stroke="#fff" strokeWidth="2" className="robot-alarm" />}
      {idle && !needs && (
        <text x="50" y="12" className="robot-zzz" fontSize="12" fill={ACCENT} aria-hidden>
          z
        </text>
      )}
    </svg>
  );
}
