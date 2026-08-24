"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import AppHeader from "@/components/AppHeader";
import AppRail from "@/components/AppRail";
import PageEnter from "@/components/motion/PageEnter";
import { checkApiHealth } from "@/lib/api";
import type { AuthUser } from "@/types";

export default function DeskChrome({
  children,
  caseId,
  search,
}: {
  children: ReactNode;
  caseId?: number | null;
  search?: ReactNode;
}) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);

  useEffect(() => {
    void checkApiHealth().then(setApiReachable);
  }, []);

  const onUser = useCallback((next: AuthUser | null) => setUser(next), []);

  return (
    <PageEnter>
      <div className="relative flex min-h-screen flex-col pb-16">
        <AppHeader
          apiReachable={apiReachable}
          user={user}
          onUserChange={onUser}
          caseId={caseId}
          search={search}
        />
        <AppRail />
        <main className="mx-auto w-full max-w-[1600px] px-3 py-5 sm:px-4">
          {children}
        </main>
      </div>
    </PageEnter>
  );
}
