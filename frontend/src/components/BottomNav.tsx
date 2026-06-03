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
      className="fixed bottom-0 inset-x-0 z-[1100] bg-surface/80 backdrop-blur-xl
                 border-t border-white/[0.06] pb-[env(safe-area-inset-bottom)]
                 shadow-[0_-8px_24px_-16px_rgb(0_0_0/0.8)]
                 will-change-transform [transform:translateZ(0)]
                 [-webkit-backface-visibility:hidden]"
    >
      <ul className="mx-auto grid max-w-3xl grid-cols-5">
        {ITEMS.map(({ to, label, Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `group relative flex flex-col items-center gap-1 py-2.5
                 text-[11px] font-medium tracking-tight transition-colors ${
                   isActive ? "text-accent" : "text-muted hover:text-gray-300"
                 }`
              }
            >
              {({ isActive }) => (
                <>
                  {/* Indicateur d'onglet actif : trait lumineux en haut. */}
                  <span
                    className={`absolute top-0 h-[3px] w-7 rounded-full bg-accent
                                transition-all duration-300 ${
                                  isActive
                                    ? "opacity-100 shadow-[0_0_12px_2px_rgb(var(--accent)/0.5)]"
                                    : "opacity-0"
                                }`}
                    aria-hidden="true"
                  />
                  <Icon
                    className="h-[22px] w-[22px] transition-transform duration-200 group-active:scale-90"
                    strokeWidth={isActive ? 2.25 : 1.75}
                    aria-hidden="true"
                  />
                  <span>{label}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
