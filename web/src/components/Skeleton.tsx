import type { CSSProperties, ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface SkeletonProps {
  className?: string;
  style?: CSSProperties;
  /** Width and height shorthand. Accepts any CSS length. */
  width?: string;
  height?: string;
  rounded?: "sm" | "md" | "lg" | "full";
}

const radii = { sm: "rounded-sm", md: "rounded-md", lg: "rounded-lg", full: "rounded-full" } as const;

function SkeletonBase({ className, style, width, height, rounded = "md" }: SkeletonProps): ReactNode {
  const styleProp: CSSProperties = {
    ...(width !== undefined && { width }),
    ...(height !== undefined && { height }),
    ...style,
  };
  return (
    <div
      aria-hidden="true"
      role="presentation"
      className={cn("skeleton", radii[rounded], className)}
      style={styleProp}
    />
  );
}

function Tile({ className }: { className?: string }): ReactNode {
  return <SkeletonBase className={cn("h-32 w-full", className)} rounded="lg" />;
}

function Row({ className }: { className?: string }): ReactNode {
  return <SkeletonBase className={cn("h-4 w-full", className)} />;
}

function Pill({ className }: { className?: string }): ReactNode {
  return <SkeletonBase className={cn("h-6 w-20", className)} rounded="full" />;
}

export const Skeleton = Object.assign(SkeletonBase, { Tile, Row, Pill });
