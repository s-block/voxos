import { ChevronLeft, ChevronRight } from "lucide-react";
import { NavLink } from "react-router";
import { appPages } from "../appConstants";
import { VoxosMark } from "../components/VoxosMark";
import { hasNestedPageRoutes, pagePath, pagePaths } from "../routing";
import type { User } from "firebase/auth";

type SidebarNavProps = {
  firebaseUser: User;
  isCollapsed: boolean;
  onToggleCollapsed: () => void;
};

export function SidebarNav({
  firebaseUser,
  isCollapsed,
  onToggleCollapsed,
}: SidebarNavProps) {
  const accountPage = appPages.find((page) => page.page === "account");
  const AccountIcon = accountPage?.icon;
  const displayName =
    firebaseUser.displayName ?? firebaseUser.email ?? firebaseUser.uid;

  return (
    <aside
      className={`sidebar-nav ${isCollapsed ? "collapsed" : ""}`}
      aria-label="Main navigation"
    >
      <div className="sidebar-header">
        <NavLink
          className="brand-button"
          title="VoxOS"
          to={pagePaths.chat}
        >
          <VoxosMark className="brand-mark" size={32} />
          <span>VoxOS</span>
        </NavLink>
        <button
          className="icon-button"
          onClick={onToggleCollapsed}
          title={isCollapsed ? "Expand navigation" : "Collapse navigation"}
          type="button"
        >
          {isCollapsed ? (
            <ChevronRight aria-hidden="true" size={18} />
          ) : (
            <ChevronLeft aria-hidden="true" size={18} />
          )}
        </button>
      </div>

      <nav className="nav-list">
        {appPages
          .filter((page) => page.page !== "account")
          .map((page) => {
            const Icon = page.icon;
            return (
              <NavLink
                className={({ isActive }) => (isActive ? "selected" : undefined)}
                end={!hasNestedPageRoutes(page.page)}
                key={page.page}
                title={page.label}
                to={pagePath(page.page)}
              >
                <Icon aria-hidden="true" size={18} />
                <span>{page.label}</span>
              </NavLink>
            );
          })}
      </nav>

      {AccountIcon ? (
        <NavLink
          className={({ isActive }) =>
            `sidebar-account ${isActive ? "selected" : ""}`
          }
          end
          title={displayName}
          to={pagePaths.account}
        >
          <AccountIcon aria-hidden="true" size={18} />
          <span>
            <strong>{displayName}</strong>
            <small>Account</small>
          </span>
        </NavLink>
      ) : null}
    </aside>
  );
}
