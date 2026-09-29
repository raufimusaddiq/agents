import { useLayoutEffect, useRef, useState, useEffect } from "react";

/** FLIP layout animation: 200-300ms, none under prefers-reduced-motion. */
export function useFlip(deps: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const positions = useRef<Map<string, DOMRect>>(new Map());

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const nodes = Array.from(el.querySelectorAll<HTMLElement>("[data-flip-id]"));
    const next = new Map<string, DOMRect>();
    for (const node of nodes) {
      const id = node.dataset.flipId!;
      const rect = node.getBoundingClientRect();
      next.set(id, rect);
      const prev = positions.current.get(id);
      if (prev && !reduce) {
        const dx = prev.left - rect.left;
        const dy = prev.top - rect.top;
        if (dx || dy) {
          node.animate(
            [
              { transform: `translate(${dx}px, ${dy}px)` },
              { transform: "translate(0, 0)" },
            ],
            { duration: 250, easing: "cubic-bezier(.2,.8,.2,1)" },
          );
        }
      }
    }
    positions.current = next;
  }, [deps]);

  return ref;
}

/** Count-up seconds since a timestamp, for "time in column". */
export function useNow(intervalMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
