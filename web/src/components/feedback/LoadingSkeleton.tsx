import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export type LoadingSkeletonVariant = "card" | "row" | "block";

export interface LoadingSkeletonProps {
  variant?: LoadingSkeletonVariant;
  count?: number;
  className?: string;
}

function placeholderKeys(count: number, prefix: string): string[] {
  return Array.from({ length: count }, (_, i) => `${prefix}-${i}`);
}

export function LoadingSkeleton({ variant = "card", count = 3, className }: LoadingSkeletonProps) {
  if (variant === "row") {
    const keys = placeholderKeys(count, "row");
    return (
      <div className={cn("flex flex-col gap-2", className)}>
        {keys.map((k) => (
          <Skeleton key={k} className="h-10 w-full rounded" />
        ))}
      </div>
    );
  }

  if (variant === "block") {
    return <Skeleton className={cn("h-32 w-full rounded", className)} />;
  }

  const keys = placeholderKeys(count, "card");
  return (
    <div className={cn("grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3", className)}>
      {keys.map((k) => (
        <div key={k} className="flex flex-col gap-3 rounded border border-border bg-bg p-5">
          <Skeleton className="h-5 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
        </div>
      ))}
    </div>
  );
}
