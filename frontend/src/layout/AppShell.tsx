import type { ReactNode } from "react";
import { ConnectionState } from "livekit-client";
import type { User } from "firebase/auth";
import { pageDescriptions, pageLabels } from "../appConstants";
import type { AppPage } from "../appTypes";
import { displayConnectionState } from "../appUtils";
import { SidebarNav } from "./SidebarNav";

type AppShellProps = {
  children: ReactNode;
  connectionState: ConnectionState;
  currentPage: AppPage;
  firebaseUser: User;
  isConnected: boolean;
  isSidebarCollapsed: boolean;
  onToggleSidebar: () => void;
};

export function AppShell({
  children,
  connectionState,
  currentPage,
  firebaseUser,
  isConnected,
  isSidebarCollapsed,
  onToggleSidebar,
}: AppShellProps) {
  return (
    <main
      className={`app-shell ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}
      data-lk-theme="default"
    >
      <SidebarNav
        firebaseUser={firebaseUser}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapsed={onToggleSidebar}
      />
      <section className="workspace">
        <header className="page-header">
          <div className="page-heading">
            <h1>{pageLabels[currentPage]}</h1>
            <p>{pageDescriptions[currentPage]}</p>
          </div>
          {connectionState !== ConnectionState.Disconnected ? (
            <div className="status-block">
              <span className={`status-dot ${isConnected ? "on" : ""}`} />
              <span>Voice {displayConnectionState(connectionState)}</span>
            </div>
          ) : null}
        </header>
        {children}
      </section>
    </main>
  );
}
