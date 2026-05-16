import { useEffect, useRef, useState, type ReactNode } from "react";
import { Copy, DownloadSimple, Pause, Play } from "@phosphor-icons/react";

import { Button } from "./Button";
import { cn } from "@/lib/cn";
import { t } from "@/messages";

export interface LogViewerProps {
  readonly title: string;
  readonly lines: readonly string[];
  readonly maxHeightClass?: string;
  readonly onCopyAll?: () => void;
  readonly onDownload?: () => void;
}

/**
 * Design-system §6.12. Mono, virtualized-less (cap at 200 visible lines)
 * with auto-scroll + Pause-tail toggle.
 *
 * Per phase-8-design-memo §N: 20-row max-height inside the slide-out
 * (max-h-[450px]). Caller can override with `maxHeightClass`.
 */
export function LogViewer({
  title,
  lines,
  maxHeightClass = "max-h-[450px]",
  onCopyAll,
  onDownload,
}: LogViewerProps): ReactNode {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const programmaticRef = useRef(false);
  const [paused, setPaused] = useState(false);
  const [userScrolling, setUserScrolling] = useState(false);

  // Auto-scroll to bottom when new lines arrive AND we're not paused
  // AND the user hasn't scrolled up.
  useEffect(() => {
    if (paused || userScrolling) return;
    const body = bodyRef.current;
    if (body === null) return;
    programmaticRef.current = true;
    body.scrollTop = body.scrollHeight;
    // microtask later, the scroll handler resets the flag
    queueMicrotask(() => {
      programmaticRef.current = false;
    });
  }, [lines, paused, userScrolling]);

  function onScroll(): void {
    if (programmaticRef.current) return;
    const body = bodyRef.current;
    if (body === null) return;
    const distanceFromBottom = body.scrollHeight - body.scrollTop - body.clientHeight;
    setUserScrolling(distanceFromBottom > 8);
  }

  function onResumeTail(): void {
    setUserScrolling(false);
    const body = bodyRef.current;
    if (body !== null) {
      programmaticRef.current = true;
      body.scrollTop = body.scrollHeight;
      queueMicrotask(() => {
        programmaticRef.current = false;
      });
    }
  }

  return (
    <div className="flex flex-col rounded-(--radius-lg) overflow-hidden border border-(--color-border-subtle) bg-(--color-bg-sunken)">
      <div className="flex items-center gap-(--space-3) bg-(--color-bg-elevated) px-(--space-3) py-(--space-2) border-b border-(--color-border-subtle)">
        <h4 className="text-(length:--text-sm) font-medium text-(--color-fg-strong)">
          {title}
        </h4>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setPaused((p) => !p)}
          aria-pressed={paused}
        >
          {paused ? (
            <>
              <Play className="h-4 w-4" weight="regular" aria-hidden="true" />
              {t("logViewer.resume")}
            </>
          ) : (
            <>
              <Pause className="h-4 w-4" weight="regular" aria-hidden="true" />
              {t("logViewer.pause")}
            </>
          )}
        </Button>
        {onCopyAll !== undefined && (
          <Button variant="ghost" size="sm" onClick={onCopyAll}>
            <Copy className="h-4 w-4" weight="regular" aria-hidden="true" />
            {t("logViewer.copyAll")}
          </Button>
        )}
        {onDownload !== undefined && (
          <Button variant="ghost" size="sm" onClick={onDownload}>
            <DownloadSimple className="h-4 w-4" weight="regular" aria-hidden="true" />
            {t("logViewer.download")}
          </Button>
        )}
      </div>
      <div className="relative">
        <div
          ref={bodyRef}
          onScroll={onScroll}
          className={cn(
            "font-(family-name:--font-mono) text-(length:--text-sm) leading-[1.5]",
            "text-(--color-fg-default) bg-(--color-bg-sunken) px-(--space-3) py-(--space-2)",
            "overflow-auto",
            maxHeightClass,
          )}
        >
          {lines.length === 0 ? (
            <div className="text-(--color-fg-muted) py-(--space-4) text-center">
              {t("logViewer.empty")}
            </div>
          ) : (
            lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap break-words">
                {line}
              </div>
            ))
          )}
        </div>
        {userScrolling && (
          <button
            type="button"
            onClick={onResumeTail}
            className="absolute bottom-(--space-3) right-(--space-3) rounded-full bg-(--color-accent) text-white px-(--space-3) py-(--space-1) text-(length:--text-xs) shadow-(--shadow-md)"
          >
            {t("logViewer.resumeTail")}
          </button>
        )}
      </div>
    </div>
  );
}
