import { NavLink } from "react-router-dom";
import {
  Bike,
  Bot,
  ClipboardList,
  LayoutDashboard,
  Sunrise,
  type LucideIcon,
} from "lucide-react";

const ITEMS: { to: string; label: string; Icon: LucideIcon }[] = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/activites", label: "Activités", Icon: Bike },
  { to: "/matin", label: "Matin", Icon: Sunrise },
  { to: "/plan", label: "Plan", Icon: ClipboardList },
  { to: "/coach", label: "Coach", Icon: Bot },
];

export default function BottomNav() {
  return (
    <nav
      className="fixed bottom-0 inset-x-0 z-[1100] bg-card/95 backdrop-blur
                 border-t border-white/5 pb-[env(safe-area-inset-bottom)]
                 will-change-transform [transform:translateZ(0)]
                 [-webkit-backface-visibility:hidden]"
    >
      <ul className="grid grid-cols-5">
        {ITEMS.map(({ to, label, Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex flex-col items-center gap-1 py-2.5 text-xs ${
                  isActive ? "text-accent" : "text-muted"
                }`
              }
            >
              <Icon className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
