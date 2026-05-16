import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Compass } from "@phosphor-icons/react";

import { EmptyState } from "@/components/EmptyState";
import { t } from "@/messages";

export function NotFoundPage(): ReactNode {
  useEffect(() => {
    document.title = t("notFound.title");
  }, []);
  return (
    <EmptyState
      icon={Compass}
      title={t("notFound.title")}
      description={t("notFound.body")}
      action={
        <Link
          to="/"
          className="text-(--color-accent) underline underline-offset-4 hover:text-(--color-accent-strong)"
        >
          {t("notFound.backHome")}
        </Link>
      }
    />
  );
}
