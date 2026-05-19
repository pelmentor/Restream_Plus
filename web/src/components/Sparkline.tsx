import { useEffect, useRef, type ReactNode } from "react";

import { useReducedMotion } from "@/lib/useReducedMotion";

export interface SparklineSample {
  readonly bitrate: number; // kbps
  readonly healthy: boolean; // false → reconnecting segment
}

export interface SparklineProps {
  readonly samples: readonly SparklineSample[];
  readonly width?: number;
  readonly height?: number;
  readonly ariaLabel: string;
  readonly srSummary?: string;
}

/**
 * Design-system §6.9: 240×48 canvas, DPR-scaled, theme-aware via
 * MutationObserver. Reads resolved CSS custom-property values directly
 * (Phase 7 source-form deviation — tokens.css ships full `hsl(...)`).
 * Reduced-motion: straight lineTo (no bezier smoothing).
 */
export function Sparkline({
  samples,
  width = 240,
  height = 48,
  ariaLabel,
  srSummary,
}: SparklineProps): ReactNode {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reducedMotion = useReducedMotion();
  const tokensRef = useRef<{ accent: string; error: string; bg: string } | null>(null);

  useEffect(() => {
    function readTokens(): void {
      const root = document.documentElement;
      const styles = window.getComputedStyle(root);
      tokensRef.current = {
        accent: styles.getPropertyValue("--color-accent").trim() || "currentColor",
        error: styles.getPropertyValue("--color-error").trim() || "currentColor",
        bg: styles.getPropertyValue("--color-bg-elevated").trim() || "transparent",
      };
      draw();
    }
    readTokens();
    const observer = new MutationObserver(readTokens);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    mediaQuery.addEventListener("change", readTokens);
    return () => {
      observer.disconnect();
      mediaQuery.removeEventListener("change", readTokens);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- draw is stable per render via closure
  }, []);

  useEffect(() => {
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- draw consumes refs/props
  }, [samples, width, height, reducedMotion]);

  function draw(): void {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const tokens = tokensRef.current;
    if (tokens === null) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${String(width)}px`;
    canvas.style.height = `${String(height)}px`;
    const ctx = canvas.getContext("2d");
    if (ctx === null) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    if (samples.length === 0) {
      // Hex Audit UI-F8 (slice 10): paint a visible empty-state placeholder
      // instead of leaving the canvas blank. Pre-slice-10, "no samples yet"
      // and "the chart failed to render" were visually indistinguishable —
      // operator sees a transparent 240×48 box and has no signal whether
      // the dashboard is loading, broken, or just early in a session.
      // Now: a faint dashed horizontal line at mid-height communicates
      // "the chart is mounted and waiting for data" while staying clearly
      // distinct from a real-data line (which is solid).
      ctx.strokeStyle = tokens.bg;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
      ctx.setLineDash([]);
      return;
    }

    const max = Math.max(1, ...samples.map((s) => s.bitrate));
    const min = Math.min(0, ...samples.map((s) => s.bitrate));
    const range = Math.max(1, max - min);
    const stepX = samples.length > 1 ? width / (samples.length - 1) : 0;

    // Faint zero-line / baseline.
    ctx.strokeStyle = tokens.bg;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, height - 1);
    ctx.lineTo(width, height - 1);
    ctx.stroke();

    let currentColor = samples[0]?.healthy === false ? tokens.error : tokens.accent;
    ctx.strokeStyle = currentColor;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "miter";
    ctx.lineCap = "round";
    ctx.beginPath();

    samples.forEach((sample, i) => {
      const x = i * stepX;
      const y = height - ((sample.bitrate - min) / range) * (height - 2) - 1;
      const color = sample.healthy ? tokens.accent : tokens.error;
      if (i === 0) {
        ctx.moveTo(x, y);
        return;
      }
      if (color !== currentColor) {
        ctx.stroke();
        ctx.strokeStyle = color;
        currentColor = color;
        ctx.beginPath();
        const prevSample = samples[i - 1];
        if (prevSample !== undefined) {
          const prevX = (i - 1) * stepX;
          const prevY = height - ((prevSample.bitrate - min) / range) * (height - 2) - 1;
          ctx.moveTo(prevX, prevY);
        }
      }
      if (reducedMotion) {
        ctx.lineTo(x, y);
      } else {
        const prevSample = samples[i - 1];
        if (prevSample === undefined) {
          ctx.lineTo(x, y);
        } else {
          const prevX = (i - 1) * stepX;
          const prevY = height - ((prevSample.bitrate - min) / range) * (height - 2) - 1;
          const midX = (prevX + x) / 2;
          ctx.bezierCurveTo(midX, prevY, midX, y, x, y);
        }
      }
    });
    ctx.stroke();
  }

  return (
    <div>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={ariaLabel}
        className="block"
      />
      {srSummary !== undefined && <span className="sr-only">{srSummary}</span>}
    </div>
  );
}
