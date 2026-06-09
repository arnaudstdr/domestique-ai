import { NavLink } from "react-router-dom";
import {
  Bike,
  Bot,
  ClipboardList,
  LayoutDashboard,
  LineChart,
  Sunrise,
  type LucideIcon,
} from "lucide-react";

type NavItem = { to: string; label: string; Icon: LucideIcon };

const ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/activites", label: "Activités", Icon: Bike },
  { to: "/matin", label: "Matin", Icon: Sunrise },
  { to: "/plan", label: "Plan", Icon: ClipboardList },
  { to: "/coach", label: "Coach", Icon: Bot },
];

// En consultation coach (lecture seule), on n'expose que les pages de lecture :
// pas de saisie matinale ni de chat coach, et on ajoute les tendances.
const VIEWING_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/activites", label: "Activités", Icon: Bike },
  { to: "/tendances", label: "Tendances", Icon: LineChart },
  { to: "/plan", label: "Plan", Icon: ClipboardList },
];

export default function BottomNav({ viewing = false }: { viewing?: boolean }) {
  const items = viewing ? VIEWING_ITEMS : ITEMS;
  return (
    <nav
      className="fixed bottom-0 inset-x-0 z-[1100] bg-surface/80 backdrop-blur-xl
                 border-t border-white/[0.06] pb-[env(safe-area-inset-bottom)]
                 shadow-[0_-8px_24px_-16px_rgb(0_0_0/0.8)]
                 will-change-transform [transform:translateZ(0)]
                 [-webkit-backface-visibility:hidden]"
    >
      <ul
        className="mx-auto grid max-w-3xl"
        style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}
      >
        {items.map(({ to, label, Icon }) => (
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
